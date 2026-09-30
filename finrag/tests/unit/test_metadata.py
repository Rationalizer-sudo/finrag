"""元数据抽取单元测试。"""

from __future__ import annotations

import unittest
from datetime import date

from app.core.constants import BusinessLine, DocStatus, DocType
from app.ingest.metadata import (
    DocumentMeta,
    clean_title_line,
    effective_timestamp,
    extract_doc_no,
    extract_effective_date,
    extract_expiry_date,
    extract_issuer,
    extract_metadata,
    extract_publish_date,
    extract_tags,
    extract_title,
    infer_business_line,
    infer_doc_type,
    infer_status,
    next_expiry_hint,
    parse_cn_date,
    strip_file_name,
)

SAMPLE = """中国人民银行关于规范商业银行流动性风险管理的通知
银发(2020)第15号
中国人民银行
发布日期：2020年3月1日
第一条 为加强商业银行流动性风险管理，根据《中华人民共和国商业银行法》制定本通知。
第二条 本通知适用于商业银行。
第十二条 本办法自2020年6月1日起施行。
"""


class TestDateParsing(unittest.TestCase):
    """日期解析。"""

    def test_parse_cn_date(self):
        """中文日期解析。"""
        self.assertEqual(parse_cn_date("2020年6月1日"), date(2020, 6, 1))
        self.assertEqual(parse_cn_date("2020-06-01"), date(2020, 6, 1))
        self.assertEqual(parse_cn_date("2020/6/1"), date(2020, 6, 1))

    def test_parse_cn_date_invalid(self):
        """非法日期与空值返回 None。"""
        self.assertIsNone(parse_cn_date("2020年13月40日"))
        self.assertIsNone(parse_cn_date(""))
        self.assertIsNone(parse_cn_date("没有日期"))

    def test_effective_timestamp(self):
        """日期转秒级时间戳；None -> 0。"""
        self.assertGreater(effective_timestamp(date(2020, 6, 1)), 0)
        self.assertEqual(effective_timestamp(None), 0)
        self.assertEqual(effective_timestamp(None, default=7), 7)

    def test_next_expiry_hint(self):
        """有效期提示计算。"""
        self.assertEqual(next_expiry_hint(date(2020, 1, 1), years=3), date(2022, 12, 31))
        self.assertIsNone(next_expiry_hint(None))

    def test_next_expiry_hint_leap_year(self):
        """闰年 2 月 29 日不应抛异常。"""
        self.assertEqual(next_expiry_hint(date(2020, 2, 29), years=1), date(2021, 2, 27))


class TestFieldExtraction(unittest.TestCase):
    """各字段抽取。"""

    def test_extract_doc_no(self):
        """文号抽取与归一化。"""
        self.assertEqual(extract_doc_no(SAMPLE), "银发〔2020〕15号")
        self.assertEqual(extract_doc_no("证监发〔2019〕3号"), "证监发〔2019〕3号")
        self.assertEqual(extract_doc_no("没有文号"), "")

    def test_extract_title(self):
        """标题抽取优先于文件名。"""
        title = extract_title(SAMPLE, "random.txt")
        self.assertIn("流动性风险管理", title)

    def test_extract_title_book_marks(self):
        """《》包裹的标题应被提取。"""
        self.assertEqual(extract_title("《商业银行资本管理办法》\n正文", "x.txt"), "商业银行资本管理办法")

    def test_extract_title_skips_marker_lines(self):
        """整行标记（【…】）不应被当成标题。"""
        text = "【演示数据集·非真实法规】\n商业银行流动性风险管理办法\n银发〔2020〕1号"
        self.assertEqual(extract_title(text, "x.txt"), "商业银行流动性风险管理办法")

    def test_extract_title_fallback_to_filename(self):
        """无有效标题行时回退文件名。"""
        self.assertEqual(extract_title("", "demo_001_银行_流动性风险管理.txt"), "银行 流动性风险管理")

    def test_strip_file_name(self):
        """文件名清洗。"""
        self.assertEqual(strip_file_name("中国人民银行_2020_办法.txt"), "中国人民银行 2020 办法")
        self.assertEqual(strip_file_name(""), "")

    def test_clean_title_line(self):
        """标题装饰符号清理。"""
        self.assertEqual(clean_title_line("《商业银行法》"), "商业银行法")

    def test_extract_issuer(self):
        """发文机关抽取：优先文号前缀映射。"""
        self.assertEqual(extract_issuer(SAMPLE, "银发〔2020〕15号"), "中国人民银行")
        self.assertEqual(extract_issuer("中国证券监督管理委员会公告", ""), "中国证券监督管理委员会")

    def test_extract_issuer_by_position(self):
        """多个机关出现时取文首最近的。"""
        text = "国务院办公厅转发中国证券监督管理委员会关于……的通知"
        self.assertEqual(extract_issuer(text, ""), "国务院办公厅")

    def test_extract_publish_date(self):
        """发布日期抽取。"""
        self.assertEqual(extract_publish_date(SAMPLE), date(2020, 3, 1))

    def test_extract_effective_date(self):
        """施行日期抽取。"""
        self.assertEqual(extract_effective_date(SAMPLE), date(2020, 6, 1))
        self.assertIsNone(extract_effective_date("没有施行日期"))

    def test_extract_expiry_date(self):
        """失效日期抽取。"""
        self.assertEqual(
            extract_expiry_date("本规定自2018年1月1日起废止。"), date(2018, 1, 1)
        )
        self.assertIsNone(extract_expiry_date("长期有效"))


class TestInference(unittest.TestCase):
    """状态、条线与类型推断。"""

    def test_infer_status_effective(self):
        """施行日期已过 -> 现行有效。"""
        self.assertEqual(
            infer_status("", date(2020, 1, 1), None, None, today=date(2024, 1, 1)),
            DocStatus.EFFECTIVE.value,
        )

    def test_infer_status_pending(self):
        """施行日期未到 -> 尚未生效。"""
        self.assertEqual(
            infer_status("", date(2030, 1, 1), None, None, today=date(2024, 1, 1)),
            DocStatus.PENDING.value,
        )

    def test_infer_status_repealed_by_expiry(self):
        """失效日期已过 -> 已废止。"""
        self.assertEqual(
            infer_status("", date(2010, 1, 1), date(2018, 1, 1), None, today=date(2024, 1, 1)),
            DocStatus.REPEALED.value,
        )

    def test_infer_status_repealed_by_keyword(self):
        """明示废止用语 -> 已废止。"""
        self.assertEqual(
            infer_status("本通知予以废止。"), DocStatus.REPEALED.value
        )

    def test_infer_status_amended(self):
        """修订后重新发布 -> 已修订。"""
        self.assertEqual(
            infer_status("本办法修订后重新发布。"), DocStatus.AMENDED.value
        )

    def test_infer_status_immediate_effect(self):
        """自公布之日起施行 -> 现行有效。"""
        self.assertEqual(
            infer_status("本指引自公布之日起施行。"), DocStatus.EFFECTIVE.value
        )

    def test_infer_status_unknown(self):
        """无任何线索 -> 状态待核。"""
        self.assertEqual(infer_status("一段没有日期的说明"), DocStatus.UNKNOWN.value)

    def test_infer_business_line(self):
        """业务条线按关键词投票。"""
        self.assertEqual(infer_business_line("保险公司偿付能力管理规定"), BusinessLine.INSURANCE.value)
        self.assertEqual(infer_business_line("非银行支付机构监督管理条例"), BusinessLine.PAYMENT.value)
        self.assertEqual(infer_business_line("无关标题"), BusinessLine.GENERAL.value)

    def test_infer_doc_type_law_by_title(self):
        """标题为「中华人民共和国XX法」-> 法律。"""
        self.assertEqual(
            infer_doc_type("中华人民共和国商业银行法", "根据本法制定"), DocType.LAW.value
        )

    def test_infer_doc_type_ignores_body_citation(self):
        """正文引用法律不应把本文件误判为法律（重要回归点）。"""
        result = infer_doc_type(
            "商业银行流动性风险管理办法",
            "根据《中华人民共和国商业银行法》等法律法规，制定本办法。",
        )
        self.assertNotEqual(result, DocType.LAW.value)
        self.assertEqual(result, DocType.NORMATIVE.value)

    def test_infer_doc_type_admin_regulation(self):
        """国务院令 -> 行政法规。"""
        self.assertEqual(
            infer_doc_type("某某条例", "国务院令第700号"), DocType.ADMIN_REGULATION.value
        )

    def test_infer_doc_type_judicial(self):
        """法释文号 -> 司法解释。"""
        self.assertEqual(
            infer_doc_type("关于审理金融案件的解释", "", "法释〔2020〕1号"),
            DocType.JUDICIAL.value,
        )

    def test_infer_doc_type_self_regulation(self):
        """行业协会自律规则。"""
        self.assertEqual(
            infer_doc_type("中国证券业协会自律规则", ""), DocType.SELF_REGULATION.value
        )

    def test_infer_doc_type_other(self):
        """完全无线索 -> 其他。"""
        self.assertEqual(infer_doc_type("某文件", "无关键词"), DocType.OTHER.value)


class TestTagsAndMeta(unittest.TestCase):
    """标签与整体抽取。"""

    def test_extract_tags(self):
        """标签应包含条线与类型，且不含停用词。"""
        tags = extract_tags(
            "商业银行流动性风险管理办法",
            SAMPLE,
            BusinessLine.BANKING.value,
            DocType.NORMATIVE.value,
        )
        self.assertIn(BusinessLine.BANKING.value, tags)
        self.assertIn(DocType.NORMATIVE.value, tags)
        self.assertNotIn("应当", tags)
        self.assertNotIn("相关", tags)

    def test_extract_metadata_end_to_end(self):
        """整体抽取结果完整。"""
        meta = extract_metadata(SAMPLE, file_name="demo.txt", source_url="https://x.test")
        self.assertIsInstance(meta, DocumentMeta)
        self.assertEqual(meta.doc_no, "银发〔2020〕15号")
        self.assertEqual(meta.issuer, "中国人民银行")
        self.assertEqual(meta.business_line, BusinessLine.BANKING.value)
        self.assertEqual(meta.effective_date, date(2020, 6, 1))
        self.assertEqual(meta.source_url, "https://x.test")
        self.assertEqual(meta.confidence["doc_no"], 1.0)

    def test_metadata_to_dict(self):
        """序列化日期为 ISO 字符串。"""
        meta = extract_metadata(SAMPLE)
        data = meta.to_dict()
        self.assertEqual(data["effective_date"], "2020-06-01")
        self.assertIsInstance(data["tags"], list)

    def test_metadata_empty_text(self):
        """空文本不崩溃，且状态标记为待核。"""
        meta = extract_metadata("", file_name="empty.txt")
        self.assertEqual(meta.status, DocStatus.UNKNOWN.value)
        self.assertEqual(meta.title, "empty")
        self.assertIsInstance(meta.tags, list)
        self.assertEqual(meta.doc_no, "")

    def test_extract_doc_no_loose_brackets(self):
        """未清洗的半角括号文号也能识别。"""
        self.assertEqual(extract_doc_no("银发(2020)第15号"), "银发〔2020〕15号")
        self.assertEqual(extract_doc_no("证监发[2019]3号"), "证监发〔2019〕3号")


if __name__ == "__main__":
    unittest.main()
