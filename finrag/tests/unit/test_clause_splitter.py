"""条文切分模块单元测试。"""

from __future__ import annotations

import unittest

from app.ingest.clause_splitter import (
    ClauseChunk,
    build_splitter,
    extract_article_title,
    split_by_clause,
    split_by_recursive,
    split_document,
    summarize_chunks,
)
from tests.support import make_settings

DOC = """商业银行流动性风险管理办法
银发〔2020〕15号

第一章 总则
第一条 为加强商业银行流动性风险管理，根据有关法律法规，制定本办法。
第二条 本办法适用于在中华人民共和国境内依法设立的商业银行及其分支机构。
第三条 【定义】本办法所称流动性风险，是指商业银行无法以合理成本及时获得充足资金的风险。

第二章 治理与职责
第四条 商业银行董事会应当承担流动性风险管理的最终责任，高级管理层负责组织实施。
第五条 商业银行应当设立专门部门负责流动性风险管理，配备专职人员。

第三章 附则
第六条 本办法自2020年6月1日起施行。
"""

LONG_CLAUSE_DOC = (
    "第一条 商业银行应当建立流动性风险管理体系，"
    + "包括治理架构、管理策略、风险识别与计量、监测与报告、压力测试、应急计划等要素。" * 24
    + "\n第二条 本办法自2020年6月1日起施行。"
)

NO_CLAUSE_DOC = """关于加强金融消费者权益保护的通知

各金融机构应当切实履行金融消费者权益保护义务，健全工作机制。
各机构应当完善投诉处理流程，及时回应消费者诉求。
各机构应当加强金融知识普及教育，提升消费者风险识别能力。
"""


class TestClauseChunk(unittest.TestCase):
    """切分结果数据结构。"""

    def test_properties(self):
        """字符数、词数与展示条文号。"""
        chunk = ClauseChunk(index=0, content="商业银行应当遵守规定。", clause_no="第一条")
        self.assertEqual(chunk.char_count, len(chunk.content))
        self.assertGreater(chunk.token_count, 0)
        self.assertEqual(chunk.display_clause_no, "第一条")

    def test_display_clause_no_with_part(self):
        """超长条文分片时展示分片序号。"""
        chunk = ClauseChunk(index=0, content="内容", clause_no="第五条", part=2)
        self.assertEqual(chunk.display_clause_no, "第五条(2)")

    def test_to_dict(self):
        """序列化字段完整。"""
        chunk = ClauseChunk(index=3, content="内容", clause_no="第三条", chapter="第二章")
        data = chunk.to_dict()
        for key in ("index", "clause_no", "chapter", "article_title", "content",
                    "char_count", "token_count", "is_clause_based"):
            self.assertIn(key, data)


class TestSplitter(unittest.TestCase):
    """切分策略。"""

    def test_build_splitter_uses_settings(self):
        """递归切分器参数来自配置。"""
        settings = make_settings(chunk_size=200, chunk_overlap=30)
        splitter = build_splitter(settings)
        self.assertEqual(splitter._chunk_size, 200)
        self.assertEqual(splitter._chunk_overlap, 30)

    def test_split_by_clause(self):
        """按条文切分，条文号与章节正确。"""
        chunks = split_by_clause(DOC)
        self.assertEqual(len(chunks), 6)
        self.assertEqual(chunks[0].clause_no, "第一条")
        self.assertTrue(chunks[0].is_clause_based)
        self.assertEqual(chunks[0].chapter, "第一章 总则")
        self.assertEqual(chunks[3].chapter, "第二章 治理与职责")
        self.assertEqual(chunks[5].chapter, "第三章 附则")

    def test_split_by_clause_extracts_article_title(self):
        """【】小标题应被抽取，同时保留在正文中。"""
        chunks = split_by_clause(DOC)
        third = next(c for c in chunks if c.clause_no == "第三条")
        self.assertEqual(third.article_title, "定义")
        self.assertIn("【定义】", third.content)

    def test_split_by_clause_no_structure(self):
        """无条文结构时返回空列表。"""
        self.assertEqual(split_by_clause(NO_CLAUSE_DOC), [])
        self.assertEqual(split_by_clause(""), [])

    def test_extract_article_title(self):
        """小标题抽取兼容【】与[]。"""
        self.assertEqual(extract_article_title("第十二条【信息披露】内容"), "信息披露")
        self.assertEqual(extract_article_title("第十二条[信息披露]内容"), "信息披露")
        self.assertEqual(extract_article_title("第十二条 内容"), "")

    def test_long_clause_is_split_with_prefix(self):
        """超长条文应二次切分，且每片补上条文号前缀。"""
        chunks = split_by_clause(LONG_CLAUSE_DOC)
        first_clause = [c for c in chunks if c.clause_no == "第一条"]
        self.assertGreater(len(first_clause), 1)
        for index, chunk in enumerate(first_clause, start=1):
            self.assertEqual(chunk.part, index)
            self.assertTrue(chunk.content.startswith("第一条"))
            self.assertLessEqual(chunk.char_count, 900)

    def test_split_by_recursive(self):
        """递归切分用于无条文结构的文档。"""
        chunks = split_by_recursive(NO_CLAUSE_DOC, make_settings(chunk_size=60, chunk_overlap=10))
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertFalse(chunk.is_clause_based)
            self.assertTrue(chunk.clause_no)

    def test_split_by_recursive_empty(self):
        """空文本返回空列表。"""
        self.assertEqual(split_by_recursive(""), [])

    def test_split_document_prefers_clause(self):
        """有条文结构时优先条文切分。"""
        chunks = split_document(DOC)
        self.assertTrue(any(c.is_clause_based for c in chunks))
        self.assertGreaterEqual(len(chunks), 6)

    def test_split_document_falls_back(self):
        """无条文结构时回退递归切分。"""
        chunks = split_document(NO_CLAUSE_DOC, make_settings(chunk_size=60, chunk_overlap=10))
        self.assertGreater(len(chunks), 0)
        self.assertFalse(any(c.is_clause_based for c in chunks))

    def test_split_document_empty(self):
        """空文本返回空列表。"""
        self.assertEqual(split_document(""), [])

    def test_preamble_kept_only_when_long_enough(self):
        """过短的引言（纯标题元数据）不入库，避免噪声。"""
        short_preamble = "某办法\n某发〔2020〕1号\n某机关\n"
        doc = short_preamble + "第一条 内容足够长的一条条文内容在这里。"
        chunks = split_by_clause(doc)
        self.assertNotIn("前言", [c.clause_no for c in chunks])

    def test_preamble_kept_when_substantial(self):
        """有实质内容的引言应作为知识点保留。"""
        preamble = (
            "为贯彻落实党中央、国务院关于防范化解重大金融风险的决策部署，"
            "进一步规范金融市场秩序，保护金融消费者合法权益，现就有关事项通知如下："
        )
        doc = preamble + "\n第一条 内容足够长的一条条文内容在这里。"
        chunks = split_by_clause(doc)
        self.assertIn("前言", [c.clause_no for c in chunks])


class TestSummarize(unittest.TestCase):
    """切分统计。"""

    def test_summarize_chunks(self):
        """统计字段正确。"""
        chunks = split_by_clause(DOC)
        stats = summarize_chunks(chunks)
        self.assertEqual(stats["count"], len(chunks))
        self.assertGreater(stats["total_chars"], 0)
        self.assertGreater(stats["avg_chars"], 0)
        self.assertTrue(stats["clause_based"])

    def test_summarize_empty(self):
        """空列表返回零值而不是报错。"""
        stats = summarize_chunks([])
        self.assertEqual(stats["count"], 0)
        self.assertFalse(stats["clause_based"])


if __name__ == "__main__":
    unittest.main()
