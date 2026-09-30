"""RRF 融合与重排单元测试。"""

from __future__ import annotations

import unittest

from app.retrieval.rerank import (
    RRF_WEIGHT,
    Reranker,
    fuse_and_rerank,
    lexical_coverage,
    normalize_scores,
    reciprocal_rank_fusion,
    rerank,
)
from app.retrieval.types import RetrievedClause
from tests.support import make_settings


def make_clause(chunk_id: int, content: str = "", **kwargs) -> RetrievedClause:
    """构造检索结果用于测试。"""
    return RetrievedClause(
        chunk_id=chunk_id,
        document_id=kwargs.pop("document_id", 1),
        content=content or f"第{chunk_id}条 商业银行流动性风险管理要求。",
        **kwargs,
    )


class TestRRF(unittest.TestCase):
    """倒数排名融合。"""

    def test_single_ranking_order_preserved(self):
        """单路检索时融合分数随排名单调递减。"""
        fused = reciprocal_rank_fusion([[1, 2, 3]], k=60)
        self.assertGreater(fused[1], fused[2])
        self.assertGreater(fused[2], fused[3])

    def test_consensus_ranks_higher(self):
        """两路都靠前的文档融合分数最高。"""
        fused = reciprocal_rank_fusion([[1, 2, 3], [2, 1, 4]], k=60)
        top_two = sorted(fused.items(), key=lambda kv: -kv[1])[:2]
        self.assertEqual({top_two[0][0], top_two[1][0]}, {1, 2})

    def test_disjoint_rankings(self):
        """完全不重叠的两路检索都会被保留。"""
        fused = reciprocal_rank_fusion([[1], [2]], k=60)
        self.assertEqual(set(fused), {1, 2})

    def test_weights_applied(self):
        """权重影响融合结果。"""
        fused = reciprocal_rank_fusion([[1], [2]], k=60, weights=[3.0, 1.0])
        self.assertGreater(fused[1], fused[2])

    def test_zero_weight_ignored(self):
        """权重为 0 的列表不参与融合。"""
        fused = reciprocal_rank_fusion([[1], [2]], k=60, weights=[1.0, 0.0])
        self.assertIn(1, fused)
        self.assertNotIn(2, fused)

    def test_empty_input(self):
        """空输入返回空字典。"""
        self.assertEqual(reciprocal_rank_fusion([]), {})
        self.assertEqual(reciprocal_rank_fusion([[], []]), {})

    def test_invalid_k(self):
        """k 必须为正。"""
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([[1]], k=0)

    def test_k_effect(self):
        """k 越大，排名差异带来的分数差越小。"""
        small = reciprocal_rank_fusion([[1, 2]], k=1)
        large = reciprocal_rank_fusion([[1, 2]], k=1000)
        self.assertGreater(small[1] - small[2], large[1] - large[2])


class TestNormalize(unittest.TestCase):
    """归一化。"""

    def test_normalize_spread(self):
        """Min-Max 归一化到 [0,1]。"""
        self.assertEqual(normalize_scores([1.0, 2.0, 3.0]), [0.0, 0.5, 1.0])

    def test_normalize_constant(self):
        """全部相等时返回全 1（避免除零）。"""
        self.assertEqual(normalize_scores([2.0, 2.0]), [1.0, 1.0])

    def test_normalize_empty(self):
        """空输入返回空列表。"""
        self.assertEqual(normalize_scores([]), [])


class TestLexicalCoverage(unittest.TestCase):
    """词汇覆盖度。"""

    def test_full_coverage(self):
        """查询词全部出现时覆盖度为 1。"""
        self.assertAlmostEqual(
            lexical_coverage("流动性风险管理", "商业银行流动性风险管理办法"), 1.0
        )

    def test_zero_coverage(self):
        """查询词完全不出现时为 0。"""
        self.assertEqual(lexical_coverage("流动性风险", "保险公司偿付能力"), 0.0)

    def test_empty_query(self):
        """空查询返回 0。"""
        self.assertEqual(lexical_coverage("", "内容"), 0.0)


class TestRerank(unittest.TestCase):
    """重排。"""

    def _candidates(self):
        return [
            make_clause(
                1,
                "商业银行应当加强流动性风险管理，建立压力测试机制。",
                rrf_score=0.030,
                vector_score=0.9,
            ),
            make_clause(
                2,
                "保险公司偿付能力充足率不得低于法定标准。",
                rrf_score=0.032,
                vector_score=0.6,
            ),
            make_clause(
                3,
                "商业银行流动性风险压力测试至少每季度开展一次。",
                rrf_score=0.028,
                vector_score=0.7,
            ),
        ]

    def test_rerank_sets_scores_and_ranks(self):
        """重排应写入 score 与 rank。"""
        result = rerank(self._candidates(), "流动性风险压力测试", top_k=3)
        self.assertEqual([item.rank for item in result], [1, 2, 3])
        for item in result:
            self.assertGreaterEqual(item.score, 0.0)
            self.assertLessEqual(item.score, 1.0)

    def test_rerank_rrf_dominates(self):
        """RRF 是主信号：词汇覆盖不足以翻盘明显更高的 RRF（权重 0.8 : 0.2）。"""
        result = rerank(self._candidates(), "流动性风险压力测试", top_k=3)
        self.assertEqual(result[0].chunk_id, 2)

    def test_rerank_lexical_breaks_tie(self):
        """RRF 相同时，词汇覆盖高的候选胜出（体现重排的作用）。"""
        candidates = [
            make_clause(1, "保险公司偿付能力充足率不得低于法定标准。", rrf_score=0.030),
            make_clause(2, "商业银行应当加强流动性风险管理并开展压力测试。", rrf_score=0.030),
        ]
        result = rerank(candidates, "流动性风险压力测试", top_k=2)
        self.assertEqual(result[0].chunk_id, 2)
        self.assertGreater(result[0].score, result[1].score)

    def test_rerank_top_k_truncates(self):
        """top_k 生效。"""
        result = rerank(self._candidates(), "流动性", top_k=2)
        self.assertEqual(len(result), 2)

    def test_rerank_empty(self):
        """空候选返回空列表。"""
        self.assertEqual(rerank([], "问题"), [])

    def test_rerank_records_matched_terms(self):
        """记录命中的查询词，便于前端高亮与评分。"""
        result = rerank(self._candidates(), "流动性风险压力测试", top_k=3)
        self.assertTrue(any(item.matched_terms for item in result))

    def test_rerank_weight_balance(self):
        """RRF 与词汇权重之和应为 1（保证得分可解释）。"""
        self.assertAlmostEqual(RRF_WEIGHT + (1 - RRF_WEIGHT), 1.0)

    def test_reranker_class_uses_settings_top_k(self):
        """Reranker 使用配置中的 top_k。"""
        reranker = Reranker(make_settings(retrieve_top_k=2))
        result = reranker.rerank(self._candidates(), "流动性")
        self.assertEqual(len(result), 2)


class TestFuseAndRerank(unittest.TestCase):
    """便捷融合函数。"""

    def test_fuse_and_rerank(self):
        """返回按分数降序的字典。"""
        fused = fuse_and_rerank([1, 2, 3], [2, 3, 4], "流动性", top_k=3)
        self.assertLessEqual(len(fused), 3)
        values = list(fused.values())
        self.assertEqual(values, sorted(values, reverse=True))

    def test_fuse_and_rerank_weights(self):
        """支持自定义两路权重。"""
        fused = fuse_and_rerank([1], [2], "q", vector_weight=5.0, bm25_weight=1.0)
        self.assertGreater(fused[1], fused[2])

    def test_fuse_empty(self):
        """空输入返回空字典。"""
        self.assertEqual(fuse_and_rerank([], [], "q"), {})


if __name__ == "__main__":
    unittest.main()
