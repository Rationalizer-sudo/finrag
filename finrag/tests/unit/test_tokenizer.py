"""中文分词与相似度工具单元测试。"""

from __future__ import annotations

import unittest

from app.retrieval.tokenizer import (
    STOPWORDS,
    coverage,
    jaccard,
    term_set,
    tokenize,
    tokenize_query,
    tokenizer_name,
    top_terms,
)


class TestTokenize(unittest.TestCase):
    """分词行为。"""

    def test_tokenize_basic(self):
        """中文句子被切分为词。"""
        tokens = tokenize("商业银行应当加强流动性风险管理")
        self.assertTrue(tokens)
        self.assertIn("流动性", tokens)

    def test_tokenize_filters_stopwords(self):
        """停用词应被过滤。"""
        tokens = tokenize("商业银行应当加强管理")
        for word in ("应当", "的", "了"):
            self.assertNotIn(word, tokens)

    def test_tokenize_keeps_stopwords_when_disabled(self):
        """关闭停用词过滤时保留全部词元。"""
        tokens = tokenize("应当加强管理", drop_stopwords=False)
        self.assertIn("应当", tokens)

    def test_tokenize_removes_punctuation(self):
        """标点符号不应成为词元。"""
        tokens = tokenize("商业银行，应当；遵守：规定。")
        for token in tokens:
            self.assertNotIn(token, {"，", "。", "；", "：", "、", "！", "？"})

    def test_tokenize_empty(self):
        """空输入返回空列表。"""
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize(None), [])  # type: ignore[arg-type]

    def test_custom_dictionary_terms(self):
        """金融领域词表生效（长术语不被切碎）。"""
        tokens = tokenize("资本充足率不得低于监管要求")
        self.assertIn("资本充足率", tokens)

    def test_tokenize_query_dedupes(self):
        """查询分词去重但保持顺序。"""
        tokens = tokenize_query("流动性 流动性 风险 风险")
        self.assertEqual(tokens.count("流动性"), 1)
        self.assertLessEqual(len(tokens), len(tokenize("流动性 流动性 风险 风险")))

    def test_stopwords_is_nonempty(self):
        """停用词表非空。"""
        self.assertGreater(len(STOPWORDS), 20)

    def test_tokenizer_name(self):
        """分词器名称可用于响应标注。"""
        self.assertEqual(tokenizer_name(), "jieba")

    def test_top_terms(self):
        """高频词按频次降序返回。"""
        terms = top_terms("流动性风险流动性风险流动性管理", limit=3)
        self.assertTrue(terms)
        self.assertLessEqual(len(terms), 3)
        self.assertEqual(terms[0], "流动性")


class TestSimilarity(unittest.TestCase):
    """相似度工具。"""

    def test_term_set(self):
        """词元集合去重。"""
        self.assertEqual(term_set("流动性 流动性"), {"流动性"})

    def test_jaccard_identical(self):
        """完全相同集合相似度为 1。"""
        self.assertEqual(jaccard(["a", "b"], ["a", "b"]), 1.0)

    def test_jaccard_disjoint(self):
        """完全不相交为 0。"""
        self.assertEqual(jaccard(["a"], ["b"]), 0.0)

    def test_jaccard_empty(self):
        """空集合返回 0 而不是除零。"""
        self.assertEqual(jaccard([], ["a"]), 0.0)

    def test_coverage(self):
        """覆盖率 = 命中数 / needle 总数。"""
        self.assertAlmostEqual(coverage(["a", "b", "c"], ["a", "b"]), 2 / 3)
        self.assertEqual(coverage([], ["a"]), 0.0)
        self.assertEqual(coverage(["a"], ["a"]), 1.0)


if __name__ == "__main__":
    unittest.main()
