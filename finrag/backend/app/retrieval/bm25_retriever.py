"""BM25 关键词检索器。

设计要点
--------
* **内存索引 + 指纹缓存**：BM25 需要全量语料统计，因此把 ``(chunk_id, 正文)``
  读入内存建立索引；索引以「语料指纹」（条文数 + 最近更新时间）做缓存键，
  知识库增量更新后指纹变化，索引自动重建，无需人工清缓存；
* **分词口径统一**：与标签、可信度评分共用 :mod:`app.retrieval.tokenizer`；
* **过滤口径一致**：与向量链路共用 :class:`RetrievalFilters`，
  保证「过时条文不返回」这一差异点两条链路都生效。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger
from app.db import repository
from app.db.models import KnowledgeChunk
from app.retrieval.tokenizer import tokenize, tokenize_query
from app.retrieval.types import RetrievalFilters

logger = get_logger(__name__)


@dataclass
class Bm25Index:
    """内存 BM25 索引。"""

    chunk_ids: List[int] = field(default_factory=list)
    tokens: List[List[str]] = field(default_factory=list)
    _bm25: Any = None

    @property
    def size(self) -> int:
        """索引中的条文数量。"""
        return len(self.chunk_ids)

    def build(self, corpus: Sequence[Tuple[int, str]]) -> "Bm25Index":
        """用 ``[(chunk_id, content), ...]`` 构建索引。

        若未安装 ``rank_bm25``，则退化为纯词频打分（仍可工作，
        只是关键词权重不如 BM25 精细）。
        """
        self.chunk_ids = [int(item[0]) for item in corpus]
        self.tokens = [tokenize(item[1]) for item in corpus]

        # 语料为空、或全部文档都切不出词元时，BM25Okapi 会因 avgdl=0 除零，
        # 因此退化为词频打分路径（结果同样为空，但不会抛异常）。
        total_tokens = sum(len(item) for item in self.tokens)
        if not self.tokens or total_tokens == 0:
            self._bm25 = None
            return self

        try:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self.tokens)
        except ImportError:  # pragma: no cover - 依赖缺失分支
            logger.warning("未安装 rank_bm25，BM25 退化为词频打分")
            self._bm25 = None
        return self

    def search(self, query: str, top_k: int = 10) -> List[Tuple[int, float]]:
        """检索，返回 ``[(chunk_id, score), ...]``，按分数降序。"""
        if not self.chunk_ids or not query.strip():
            return []
        query_tokens = tokenize_query(query)
        if not query_tokens:
            return []

        if self._bm25 is not None:
            scores = self._bm25.get_scores(query_tokens)
        else:  # pragma: no cover - 退化路径
            scores = [float(sum(tokens.count(q) for q in query_tokens)) for tokens in self.tokens]

        ranked = sorted(
            zip(self.chunk_ids, (float(s) for s in scores)),
            key=lambda item: (-item[1], item[0]),
        )
        return [(cid, score) for cid, score in ranked[:top_k] if score > 0]


class Bm25Retriever:
    """带缓存的 BM25 检索器（线程安全）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._cache: Dict[str, Tuple[Tuple[int, str], Bm25Index]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 缓存管理
    @staticmethod
    def _filter_key(filters: Optional[RetrievalFilters]) -> str:
        """把过滤条件序列化为可哈希的缓存键。"""
        if filters is None:
            return "default"
        return json.dumps(filters.to_dict(), sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _fingerprint(db: Any, filters: Optional[RetrievalFilters]) -> Tuple[int, str]:
        """语料指纹：条文总数 + 最近更新时间，用于判断是否需要重建索引。"""
        stmt = repository.apply_chunk_filters(
            select(func.count(), func.max(KnowledgeChunk.updated_at)).select_from(
                KnowledgeChunk
            ),
            filters,
        )
        row = db.execute(stmt).first()
        count = int(row[0] or 0) if row else 0
        latest = row[1] if row else None
        # SQLite 与 MySQL 对 MAX(datetime) 的返回类型不同，这里统一为字符串
        if hasattr(latest, "isoformat"):
            latest_text = latest.isoformat()
        else:
            latest_text = str(latest) if latest is not None else ""
        return count, latest_text

    def get_index(self, db: Any, filters: Optional[RetrievalFilters] = None) -> Bm25Index:
        """获取（必要时重建）BM25 索引。"""
        key = self._filter_key(filters)
        fingerprint = self._fingerprint(db, filters)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]

        corpus = repository.iter_chunk_texts(db, filters)
        index = Bm25Index().build(corpus)
        with self._lock:
            self._cache[key] = (fingerprint, index)
        logger.info("BM25 索引重建完成：%d 条条文", index.size)
        return index

    def invalidate(self) -> None:
        """主动失效所有缓存索引（入库完成后调用）。"""
        with self._lock:
            self._cache.clear()
        logger.debug("BM25 索引缓存已清空")

    # ---------------------------------------------------------------- 检索
    def retrieve(
        self,
        db: Any,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
    ) -> List[Tuple[int, float]]:
        """关键词检索，返回 ``[(chunk_id, score), ...]``。"""
        if not query or not query.strip():
            return []
        limit = int(top_k or self.settings.retrieve_candidate_k)
        # 与向量链路保持同一默认口径：未显式指定时只检索现行有效/尚未生效条文
        active_filters = filters if filters is not None else RetrievalFilters()
        index = self.get_index(db, active_filters)
        return index.search(query.strip(), top_k=limit)

    def retrieve_ids(
        self,
        db: Any,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
    ) -> List[int]:
        """只返回命中的 chunk_id 列表。"""
        return [cid for cid, _ in self.retrieve(db, query, top_k, filters)]


# 进程级单例：让索引缓存跨请求复用
_default_retriever: Optional[Bm25Retriever] = None


def get_bm25_retriever(refresh: bool = False) -> Bm25Retriever:
    """获取全局 BM25 检索器。"""
    global _default_retriever
    if _default_retriever is None or refresh:
        _default_retriever = Bm25Retriever()
    return _default_retriever


__all__ = ["Bm25Index", "Bm25Retriever", "get_bm25_retriever"]
