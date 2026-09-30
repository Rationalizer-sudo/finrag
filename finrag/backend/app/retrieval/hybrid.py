"""混合检索器（向量 + BM25 + RRF + 时效过滤）。

完整链路::

    查询
     ├─ 向量检索(Milvus, 余弦相似度, 带时效/条线过滤) ─┐
     └─ 关键词检索(MySQL 条文 + BM25, 同口径过滤)     ─┤
                                                      ├─ RRF 融合
                                                      ├─ MySQL 回查正文（协同查询）
                                                      └─ 归一化 + 词汇覆盖重排 → Top-K

这是课题「结合向量检索与关键词检索」的具体落地，也是
「问答时优先返回有效条文，过滤过时信息」的执行点。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.constants import RetrievalMode
from app.core.logging_conf import get_logger
from app.db import repository
from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.retrieval.bm25_retriever import Bm25Retriever, get_bm25_retriever
from app.retrieval.rerank import reciprocal_rank_fusion, rerank
from app.retrieval.types import RetrievalFilters, RetrievedClause
from app.retrieval.vector_retriever import VectorRetriever

logger = get_logger(__name__)


def build_retrieved_clause(
    chunk: KnowledgeChunk,
    document: Optional[KnowledgeDocument] = None,
) -> RetrievedClause:
    """把 MySQL 条文记录装配为 :class:`RetrievedClause`。"""
    doc = document if document is not None else getattr(chunk, "document", None)
    return RetrievedClause(
        chunk_id=int(chunk.id),
        document_id=int(chunk.document_id),
        content=chunk.content or "",
        clause_no=chunk.clause_no or "",
        chapter=chunk.chapter or "",
        article_title=chunk.article_title or "",
        status=chunk.status or "",
        effective_date=chunk.effective_date,
        expiry_date=chunk.expiry_date,
        business_line=chunk.business_line or "",
        doc_type=chunk.doc_type or "",
        doc_title=getattr(doc, "title", "") or "",
        doc_no=getattr(doc, "doc_no", "") or "",
        issuer=getattr(doc, "issuer", "") or "",
        source_url=getattr(doc, "source_url", "") or "",
        tags=[tag.name for tag in (chunk.tags or [])],
    )


class HybridRetriever:
    """混合检索器：同时使用语义与关键词信号。"""

    def __init__(
        self,
        vector_retriever: VectorRetriever,
        bm25_retriever: Optional[Bm25Retriever] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.bm25_retriever = bm25_retriever or get_bm25_retriever()
        self.settings: Settings = settings or get_settings()

    # ---------------------------------------------------------------- 检索
    def retrieve(
        self,
        db: Session,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
        mode: Optional[str] = None,
    ) -> List[RetrievedClause]:
        """执行检索，返回重排后的 Top-K 条文。

        :param db: MySQL 会话（BM25 语料读取 + 向量命中回查正文）。
        :param query: 用户问题。
        :param top_k: 返回条数，默认取配置 ``RETRIEVE_TOP_K``。
        :param filters: 时效性/条线过滤条件。
        :param mode: ``vector`` / ``bm25`` / ``hybrid``，默认取配置。
        """
        text = (query or "").strip()
        if not text:
            return []

        limit = int(top_k or self.settings.retrieve_top_k)
        candidate_k = max(int(self.settings.retrieve_candidate_k), limit * 3)
        active_mode = mode or self.settings.retrieve_mode
        if active_mode not in RetrievalMode.values():
            active_mode = RetrievalMode.HYBRID.value

        # 未显式传过滤条件时统一使用默认值（only_valid=True）。
        # 必须在此处归一化，否则向量链路会自带有效性过滤、而关键词链路不过滤，
        # 导致已废止条文通过 BM25 混入结果——两条链路的过滤口径必须完全一致。
        active_filters = filters if filters is not None else RetrievalFilters()

        vector_hits = []
        bm25_hits: List = []
        if active_mode in (RetrievalMode.VECTOR.value, RetrievalMode.HYBRID.value):
            vector_hits = self.vector_retriever.retrieve(text, candidate_k, active_filters)
        if active_mode in (RetrievalMode.BM25.value, RetrievalMode.HYBRID.value):
            bm25_hits = self.bm25_retriever.retrieve(db, text, candidate_k, active_filters)

        vector_ids = [hit.chunk_id for hit in vector_hits]
        vector_scores = {hit.chunk_id: hit.score for hit in vector_hits}
        bm25_ids = [chunk_id for chunk_id, _ in bm25_hits]
        bm25_scores = {chunk_id: score for chunk_id, score in bm25_hits}
        vector_ranks = {chunk_id: pos for pos, chunk_id in enumerate(vector_ids, start=1)}
        bm25_ranks = {chunk_id: pos for pos, chunk_id in enumerate(bm25_ids, start=1)}

        if active_mode == RetrievalMode.VECTOR.value:
            fused: Dict[int, float] = {
                cid: 1.0 / (self.settings.rrf_k + rank)
                for cid, rank in vector_ranks.items()
            }
        elif active_mode == RetrievalMode.BM25.value:
            fused = {
                cid: 1.0 / (self.settings.rrf_k + rank) for cid, rank in bm25_ranks.items()
            }
        else:
            fused = reciprocal_rank_fusion(
                [vector_ids, bm25_ids], k=self.settings.rrf_k
            )

        if not fused:
            logger.info(
                "检索无命中：mode=%s query=%r filters=%s",
                active_mode, text[:40], active_filters.to_dict(),
            )
            return []

        ordered_ids = [cid for cid, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]

        # ---- MySQL 回查正文（结构化 + 向量协同查询） ----
        records = repository.get_chunks_by_ids(db, ordered_ids)
        candidates: List[RetrievedClause] = []
        missing = 0
        for chunk_id in ordered_ids:
            chunk = records.get(chunk_id)
            if chunk is None:
                missing += 1
                continue
            clause = build_retrieved_clause(chunk)
            clause.vector_score = float(vector_scores.get(chunk_id, 0.0))
            clause.bm25_score = float(bm25_scores.get(chunk_id, 0.0))
            clause.rrf_score = float(fused.get(chunk_id, 0.0))
            clause.vector_rank = int(vector_ranks.get(chunk_id, 0))
            clause.bm25_rank = int(bm25_ranks.get(chunk_id, 0))
            candidates.append(clause)

        if missing:
            logger.warning("有 %d 条向量命中在 MySQL 中不存在，已跳过（索引待重建）", missing)

        # ---- 相关度下限过滤 ----
        # 关键词命中（BM25 分数 > 0）视为强证据，直接保留；
        # 仅靠向量召回的候选必须达到相似度下限，否则丢弃——
        # 这是「资料不足时如实说明未检索到相关规定」的工程保障。
        before = len(candidates)
        candidates = [
            item
            for item in candidates
            if item.bm25_score > 0 or item.vector_score >= self.settings.min_vector_score
        ]
        if before != len(candidates):
            logger.info(
                "相关度下限过滤：%d -> %d 条（阈值 %.2f）",
                before, len(candidates), self.settings.min_vector_score,
            )
        if not candidates:
            return []

        results = rerank(candidates, text, top_k=limit)
        logger.info(
            "检索完成：mode=%s 向量=%d BM25=%d 融合=%d 返回=%d",
            active_mode, len(vector_ids), len(bm25_ids), len(fused), len(results),
        )
        return results

    def retrieve_documents(
        self,
        db: Session,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[RetrievalFilters] = None,
        mode: Optional[str] = None,
    ) -> List:
        """检索并转换为 LangChain ``Document`` 列表（供生成链使用）。"""
        return [
            clause.to_langchain_document()
            for clause in self.retrieve(db, query, top_k, filters, mode)
        ]


def create_hybrid_retriever(
    vector_retriever: VectorRetriever,
    settings: Optional[Settings] = None,
) -> HybridRetriever:
    """工厂函数：装配混合检索器。"""
    return HybridRetriever(vector_retriever, None, settings)


def dedupe_by_document(
    clauses: Sequence[RetrievedClause], max_per_document: int = 2
) -> List[RetrievedClause]:
    """同一文档最多保留 N 条条文，提升证据多样性（避免单一文件刷屏）。"""
    if max_per_document <= 0:
        return list(clauses)
    counter: Dict[int, int] = {}
    kept: List[RetrievedClause] = []
    for clause in clauses:
        used = counter.get(clause.document_id, 0)
        if used >= max_per_document:
            continue
        counter[clause.document_id] = used + 1
        kept.append(clause)
    return kept


__all__ = [
    "HybridRetriever",
    "create_hybrid_retriever",
    "build_retrieved_clause",
    "dedupe_by_document",
]
