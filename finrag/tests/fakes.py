"""测试替身（Test Doubles）。

生产链路严格为「Milvus + 通义千问」，因此这里**不修改任何业务代码**，
而是通过依赖注入替换三个外部依赖：

* :class:`FakeVectorStore` —— 内存向量库，余弦相似度检索，语义与 Milvus 封装一致；
* :class:`FakeEmbedder`    —— 确定性哈希向量（词袋哈希 + L2 归一化），
  同词越多余弦越大，使检索逻辑的断言具备真实意义；
* :class:`FakeChatClient`  —— 包装 LangChain 官方 ``GenericFakeChatModel``，
  仍然走真正的 LangChain 链（Prompt | ChatModel | Parser），只是模型是假的。
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Any, Dict, List, Optional, Sequence

from app.core.constants import BusinessLine, DocStatus, DocType
from app.db.milvus_store import ClauseVector, VectorHit
from app.retrieval.tokenizer import tokenize

# ---------------------------------------------------------------------------
#  向量库替身
# ---------------------------------------------------------------------------


class FakeVectorStore:
    """内存版 Milvus 封装。

    行为对齐 :class:`app.db.milvus_store.MilvusVectorStore`：
    * 主键 = MySQL ``kb_chunk.id``；
    * 支持 ``status/business_line/doc_type/document_id/effective_ts/expiry_ts`` 过滤；
    * ``search`` 按余弦相似度降序返回 :class:`VectorHit`。
    """

    def __init__(self, dim: int = 64, metric_type: str = "COSINE") -> None:
        self.dim = dim
        self.metric_type = metric_type
        self.collection = "finrag_clause_fake"
        self.rows: Dict[int, ClauseVector] = {}
        self.ensure_called = 0
        self.deleted_documents: List[int] = []
        self.fail_on_upsert = False

    # ------------------------------------------------------------- 元信息
    @property
    def uri(self) -> str:
        return "fake://milvus"

    def close(self) -> None:  # pragma: no cover - 无资源可释放
        return None

    # ------------------------------------------------------------- 集合
    def ensure_collection(self, drop: bool = False) -> Dict[str, Any]:
        """模拟建集合。"""
        self.ensure_called += 1
        if drop:
            self.rows.clear()
        return self.describe()

    def describe(self) -> Dict[str, Any]:
        """集合结构摘要。"""
        return {
            "collection": self.collection,
            "exists": True,
            "dim": self.dim,
            "index_type": "HNSW",
            "metric_type": self.metric_type,
            "fields": [
                {"name": "pk", "type": "INT64", "params": {}},
                {"name": "embedding", "type": "FLOAT_VECTOR", "params": {"dim": self.dim}},
            ],
        }

    def health(self) -> Dict[str, Any]:
        """健康状态。"""
        return {
            "connected": True,
            "uri": self.uri,
            "collection": self.collection,
            "dim": self.dim,
            "collection_exists": True,
            "vector_count": len(self.rows),
        }

    # ------------------------------------------------------------- 写入
    def upsert_clauses(self, rows: Sequence[ClauseVector], batch_size: int = 500) -> int:
        """写入/覆盖向量。"""
        if self.fail_on_upsert:
            from app.core.errors import VectorStoreError

            raise VectorStoreError("模拟向量写入失败")
        for row in rows:
            self.rows[int(row.pk)] = row
        return len(rows)

    def flush(self) -> None:
        """空实现（内存无需刷盘）。"""
        return None

    def delete_by_pks(self, pks: Sequence[int]) -> int:
        """按主键删除。"""
        count = 0
        for pk in pks:
            if self.rows.pop(int(pk), None) is not None:
                count += 1
        return count

    def delete_by_document(self, document_id: int) -> int:
        """按文档删除。"""
        self.deleted_documents.append(int(document_id))
        targets = [
            pk for pk, row in self.rows.items() if int(row.document_id) == int(document_id)
        ]
        return self.delete_by_pks(targets)

    # ------------------------------------------------------------- 检索
    @staticmethod
    def _match_filter(row: ClauseVector, expr: str) -> bool:
        """极简 Milvus 表达式求值：支持本系统生成的 ``in`` / ``==`` / ``<=`` / ``>=`` 组合。"""
        if not expr:
            return True
        conditions = [part.strip() for part in re.split(r"\s+and\s+", expr) if part.strip()]
        for condition in conditions:
            if not FakeVectorStore._eval_condition(row, condition):
                return False
        return True

    @staticmethod
    def _eval_condition(row: ClauseVector, condition: str) -> bool:
        """求值单个条件（支持括号内的 or 组合）。"""
        text = condition.strip()
        if text.startswith("(") and text.endswith(")"):
            inner = text[1:-1]
            for part in re.split(r"\s+or\s+", inner):
                if FakeVectorStore._eval_simple(row, part.strip()):
                    return True
            return False
        return FakeVectorStore._eval_simple(row, text)

    @staticmethod
    def _eval_simple(row: ClauseVector, condition: str) -> bool:
        """求值原子条件。"""
        match = re.match(r"(\w+)\s+in\s+\[(.*)\]$", condition)
        if match:
            field, raw = match.group(1), match.group(2)
            values = [v.strip().strip('"').strip("'") for v in raw.split(",") if v.strip()]
            actual = getattr(row, field, None)
            if field == "document_id":
                return str(actual) in values
            return str(actual) in values

        match = re.match(r"(\w+)\s*(==|<=|>=|<|>)\s*(-?\d+)$", condition)
        if match:
            field, operator, raw = match.group(1), match.group(2), int(match.group(3))
            actual = getattr(row, field, None)
            if actual is None:
                return False
            actual_int = int(actual)
            if operator == "==":
                return actual_int == raw
            if operator == "<=":
                return actual_int <= raw
            if operator == ">=":
                return actual_int >= raw
            if operator == "<":
                return actual_int < raw
            return actual_int > raw
        return True

    def search(
        self,
        vector: Sequence[float],
        top_k: int = 5,
        *,
        expr: str = "",
        output_fields: Optional[Sequence[str]] = None,
        ef_search: Optional[int] = None,
    ) -> List[VectorHit]:
        """余弦相似度检索。"""
        if not vector:
            return []
        scored: List[VectorHit] = []
        for pk, row in self.rows.items():
            if not self._match_filter(row, expr):
                continue
            score = _cosine(vector, row.embedding)
            scored.append(
                VectorHit(
                    pk=pk,
                    document_id=int(row.document_id),
                    score=score,
                    fields={
                        "pk": pk,
                        "document_id": row.document_id,
                        "chunk_index": row.chunk_index,
                        "status": row.status,
                        "business_line": row.business_line,
                        "doc_type": row.doc_type,
                        "effective_ts": row.effective_ts,
                        "expiry_ts": row.expiry_ts,
                        "clause_no": row.clause_no,
                    },
                )
            )
        scored.sort(key=lambda hit: (-hit.score, hit.pk))
        return scored[: int(top_k)]

    def count(self, expr: str = "") -> int:
        """统计数量。"""
        return sum(1 for row in self.rows.values() if self._match_filter(row, expr))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度；任一为零向量时返回 0。"""
    if len(left) != len(right):
        size = min(len(left), len(right))
        left, right = left[:size], right[:size]
    dot = sum(a * b for a, b in zip(left, right))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return dot / (norm_l * norm_r)


# ---------------------------------------------------------------------------
#  Embedding 替身
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """确定性哈希向量器。

    用「词袋哈希」把文本映射到固定维度并做 L2 归一化，
    因此共享词汇越多的文本余弦相似度越高——足以驱动真实的检索排序断言。
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.embedded_batches: List[List[str]] = []

    def status(self) -> Dict[str, Any]:
        """接入状态。"""
        return {
            "provider": "fake",
            "model": "fake-embedding",
            "dimension": self.dim,
            "configured": True,
        }

    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self.dim

    def _vector(self, text: str) -> List[float]:
        """词袋哈希 -> 定长归一化向量。"""
        vector = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.dim
            sign = 1.0 if int(digest[8:9], 16) % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化。"""
        self.embedded_batches.append(list(texts))
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        """单条向量化。"""
        return self._vector(text)


# ---------------------------------------------------------------------------
#  对话模型替身（仍然是真正的 LangChain ChatModel）
# ---------------------------------------------------------------------------


def build_fake_chat_model(answer: str = "依据条文，商业银行应当每季度报送一次。[1]"):
    """构造 LangChain 官方假模型，可指定固定回复。"""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    return GenericFakeChatModel(messages=itertools.cycle([AIMessage(content=answer)]))


class FakeChatClient:
    """模拟 :class:`app.llm.qwen.QwenChatClient` 的最小接口（暴露 ``.model``）。"""

    def __init__(self, answer: str = "依据条文，商业银行应当每季度报送一次。[1]") -> None:
        self.answer = answer
        self.model = build_fake_chat_model(answer)

    def status(self) -> Dict[str, Any]:
        """接入状态。"""
        return {
            "provider": "fake",
            "model": "fake-chat",
            "configured": True,
            "temperature": 0.0,
        }

    def is_configured(self) -> bool:
        """恒为已配置。"""
        return True


# ---------------------------------------------------------------------------
#  构造测试数据
# ---------------------------------------------------------------------------


def make_clause_vector(
    pk: int,
    document_id: int,
    embedding: Sequence[float],
    *,
    status: str = DocStatus.EFFECTIVE.value,
    business_line: str = BusinessLine.BANKING.value,
    doc_type: str = DocType.NORMATIVE.value,
    effective_ts: int = 0,
    expiry_ts: int = 0,
    clause_no: str = "",
    chunk_index: int = 0,
) -> ClauseVector:
    """便捷构造 :class:`ClauseVector`。"""
    return ClauseVector(
        pk=pk,
        document_id=document_id,
        embedding=list(embedding),
        chunk_index=chunk_index,
        status=status,
        business_line=business_line,
        doc_type=doc_type,
        effective_ts=effective_ts,
        expiry_ts=expiry_ts,
        clause_no=clause_no,
    )


__all__ = [
    "FakeVectorStore",
    "FakeEmbedder",
    "FakeChatClient",
    "build_fake_chat_model",
    "make_clause_vector",
]
