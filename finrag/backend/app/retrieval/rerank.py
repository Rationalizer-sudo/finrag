"""检索结果融合与重排。

融合策略：**RRF（Reciprocal Rank Fusion）**
----------------------------------------
向量检索与 BM25 的分数不在同一量纲（余弦相似度 vs 无上界 BM25 分），
直接线性加权需要反复调参且对新语料不稳定。RRF 只利用**排名**信息：

.. math::  score(d) = \\sum_{r \\in R} \\frac{w_r}{k + rank_r(d)}

实践中小 ``k``（默认 60）即可获得稳健效果，是工业界混合检索的常用做法。

在此之上叠加一层**词汇覆盖度重排**：候选条文对查询词的覆盖比例，
用于把「恰好命中关键词但语义偏离」的条文向后压。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger
from app.retrieval.tokenizer import coverage, tokenize_query
from app.retrieval.types import RetrievedClause

logger = get_logger(__name__)

DEFAULT_RRF_K = 60
# 最终得分的组合权重：RRF 为主，词汇覆盖为辅
RRF_WEIGHT = 0.8
LEXICAL_WEIGHT = 0.2


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    k: int = DEFAULT_RRF_K,
    weights: Optional[Sequence[float]] = None,
) -> Dict[int, float]:
    """RRF 融合多个已排序的候选列表。

    :param rankings: 多个 ``[id, ...]`` 列表，各自已按相关性降序。
    :param k: RRF 平滑常数，越大则排名差异影响越小。
    :param weights: 各列表权重，默认等权。
    :returns: ``{id: rrf_score}``。

    示例::

        >>> reciprocal_rank_fusion([[1, 2], [2, 3]], k=60)
        {1: 0.016393..., 2: 0.032522..., 3: 0.015873...}
    """
    if not rankings:
        return {}
    if k <= 0:
        raise ValueError("RRF 参数 k 必须为正整数")

    scores: Dict[int, float] = {}
    for index, ranking in enumerate(rankings):
        weight = 1.0
        if weights is not None and index < len(weights):
            weight = float(weights[index])
        if weight <= 0:
            continue
        for rank, doc_id in enumerate(ranking, start=1):
            key = int(doc_id)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    return scores


def lexical_coverage(query: str, text: str) -> float:
    """查询词在候选条文中的覆盖比例（0~1）。"""
    query_tokens = tokenize_query(query)
    if not query_tokens:
        return 0.0
    return coverage(query_tokens, tokenize_query(text))


def normalize_scores(scores: Sequence[float]) -> List[float]:
    """Min-Max 归一化；全部相等时返回全 1。"""
    if not scores:
        return []
    low, high = min(scores), max(scores)
    if high - low < 1e-12:
        return [1.0 for _ in scores]
    return [(value - low) / (high - low) for value in scores]


def rerank(
    candidates: List[RetrievedClause],
    query: str,
    top_k: Optional[int] = None,
    *,
    lexical_weight: float = LEXICAL_WEIGHT,
) -> List[RetrievedClause]:
    """对融合后的候选做归一化 + 词汇覆盖重排。

    会就地写入 ``score``、``rank``、``matched_terms`` 字段。
    """
    if not candidates:
        return []

    rrf_values = [clause.rrf_score for clause in candidates]
    normalized = normalize_scores(rrf_values)
    query_tokens = set(tokenize_query(query))

    for clause, norm_rrf in zip(candidates, normalized):
        lexical = lexical_coverage(query, clause.content)
        clause.score = round(
            (1.0 - lexical_weight) * norm_rrf + lexical_weight * lexical, 6
        )
        clause.matched_terms = sorted(
            token for token in query_tokens if token in clause.content
        )

    candidates.sort(key=lambda item: (-item.score, -item.vector_score, item.chunk_id))
    limit = int(top_k) if top_k else len(candidates)
    result = candidates[:limit]
    for position, clause in enumerate(result, start=1):
        clause.rank = position
    return result


def fuse_and_rerank(
    vector_ids: Sequence[int],
    bm25_ids: Sequence[int],
    query: str,
    *,
    k: int = DEFAULT_RRF_K,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    top_k: Optional[int] = None,
) -> Dict[int, float]:
    """便捷函数：融合两路检索并返回 ``{chunk_id: rrf_score}``（降序字典）。"""
    fused = reciprocal_rank_fusion(
        [list(vector_ids), list(bm25_ids)], k=k, weights=[vector_weight, bm25_weight]
    )
    ordered = dict(sorted(fused.items(), key=lambda item: (-item[1], item[0])))
    if top_k:
        return dict(list(ordered.items())[: int(top_k)])
    return ordered


class Reranker:
    """重排器（可替换实现，便于后续接入 Cross-Encoder 精排模型）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()

    def rerank(
        self,
        candidates: List[RetrievedClause],
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievedClause]:
        """执行重排。"""
        return rerank(candidates, query, top_k or self.settings.retrieve_top_k)


__all__ = [
    "reciprocal_rank_fusion",
    "lexical_coverage",
    "normalize_scores",
    "rerank",
    "fuse_and_rerank",
    "Reranker",
    "DEFAULT_RRF_K",
]
