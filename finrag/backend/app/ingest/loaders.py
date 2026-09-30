"""多模态文档加载器。

统一把 ``txt / md / pdf / docx / 图片`` 解析为 :class:`ParsedDocument`，
上层（切分、入库、问答）只依赖这一个中间表示，新增格式只需在此注册。

对应课题技术要求：「支持文本、PDF、图片（OCR 解析）文档导入」。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.config import Settings, get_settings
from app.core.errors import ParserError, UnsupportedFileError
from app.core.logging_conf import get_logger
from app.ingest.ocr import OcrEngine

logger = get_logger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | DOCX_EXTENSIONS | IMAGE_EXTENSIONS

# PDF 文本层少于该字符数时，判定为扫描件并尝试 OCR
PDF_TEXT_LAYER_MIN_CHARS = 60

# 无文本层时的占位提示，避免上层拿到空字符串而无从判断
SCANNED_PDF_HINT = (
    "【系统提示】该 PDF 未提取到文本层，可能是扫描件。"
    "请启用 OCR（安装 tesseract-ocr 与 pdf2image）后重新导入。"
)


@dataclass
class ParsedPage:
    """单页解析结果。"""

    page_no: int
    text: str
    ocr_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {"page_no": self.page_no, "text": self.text, "ocr_used": self.ocr_used}


@dataclass
class ParsedDocument:
    """文档解析的统一中间表示。"""

    file_name: str
    file_type: str
    text: str
    pages: List[ParsedPage] = field(default_factory=list)
    source_path: str = ""
    ocr_used: bool = False
    parser_name: str = ""
    file_hash: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        """全文字符数。"""
        return len(self.text or "")

    def to_dict(self, with_text: bool = True) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        data: Dict[str, Any] = {
            "file_name": self.file_name,
            "file_type": self.file_type,
            "char_count": self.char_count,
            "page_count": len(self.pages),
            "ocr_used": self.ocr_used,
            "parser_name": self.parser_name,
            "file_hash": self.file_hash,
            "warnings": self.warnings,
        }
        if with_text:
            data["text"] = self.text
        return data


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """计算文件 SHA-256（增量入库的判重依据）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    """计算文本 SHA-256（条文去重依据）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def split_extension(path: str | Path) -> str:
    """返回小写扩展名（含点）。"""
    return Path(path).suffix.lower()


# --------------------------------------------------------------- 各格式解析
def load_text(path: str | Path) -> ParsedDocument:
    """解析纯文本 / Markdown（自动尝试 utf-8，回退 gbk）。"""
    file_path = Path(path)
    last_error: Optional[Exception] = None
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            text = file_path.read_text(encoding=encoding)
            return ParsedDocument(
                file_name=file_path.name,
                file_type=split_extension(file_path),
                text=text,
                pages=[ParsedPage(page_no=1, text=text)],
                source_path=str(file_path),
                parser_name=f"text:{encoding}",
                file_hash=sha256_file(file_path),
            )
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise ParserError(f"文本文件编码无法识别（{file_path.name}）：{last_error}")


def load_docx(path: str | Path) -> ParsedDocument:
    """解析 Word 文档（含表格文本）。"""
    file_path = Path(path)
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise ParserError("未安装 python-docx，无法解析 .docx（pip install python-docx）") from exc

    try:
        document = docx.Document(str(file_path))
    except Exception as exc:
        raise ParserError(f"解析 docx 失败（{file_path.name}）：{exc}") from exc

    blocks: List[str] = [p.text for p in document.paragraphs if p.text and p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))

    text = "\n".join(blocks)
    return ParsedDocument(
        file_name=file_path.name,
        file_type=".docx",
        text=text,
        pages=[ParsedPage(page_no=1, text=text)],
        source_path=str(file_path),
        parser_name="python-docx",
        file_hash=sha256_file(file_path),
    )


def load_pdf(path: str | Path, settings: Optional[Settings] = None) -> ParsedDocument:
    """解析 PDF：先取文本层，缺失时按配置回退到图片 OCR。"""
    cfg = settings or get_settings()
    file_path = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ParserError("未安装 pypdf，无法解析 PDF（pip install pypdf）") from exc

    try:
        reader = PdfReader(str(file_path))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")  # 尝试空密码
            except Exception:  # pragma: no cover
                raise ParserError(f"PDF 已加密，无法解析：{file_path.name}")
        pages: List[ParsedPage] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                content = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - 个别损坏页
                logger.warning("PDF 第 %d 页解析失败：%s", number, exc)
                content = ""
            pages.append(ParsedPage(page_no=number, text=content))
    except ParserError:
        raise
    except Exception as exc:
        raise ParserError(f"PDF 解析失败（{file_path.name}）：{exc}") from exc

    text = "\n".join(page.text for page in pages).strip()
    warnings: List[str] = []
    ocr_used = False

    if len(text) < PDF_TEXT_LAYER_MIN_CHARS:
        ocr = OcrEngine(cfg)
        if cfg.pdf_image_fallback and ocr.available:
            logger.info("PDF %s 无文本层，启用 OCR 回退", file_path.name)
            try:
                ocr_pages = ocr.pdf_to_pages(file_path)
                pages = [ParsedPage(page_no=n, text=t, ocr_used=True) for n, t in ocr_pages]
                text = "\n".join(page.text for page in pages).strip()
                ocr_used = True
            except Exception as exc:
                warnings.append(f"OCR 回退失败：{exc}")
                logger.warning("PDF OCR 回退失败：%s", exc)
        elif not ocr.available:
            warnings.append(f"未启用 OCR（{ocr.status()['reason']}）")
        if not text:
            text = SCANNED_PDF_HINT
            warnings.append("该 PDF 未提取到有效文本，请确认是否为扫描件")

    return ParsedDocument(
        file_name=file_path.name,
        file_type=".pdf",
        text=text,
        pages=pages,
        source_path=str(file_path),
        ocr_used=ocr_used,
        parser_name="pypdf" + ("+tesseract" if ocr_used else ""),
        file_hash=sha256_file(file_path),
        warnings=warnings,
    )


def load_image(path: str | Path, settings: Optional[Settings] = None) -> ParsedDocument:
    """解析图片 / 扫描件：走 Tesseract OCR。"""
    cfg = settings or get_settings()
    file_path = Path(path)
    ocr = OcrEngine(cfg)
    text = ocr.image_path_to_text(file_path)
    return ParsedDocument(
        file_name=file_path.name,
        file_type=split_extension(file_path),
        text=text,
        pages=[ParsedPage(page_no=1, text=text, ocr_used=True)],
        source_path=str(file_path),
        ocr_used=True,
        parser_name="tesseract",
        file_hash=sha256_file(file_path),
        warnings=[] if text.strip() else ["OCR 未识别出文字，请检查图片清晰度或语言包"],
    )


# ------------------------------------------------------------------ 分发入口
_LOADERS: Dict[str, Callable[..., ParsedDocument]] = {}


def register_loader(extensions: set, loader: Callable[..., ParsedDocument]) -> None:
    """注册自定义解析器，便于扩展到 .doc / .html 等格式。"""
    for ext in extensions:
        _LOADERS[ext.lower()] = loader


def available_loaders() -> Dict[str, str]:
    """返回扩展名 -> 解析器名称的映射。"""
    return {ext: getattr(fn, "__name__", str(fn)) for ext, fn in sorted(_LOADERS.items())}


register_loader(TEXT_EXTENSIONS, load_text)
register_loader(PDF_EXTENSIONS, load_pdf)
register_loader(DOCX_EXTENSIONS, load_docx)
register_loader(IMAGE_EXTENSIONS, load_image)


def load_document(path: str | Path, settings: Optional[Settings] = None) -> ParsedDocument:
    """按扩展名分发解析，是所有入库流程的唯一入口。

    :raises UnsupportedFileError: 扩展名不在支持列表内。
    :raises ParserError: 解析过程失败。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ParserError(f"文件不存在：{file_path}")

    extension = split_extension(file_path)
    loader = _LOADERS.get(extension)
    if loader is None:
        raise UnsupportedFileError(
            f"不支持的文件类型 {extension or '(无扩展名)'}，"
            f"当前支持：{', '.join(sorted(_LOADERS))}"
        )

    if loader is load_pdf or loader is load_image:
        return loader(file_path, settings)
    return loader(file_path)


__all__ = [
    "ParsedDocument",
    "ParsedPage",
    "load_document",
    "load_text",
    "load_pdf",
    "load_docx",
    "load_image",
    "register_loader",
    "available_loaders",
    "sha256_file",
    "sha256_text",
    "split_extension",
    "SUPPORTED_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "PDF_EXTENSIONS",
    "DOCX_EXTENSIONS",
    "IMAGE_EXTENSIONS",
]
