"""向量检索器。

职责：把查询文本向量化，转换成 Milvus 过滤表达式，检索候选条文主键。
只返回 ``(chunk_id, score)``，正文由服务层回查 MySQL（协同查询）。
"""

from __future__ import annotations

from typing import List, Optional

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger
from app.db.milvus_store import MilvusVectorStore, VectorHit, build_filter_expr
from app.llm.embeddings import Embedder
from app.retrieval.types import RetrievalFilters

logger = get_logger(__name__)


class VectorRetriever:
    """基于 Milvus 的语义检索。"""

    def __init__(
        self,
        vector_store: MilvusVectorStore,
        embedder: Embedder,
        settings: Optional[Settings] = None,
    ) -> None:
        self.store = vector_store
        self.embedder = embedder
        self.settings: Settings = settings or get_settings()

    # ------------------------------------------------------------------ 过滤
    def build_expr(self, filters: Optional[RetrievalFilters] = None) -> str:
        """把检索过滤条件翻译为 Milvus 表达式。

        与 :func:`app.db.repository._apply_chunk_filters` 保持同一口径，
        保证向量链路与关键词链路的时效性过滤结果一致。
        """
        if filters is None:
            return build_filter_expr(only_valid=True)
        return build_filter_expr(
            statuses=filters.statuses,
            business_lines=filters.business_lines,
            doc_types=filters.doc_types,
            document_ids=filters.document_ids,
            as_of=filters.as_of,
            only_valid=filters.only_valid,
            exclude_expired=filters.exclude_expired,
        )

    # ------------------------------------------------------------------ 检索
    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
    ) -> List[VectorHit]:
        """语义检索，返回按相似度降序的命中列表。"""
        if not query or not query.strip():
            return []
        limit = int(top_k or self.settings.retrieve_candidate_k)
        vector = self.embedder.embed_query(query.strip())
        expr = self.build_expr(filters)
        hits = self.store.search(vector, top_k=limit, expr=expr)
        logger.debug("向量检索命中 %d 条（expr=%s）", len(hits), expr or "<无>")
        return hits

    def retrieve_ids(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
    ) -> List[int]:
        """只返回命中的 chunk_id 列表（按相似度降序）。"""
        return [hit.chunk_id for hit in self.retrieve(query, top_k, filters)]


__all__ = ["VectorRetriever"]
