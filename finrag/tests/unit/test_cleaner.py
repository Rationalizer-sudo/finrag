"""文本清洗模块单元测试。"""

from __future__ import annotations

import unittest

from app.ingest.cleaner import (
    clean_text,
    collapse_whitespace,
    dedupe_paragraphs,
    estimate_tokens,
    is_footer_line,
    is_page_number_line,
    normalize_clause_spacing,
    normalize_dates,
    normalize_doc_no,
    normalize_unicode,
    remove_repeated_lines,
    strip_control_chars,
)


class TestNormalizers(unittest.TestCase):
    """基础规范化函数。"""

    def test_normalize_unicode_fullwidth_alnum(self):
        """全角字母数字转半角。"""
        self.assertEqual(normalize_unicode("２０２０年ＡＢＣ"), "2020年ABC")

    def test_normalize_unicode_keeps_cjk_punctuation(self):
        """中文标点必须保留（不做 NFKC，避免破坏原文）。"""
        text = "商业银行，应当遵守。"
        self.assertEqual(normalize_unicode(text), text)

    def test_normalize_unicode_empty(self):
        """空输入安全返回。"""
        self.assertEqual(normalize_unicode(""), "")

    def test_strip_control_chars(self):
        """控制字符与零宽字符被移除，换行保留。"""
        self.assertEqual(strip_control_chars("a\x00b\x07c\nd\ufeffe"), "abc\nde")

    def test_normalize_clause_spacing(self):
        """被空格拆散的条文号应还原。"""
        self.assertEqual(
            normalize_clause_spacing("第 十二 条 商业银行应当"), "第十二条 商业银行应当"
        )
        self.assertEqual(normalize_clause_spacing("第 三 章 总则"), "第三章 总则")

    def test_normalize_dates(self):
        """中文日期中的空白被压缩。"""
        self.assertEqual(
            normalize_dates("自 2020 年 6 月 1 日 起施行"), "自2020年6月1日起施行"
        )

    def test_normalize_doc_no(self):
        """文号括号统一为六角括号。"""
        self.assertEqual(normalize_doc_no("银发(2020)第15号"), "银发〔2020〕15号")
        self.assertEqual(normalize_doc_no("证监发[2019]3号"), "证监发〔2019〕3号")
        self.assertEqual(normalize_doc_no("金规【2021】1号"), "金规〔2021〕1号")

    def test_collapse_whitespace(self):
        """多余空白与空行被压缩。"""
        self.assertEqual(collapse_whitespace("a   b \n\n\n\n c "), "a b\n\nc")


class TestLineClassifiers(unittest.TestCase):
    """页码与页脚行识别。"""

    def test_page_number_lines(self):
        """常见页码形态应被识别。"""
        for line in ("- 12 -", "第 12 页", "12", "第3页 / 共30页", "— 5 —"):
            with self.subTest(line=line):
                self.assertTrue(is_page_number_line(line))

    def test_non_page_number_lines(self):
        """正文行不应被误判为页码。"""
        for line in ("第十二条 商业银行应当", "2020年6月1日", "共30家机构"):
            with self.subTest(line=line):
                self.assertFalse(is_page_number_line(line))

    def test_footer_lines(self):
        """页脚/水印行识别。"""
        self.assertTrue(is_footer_line("下载于 http://www.pbc.gov.cn"))
        self.assertTrue(is_footer_line("版权所有 中国人民银行"))
        self.assertFalse(is_footer_line("商业银行应当建立流动性风险管理体系"))


class TestRepeatedLines(unittest.TestCase):
    """跨页重复行去除。"""

    def test_remove_repeated_lines(self):
        """多页重复的页眉应被删除。"""
        pages = [
            "中国人民银行文件\n第一条 内容甲",
            "中国人民银行文件\n第二条 内容乙",
            "中国人民银行文件\n第三条 内容丙",
        ]
        cleaned = remove_repeated_lines(pages, min_repeat=3)
        for page in cleaned:
            self.assertNotIn("中国人民银行文件", page)
        self.assertIn("第一条", cleaned[0])

    def test_few_pages_kept(self):
        """页数不足时不启用重复行判定（避免误删正文）。"""
        pages = ["中国人民银行文件\n第一条", "中国人民银行文件\n第二条"]
        cleaned = remove_repeated_lines(pages, min_repeat=3)
        self.assertIn("中国人民银行文件", cleaned[0])

    def test_empty_pages(self):
        """空输入返回空列表。"""
        self.assertEqual(remove_repeated_lines([]), [])


class TestCleanText(unittest.TestCase):
    """一站式清洗。"""

    def test_clean_text_full_pipeline(self):
        """清洗应同时完成去页脚、统一文号、修正条文号与日期空格。"""
        raw = (
            "银发(2020)第15号\n"
            "- 1 -\n"
            "第 三 条 本办法自 2020 年 6 月 1 日 起施行。\n"
            "http://www.pbc.gov.cn\n"
        )
        cleaned = clean_text(raw)
        self.assertIn("银发〔2020〕15号", cleaned)
        self.assertIn("第三条", cleaned)
        self.assertIn("2020年6月1日起施行", cleaned)
        self.assertNotIn("http://", cleaned)
        self.assertNotIn("- 1 -", cleaned)

    def test_clean_text_numeric_lines_option(self):
        """drop_numeric_lines 控制是否删除「超长纯数字行」。

        注：1~4 位的纯数字行本身就会被页码规则删除，
        该开关的作用对象是更长的数字串（扫描件里的杂讯）。
        """
        raw = "第十二条 内容\n20240101\n"
        self.assertIn("20240101", clean_text(raw))
        self.assertNotIn("20240101", clean_text(raw, drop_numeric_lines=True))

    def test_clean_text_empty(self):
        """空输入安全。"""
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text(None), "")  # type: ignore[arg-type]


class TestDedupeParagraphs(unittest.TestCase):
    """段落去重。"""

    def test_dedupe(self):
        """完全重复的段落只保留一份。"""
        text = "第一段内容。\n\n第二段内容。\n\n第一段内容。"
        result = dedupe_paragraphs(text)
        self.assertEqual(result.count("第一段内容。"), 1)
        self.assertEqual(result.count("第二段内容。"), 1)

    def test_dedupe_empty(self):
        """空输入安全。"""
        self.assertEqual(dedupe_paragraphs(""), "")


class TestEstimateTokens(unittest.TestCase):
    """词数估算。"""

    def test_estimate_tokens(self):
        """中文按字、英文按词、数字按串计数。"""
        self.assertEqual(estimate_tokens("商业银行"), 4)
        self.assertEqual(estimate_tokens("hello world"), 2)
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("共3类第5条"), 3)


if __name__ == "__main__":
    unittest.main()
