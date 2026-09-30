"""Milvus 向量库封装。

设计原则
--------
1. **惰性导入**：``pymilvus`` 只在真正建连时导入，保证不装 Milvus 依赖也能
   导入模块、跑单元测试；
2. **最小冗余**：Milvus 只存 ``embedding`` ＋ 可过滤标量（状态/条线/时间戳），
   正文回查 MySQL，落实「结构化数据与向量数据协同查询」；
3. **过滤下推**：时效性（生效/失效）与业务条线过滤在 Milvus 侧完成，
   先把过时条文挡在候选集之外，再做重排与生成。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.core.config import Settings, get_settings
from app.core.constants import BusinessLine, DocStatus, DocType
from app.core.errors import VectorStoreError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# Milvus 标量字段长度上限（字符）
MAX_LEN_STATUS = 16
MAX_LEN_LINE = 32
MAX_LEN_CLAUSE_NO = 64

VECTOR_FIELD = "embedding"
PRIMARY_FIELD = "pk"
OUTPUT_FIELDS: Sequence[str] = (
    "pk",
    "document_id",
    "chunk_index",
    "status",
    "business_line",
    "doc_type",
    "effective_ts",
    "expiry_ts",
    "clause_no",
)


@dataclass
class ClauseVector:
    """写入 Milvus 的一条条文向量记录。"""

    pk: int
    document_id: int
    embedding: List[float]
    chunk_index: int = 0
    status: str = DocStatus.UNKNOWN.value
    business_line: str = BusinessLine.GENERAL.value
    doc_type: str = DocType.NORMATIVE.value
    effective_ts: int = 0
    expiry_ts: int = 0
    clause_no: str = ""

    def to_row(self) -> Dict[str, Any]:
        """转换为 Milvus 写入所需的字典。"""
        return {
            PRIMARY_FIELD: int(self.pk),
            "document_id": int(self.document_id),
            VECTOR_FIELD: list(self.embedding),
            "chunk_index": int(self.chunk_index),
            "status": (self.status or DocStatus.UNKNOWN.value)[:MAX_LEN_STATUS],
            "business_line": (self.business_line or BusinessLine.GENERAL.value)[:MAX_LEN_LINE],
            "doc_type": (self.doc_type or DocType.NORMATIVE.value)[:MAX_LEN_LINE],
            "effective_ts": int(self.effective_ts or 0),
            "expiry_ts": int(self.expiry_ts or 0),
            "clause_no": (self.clause_no or "")[:MAX_LEN_CLAUSE_NO],
        }


@dataclass
class VectorHit:
    """一次向量检索的命中结果（不含正文，正文由 MySQL 回查补齐）。"""

    pk: int
    document_id: int
    score: float
    fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> int:
        """条文在 MySQL ``kb_chunk`` 中的主键。"""
        return int(self.pk)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "score": round(float(self.score), 6),
            "clause_no": self.fields.get("clause_no", ""),
            "status": self.fields.get("status", ""),
            "business_line": self.fields.get("business_line", ""),
            "doc_type": self.fields.get("doc_type", ""),
        }


def to_timestamp(value: Any) -> int:
    """把 ``date`` / ``datetime`` / 时间戳统一为「秒级整数」，0 表示未设置。

    金融制度常出现「自发布之日起施行」这类无明确日期的情况，
    统一用 0 表示「不限」，便于 Milvus 侧做区间比较。
    """
    if value is None:
        return 0
    if isinstance(value, bool):  # 避免 True 被当成 1
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if hasattr(value, "timetuple"):
        try:
            return int(time.mktime(value.timetuple()))
        except (OverflowError, ValueError, OSError):  # pragma: no cover
            return 0
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                return int(time.mktime(time.strptime(text, fmt)))
            except ValueError:
                continue
    return 0


def _quote(value: str) -> str:
    """转义 Milvus 表达式中的字符串字面量。"""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_filter_expr(
    *,
    statuses: Optional[Iterable[str]] = None,
    business_lines: Optional[Iterable[str]] = None,
    doc_types: Optional[Iterable[str]] = None,
    document_ids: Optional[Iterable[int]] = None,
    as_of: Optional[int] = None,
    only_valid: bool = False,
    effective_only: bool = False,
    exclude_expired: bool = True,
) -> str:
    """构造 Milvus 标量过滤表达式。

    这是「时效性管理」差异化要求的实现核心：

    * ``only_valid=True`` 时仅保留 :meth:`DocStatus.valid_values` 中的状态；
    * ``as_of`` 给定时间戳时，要求 ``effective_ts <= as_of`` 且条文未过期；
    * ``exclude_expired=True`` 时，``expiry_ts == 0``（长期有效）或 ``expiry_ts > as_of``
      才保留，未指定 ``as_of`` 则用当前时间。

    :returns: Milvus 布尔表达式字符串；无任何条件时返回空串。
    """
    clauses: List[str] = []

    status_list = [s for s in (statuses or []) if s]
    if only_valid:
        status_list = list(DocStatus.valid_values())
    if effective_only:
        status_list = [DocStatus.EFFECTIVE.value]
    if status_list:
        rendered = ", ".join(_quote(s) for s in dict.fromkeys(status_list))
        clauses.append(f"status in [{rendered}]")

    lines = [b for b in (business_lines or []) if b]
    if lines:
        rendered = ", ".join(_quote(b) for b in dict.fromkeys(lines))
        clauses.append(f"business_line in [{rendered}]")

    types = [t for t in (doc_types or []) if t]
    if types:
        rendered = ", ".join(_quote(t) for t in dict.fromkeys(types))
        clauses.append(f"doc_type in [{rendered}]")

    ids = [int(i) for i in (document_ids or [])]
    if ids:
        rendered = ", ".join(str(i) for i in dict.fromkeys(ids))
        clauses.append(f"document_id in [{rendered}]")

    moment = int(as_of) if as_of else int(time.time())
    if effective_only or as_of is not None or only_valid:
        # 已生效：effective_ts 为 0（未标注，视为不限）或早于查询时刻
        clauses.append(f"(effective_ts == 0 or effective_ts <= {moment})")

    if exclude_expired:
        clauses.append(f"(expiry_ts == 0 or expiry_ts >= {moment})")

    return " and ".join(clauses)


class MilvusVectorStore:
    """Milvus 向量库客户端封装（线程安全地复用单个连接）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self.collection: str = self.settings.milvus_collection
        self.dim: int = self.settings.qwen_embedding_dim
        self._client: Any = None

    # ------------------------------------------------------------- 连接
    @property
    def uri(self) -> str:
        """Milvus 服务地址。"""
        return f"http://{self.settings.milvus_host}:{self.settings.milvus_port}"

    @property
    def token(self) -> str:
        """认证 token，未配置用户时为空串（Milvus 默认免认证）。"""
        user = self.settings.milvus_user
        if not user:
            return ""
        return f"{user}:{self.settings.milvus_password}"

    @property
    def client(self) -> Any:
        """惰性创建 MilvusClient。"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Any:
        """导入 pymilvus 并建立连接；失败时抛出可读错误。"""
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - 依赖缺失分支
            raise VectorStoreError(
                "未安装 pymilvus，请执行 pip install pymilvus 后重试"
            ) from exc
        try:
            kwargs: Dict[str, Any] = {"uri": self.uri}
            if self.token:
                kwargs["token"] = self.token
            client = MilvusClient(**kwargs)
            logger.info("已连接 Milvus：%s collection=%s", self.uri, self.collection)
            return client
        except Exception as exc:  # pragma: no cover - 需要真实服务
            raise VectorStoreError(f"连接 Milvus 失败（{self.uri}）：{exc}") from exc

    def close(self) -> None:
        """关闭连接。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                logger.warning("关闭 Milvus 连接异常", exc_info=True)
            self._client = None

    # --------------------------------------------------------- 集合管理
    def has_collection(self) -> bool:
        """判断集合是否已存在。"""
        return bool(self.client.has_collection(self.collection))

    def build_schema(self) -> Any:
        """构造集合 Schema。"""
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(PRIMARY_FIELD, DataType.INT64, is_primary=True)
        schema.add_field("document_id", DataType.INT64)
        schema.add_field(VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("status", DataType.VARCHAR, max_length=MAX_LEN_STATUS)
        schema.add_field("business_line", DataType.VARCHAR, max_length=MAX_LEN_LINE)
        schema.add_field("doc_type", DataType.VARCHAR, max_length=MAX_LEN_LINE)
        schema.add_field("effective_ts", DataType.INT64)
        schema.add_field("expiry_ts", DataType.INT64)
        schema.add_field("clause_no", DataType.VARCHAR, max_length=MAX_LEN_CLAUSE_NO)
        return schema

    def build_index_params(self) -> Any:
        """构造索引参数：向量索引 + 标量倒排索引。"""
        from pymilvus import MilvusClient

        index_params = MilvusClient.prepare_index_params()
        vector_params: Dict[str, Any] = {"metric_type": self.settings.milvus_metric_type}
        if self.settings.milvus_index_type == "HNSW":
            vector_params["params"] = {
                "M": self.settings.milvus_hnsw_m,
                "efConstruction": self.settings.milvus_hnsw_ef_construction,
            }
        elif self.settings.milvus_index_type == "IVF_FLAT":
            vector_params["params"] = {"nlist": 1024}
        else:
            vector_params["params"] = {"nlist": 1024}

        index_params.add_index(
            field_name=VECTOR_FIELD,
            index_name=f"{VECTOR_FIELD}_idx",
            index_type=self.settings.milvus_index_type,
            **vector_params,
        )

        # 标量索引为「性能优化」而非「功能必需」，不同 Milvus 版本支持度不同，
        # 因此逐个尽力添加，失败只告警不影响建集合。
        for field_name, index_type in (
            ("document_id", "STL_SORT"),
            ("status", "INVERTED"),
            ("business_line", "INVERTED"),
            ("doc_type", "INVERTED"),
            ("effective_ts", "STL_SORT"),
            ("expiry_ts", "STL_SORT"),
        ):
            try:
                index_params.add_index(
                    field_name=field_name,
                    index_name=f"{field_name}_idx",
                    index_type=index_type,
                )
            except Exception as exc:  # pragma: no cover - 版本差异分支
                logger.debug("标量索引 %s 创建跳过：%s", field_name, exc)
        return index_params

    def ensure_collection(self, drop: bool = False) -> Dict[str, Any]:
        """确保集合存在（不存在则创建，维度不匹配则报错）。

        :param drop: 为 True 时先删除重建（重建后需重新入库）。
        """
        if drop and self.has_collection():
            self.client.drop_collection(self.collection)
            logger.warning("已删除集合 %s", self.collection)

        if self.has_collection():
            desc = self.describe()
            actual_dim = desc.get("dim")
            if actual_dim and actual_dim != self.dim:
                raise VectorStoreError(
                    f"集合 {self.collection} 向量维度为 {actual_dim}，"
                    f"与配置 QWEN_EMBEDDING_DIM={self.dim} 不一致。"
                    "请统一维度后删除集合重建，或修改配置。"
                )
            return desc

        self.client.create_collection(
            collection_name=self.collection,
            schema=self.build_schema(),
            index_params=self.build_index_params(),
            consistency_level=self.settings.milvus_consistency_level,
        )
        logger.info(
            "集合 %s 创建完成：dim=%d index=%s metric=%s",
            self.collection,
            self.dim,
            self.settings.milvus_index_type,
            self.settings.milvus_metric_type,
        )
        return self.describe()

    def describe(self) -> Dict[str, Any]:
        """返回集合结构摘要，用于自检接口。"""
        info: Dict[str, Any] = {
            "collection": self.collection,
            "exists": False,
            "dim": None,
            "index_type": self.settings.milvus_index_type,
            "metric_type": self.settings.milvus_metric_type,
            "fields": [],
        }
        try:
            if not self.has_collection():
                return info
            info["exists"] = True
            desc = self.client.describe_collection(self.collection)
            for f in desc.get("fields", []) or []:
                item = {
                    "name": f.get("name"),
                    "type": str(f.get("type")),
                    "params": f.get("params", {}) or {},
                }
                info["fields"].append(item)
                if item["name"] == VECTOR_FIELD:
                    info["dim"] = item["params"].get("dim")
        except Exception as exc:  # pragma: no cover - 需要真实服务
            logger.warning("获取集合结构失败：%s", exc)
            info["error"] = str(exc)
        return info

    # ------------------------------------------------------------- 写入
    def upsert_clauses(self, rows: Sequence[ClauseVector], batch_size: int = 500) -> int:
        """批量写入/更新条文向量，返回写入条数。

        使用 ``upsert`` 语义：同一 ``pk`` 已存在时覆盖，
        因此文档重新解析后可直接覆盖旧向量，无需先删后插。
        """
        if not rows:
            return 0
        total = 0
        payload = [row.to_row() for row in rows]
        for start in range(0, len(payload), batch_size):
            batch = payload[start:start + batch_size]
            try:
                self.client.upsert(collection_name=self.collection, data=batch)
                total += len(batch)
            except Exception as exc:  # pragma: no cover - 需要真实服务
                raise VectorStoreError(
                    f"写入 Milvus 失败（第 {start} 批，共 {len(batch)} 条）：{exc}"
                ) from exc
        self.flush()
        logger.info("向 %s 写入 %d 条向量", self.collection, total)
        return total

    def flush(self) -> None:
        """刷盘，保证写入立即可检索。"""
        try:
            self.client.flush(self.collection)
        except Exception as exc:  # pragma: no cover - 需要真实服务
            logger.debug("flush 失败（忽略）：%s", exc)

    def delete_by_pks(self, pks: Sequence[int]) -> int:
        """按主键删除向量，返回删除条数。"""
        ids = [int(p) for p in pks]
        if not ids:
            return 0
        try:
            self.client.delete(collection_name=self.collection, ids=ids)
            self.flush()
            logger.info("从 %s 删除 %d 条向量", self.collection, len(ids))
            return len(ids)
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError(f"删除 Milvus 向量失败：{exc}") from exc

    def delete_by_document(self, document_id: int) -> int:
        """删除某文档的全部向量（文档下线/重新入库前调用）。"""
        try:
            self.client.delete(
                collection_name=self.collection,
                filter=f"document_id == {int(document_id)}",
            )
            self.flush()
            logger.info("删除文档 %s 的全部向量", document_id)
            return 1
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError(f"按文档删除向量失败：{exc}") from exc

    # ------------------------------------------------------------- 检索
    def search(
        self,
        vector: Sequence[float],
        top_k: int = 5,
        *,
        expr: str = "",
        output_fields: Optional[Sequence[str]] = None,
        ef_search: Optional[int] = None,
    ) -> List[VectorHit]:
        """向量相似度检索。

        :param vector: 查询向量。
        :param top_k: 返回条数。
        :param expr: Milvus 标量过滤表达式，见 :func:`build_filter_expr`。
        :param output_fields: 需要随命中返回的标量字段。
        """
        if not vector:
            return []
        if len(vector) != self.dim:
            raise VectorStoreError(
                f"查询向量维度 {len(vector)} 与集合维度 {self.dim} 不一致"
            )
        search_params: Dict[str, Any] = {
            "metric_type": self.settings.milvus_metric_type,
        }
        if self.settings.milvus_index_type == "HNSW":
            search_params["params"] = {
                "ef": int(ef_search or self.settings.milvus_hnsw_ef_search)
            }

        kwargs: Dict[str, Any] = {
            "collection_name": self.collection,
            "data": [list(vector)],
            "anns_field": VECTOR_FIELD,
            "search_params": search_params,
            "limit": int(top_k),
            "output_fields": list(output_fields or OUTPUT_FIELDS),
        }
        if expr:
            kwargs["filter"] = expr
        try:
            raw = self.client.search(**kwargs)
        except Exception as exc:  # pragma: no cover - 需要真实服务
            raise VectorStoreError(f"Milvus 检索失败：{exc}") from exc
        return self._parse_hits(raw, self.settings.milvus_metric_type)

    @staticmethod
    def _parse_hits(raw: Any, metric_type: str = "COSINE") -> List[VectorHit]:
        """把 pymilvus 的原始返回解析为 :class:`VectorHit` 列表。

        兼容两种返回形态：pymilvus 2.4 的 ``dict`` 结果与旧版对象结果。
        对 ``L2`` 距离做了 ``1/(1+d)`` 归一化，使分数越大越相似。
        """
        hits: List[VectorHit] = []
        if not raw:
            return hits
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if first is None:
            return hits

        for item in first:
            if isinstance(item, dict):
                pk = item.get("id", item.get("pk"))
                distance = float(item.get("distance", item.get("score", 0.0)))
                entity = item.get("entity", {}) or {}
            else:  # pragma: no cover - 旧版对象形态
                pk = getattr(item, "id", None)
                distance = float(getattr(item, "distance", 0.0))
                entity = getattr(item, "entity", None)
                entity = getattr(entity, "fields", entity) if entity is not None else {}
                if not isinstance(entity, dict):
                    entity = dict(entity) if hasattr(entity, "__iter__") else {}

            if pk is None:
                continue
            score = 1.0 / (1.0 + distance) if metric_type == "L2" else distance
            doc_id = entity.get("document_id", 0)
            hits.append(
                VectorHit(
                    pk=int(pk),
                    document_id=int(doc_id or 0),
                    score=float(score),
                    fields=dict(entity),
                )
            )
        return hits

    def count(self, expr: str = "") -> int:
        """统计集合内实体数量（可带过滤条件）。"""
        try:
            result = self.client.query(
                collection_name=self.collection,
                filter=expr or "",
                output_fields=["count(*)"],
            )
        except Exception as exc:  # pragma: no cover - 需要真实服务
            logger.warning("统计向量数量失败：%s", exc)
            return 0
        if isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                return int(row.get("count(*)", row.get("count", 0)) or 0)
        return 0

    # ------------------------------------------------------------- 健康
    def health(self) -> Dict[str, Any]:
        """健康检查：连通性、集合状态、向量总量。"""
        info: Dict[str, Any] = {
            "connected": False,
            "uri": self.uri,
            "collection": self.collection,
            "dim": self.dim,
            "vector_count": 0,
        }
        try:
            self.client  # 触发连接
            info["connected"] = True
            info["collection_exists"] = self.has_collection()
            if info["collection_exists"]:
                info["vector_count"] = self.count()
        except Exception as exc:
            info["error"] = str(exc)
            logger.warning("Milvus 健康检查失败：%s", exc)
        return info


_store: Optional[MilvusVectorStore] = None


def get_vector_store(settings: Optional[Settings] = None, refresh: bool = False) -> MilvusVectorStore:
    """获取全局向量库实例（单例）。"""
    global _store
    if _store is None or refresh:
        if _store is not None:
            _store.close()
        _store = MilvusVectorStore(settings)
    return _store


def reset_vector_store() -> None:
    """释放全局向量库实例（测试用）。"""
    global _store
    if _store is not None:
        _store.close()
    _store = None


__all__ = [
    "ClauseVector",
    "VectorHit",
    "MilvusVectorStore",
    "build_filter_expr",
    "to_timestamp",
    "get_vector_store",
    "reset_vector_store",
    "OUTPUT_FIELDS",
    "VECTOR_FIELD",
    "PRIMARY_FIELD",
]
