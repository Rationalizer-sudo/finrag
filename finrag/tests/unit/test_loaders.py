"""文档加载器与 OCR 模块单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.core.errors import OCRError, ParserError, UnsupportedFileError
from app.ingest.loaders import (
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    ParsedDocument,
    ParsedPage,
    available_loaders,
    load_document,
    load_docx,
    load_text,
    register_loader,
    sha256_file,
    sha256_text,
    split_extension,
)
from app.ingest.ocr import OCR_CHAR_FIXES, OcrEngine, ocr_status
from tests.support import make_settings, temp_dir


def write_text_file(directory: Path, name: str, content: str, encoding: str = "utf-8") -> Path:
    """在临时目录写入文本文件。"""
    path = directory / name
    path.write_text(content, encoding=encoding)
    return path


class TestHashing(unittest.TestCase):
    """哈希工具。"""

    def test_sha256_text_deterministic(self):
        """相同文本哈希一致，不同文本不同。"""
        self.assertEqual(sha256_text("abc"), sha256_text("abc"))
        self.assertNotEqual(sha256_text("abc"), sha256_text("abd"))

    def test_sha256_file(self):
        """文件哈希与内容哈希一致。"""
        directory = temp_dir()
        path = write_text_file(directory, "a.txt", "内容")
        self.assertEqual(sha256_file(path), sha256_text("内容"))

    def test_split_extension(self):
        """扩展名统一小写。"""
        self.assertEqual(split_extension("A.PDF"), ".pdf")
        self.assertEqual(split_extension("noext"), "")


class TestParsedDocument(unittest.TestCase):
    """中间表示。"""

    def test_char_count_and_dict(self):
        """字符数与序列化。"""
        doc = ParsedDocument(
            file_name="a.txt",
            file_type=".txt",
            text="内容",
            pages=[ParsedPage(page_no=1, text="内容")],
        )
        self.assertEqual(doc.char_count, 2)
        data = doc.to_dict()
        self.assertEqual(data["page_count"], 1)
        self.assertEqual(data["char_count"], 2)

    def test_to_dict_without_text(self):
        """可选择不返回正文。"""
        doc = ParsedDocument(file_name="a.txt", file_type=".txt", text="内容")
        self.assertNotIn("text", doc.to_dict(with_text=False))


class TestTextLoader(unittest.TestCase):
    """文本文件解析。"""

    def test_load_utf8(self):
        """UTF-8 文本解析。"""
        directory = temp_dir()
        path = write_text_file(directory, "a.txt", "第十二条 商业银行应当遵守规定。")
        parsed = load_text(path)
        self.assertEqual(parsed.file_type, ".txt")
        self.assertIn("商业银行", parsed.text)
        self.assertEqual(parsed.parser_name, "text:utf-8")
        self.assertTrue(parsed.file_hash)

    def test_load_gbk_fallback(self):
        """非 UTF-8 编码回退到 gb18030/gbk。"""
        directory = temp_dir()
        path = write_text_file(directory, "gbk.txt", "第十二条 商业银行应当遵守规定。", encoding="gbk")
        parsed = load_text(path)
        self.assertIn("商业银行", parsed.text)
        self.assertGreater(parsed.char_count, 0)

    def test_load_utf8_bom(self):
        """带 BOM 的 UTF-8 文件可解析。"""
        directory = temp_dir()
        path = directory / "bom.txt"
        path.write_bytes("\ufeff第一条 内容".encode("utf-8"))
        parsed = load_text(path)
        self.assertIn("第一条", parsed.text)


class TestDocxLoader(unittest.TestCase):
    """Word 文档解析。"""

    def test_load_docx_with_table(self):
        """段落与表格文本都要抽取。"""
        import docx

        directory = temp_dir()
        path = directory / "a.docx"
        document = docx.Document()
        document.add_paragraph("第一条 为规范银行业务，制定本办法。")
        document.add_paragraph("第二条 本办法适用于商业银行。")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "指标"
        table.rows[0].cells[1].text = "阈值"
        document.save(str(path))

        parsed = load_docx(path)
        self.assertIn("第一条", parsed.text)
        self.assertIn("指标", parsed.text)
        self.assertIn("|", parsed.text)
        self.assertEqual(parsed.parser_name, "python-docx")

    def test_load_docx_invalid_file(self):
        """非法 docx 应抛出 ParserError。"""
        directory = temp_dir()
        path = directory / "bad.docx"
        path.write_text("not a real docx", encoding="utf-8")
        with self.assertRaises(ParserError):
            load_docx(path)


class TestDispatcher(unittest.TestCase):
    """按扩展名分发。"""

    def test_available_loaders_cover_supported(self):
        """已注册的解析器覆盖全部声明支持的扩展名。"""
        loaders = available_loaders()
        for extension in SUPPORTED_EXTENSIONS:
            with self.subTest(extension=extension):
                self.assertIn(extension, loaders)

    def test_load_document_missing_file(self):
        """文件不存在抛 ParserError。"""
        with self.assertRaises(ParserError):
            load_document("definitely_missing_file.txt")

    def test_load_document_unsupported_extension(self):
        """不支持的扩展名抛 UnsupportedFileError。"""
        directory = temp_dir()
        path = directory / "a.xyz"
        path.write_text("内容", encoding="utf-8")
        with self.assertRaises(UnsupportedFileError):
            load_document(path)

    def test_load_document_dispatch_text(self):
        """分发到文本解析器。"""
        directory = temp_dir()
        path = write_text_file(directory, "a.md", "第一条 内容在这里。")
        parsed = load_document(path)
        self.assertIn("第一条", parsed.text)

    def test_register_custom_loader(self):
        """可注册自定义解析器（扩展性）。"""
        extension = ".custom"
        before = extension in available_loaders()
        determine = lambda p: ParsedDocument(file_name=p.name, file_type=extension, text="x")  # noqa: E731
        register_loader({extension}, determine)
        try:
            self.assertIn(extension, available_loaders())
        finally:
            from app.ingest import loaders as loaders_module

            loaders_module._LOADERS.pop(extension, None)
        self.assertEqual(before, ".custom" in IMAGE_EXTENSIONS or ".custom" in PDF_EXTENSIONS)


class TestOcrEngine(unittest.TestCase):
    """OCR 引擎（本机未安装 Tesseract 时的降级行为）。"""

    def test_status_structure(self):
        """状态字段完整。"""
        status = ocr_status(make_settings())
        for key in ("enabled", "available", "reason", "binary", "lang", "dpi",
                    "pytesseract", "pdf2image", "pillow"):
            self.assertIn(key, status)

    def test_disabled_by_config(self):
        """配置关闭时 available 必须为 False 且给出原因。"""
        engine = OcrEngine(make_settings(ocr_enabled=False))
        status = engine.status()
        self.assertFalse(status["available"])
        self.assertIn("OCR_ENABLED", status["reason"])

    def test_available_requires_both_module_and_binary(self):
        """可用性 = 开关 且 pytesseract 且 tesseract 可执行文件。"""
        engine = OcrEngine(make_settings())
        status = engine.status()
        expected = bool(
            status["enabled"] and status["pytesseract"] and status["binary"]
            and status["pillow"]
        )
        self.assertEqual(status["available"], expected)

    def test_fix_text(self):
        """OCR 常见错字修正。"""
        engine = OcrEngine(make_settings())
        self.assertEqual(engine.fix_text("第—条 规定"), "第一条 规定")
        self.assertEqual(engine.fix_text("笫二条 规定"), "第二条 规定")
        self.assertEqual(engine.fix_text(""), "")
        for wrong, right in OCR_CHAR_FIXES:
            self.assertEqual(engine.fix_text(wrong), right)

    def test_image_path_missing_file(self):
        """图片不存在时抛 OCRError（先校验文件，再校验依赖）。"""
        engine = OcrEngine(make_settings())
        with self.assertRaises(OCRError):
            engine.image_path_to_text("definitely_missing.png")

    def test_pdf_to_pages_missing_file(self):
        """PDF 不存在时抛 OCRError。"""
        engine = OcrEngine(make_settings())
        with self.assertRaises(OCRError):
            engine.pdf_to_pages("definitely_missing.pdf")


class TestPdfLoaderWithoutOcr(unittest.TestCase):
    """无文本层 PDF 在 OCR 不可用时应给出可读提示而不是崩溃。"""

    def test_pdf_without_text_layer(self):
        """构造空白 PDF，验证降级提示与 warnings。"""
        pypdf = __import__("pypdf")
        directory = temp_dir()
        path = directory / "blank.pdf"
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(path, "wb") as handle:
            writer.write(handle)

        parsed = load_document(path, make_settings())
        self.assertEqual(parsed.file_type, ".pdf")
        self.assertTrue(parsed.warnings)
        self.assertFalse(parsed.ocr_used)


if __name__ == "__main__":
    unittest.main()
