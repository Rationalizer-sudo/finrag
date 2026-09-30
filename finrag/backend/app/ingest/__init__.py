"""ingest 包：多模态解析 -> 清洗 -> 元数据抽取 -> 条文切分 -> 入库。"""

from app.ingest.clause_splitter import (
    ClauseChunk,
    split_by_clause,
    split_by_recursive,
    split_document,
    summarize_chunks,
)
from app.ingest.cleaner import clean_text, dedupe_paragraphs, estimate_tokens
from app.ingest.loaders import (
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    TEXT_EXTENSIONS,
    ParsedDocument,
    ParsedPage,
    available_loaders,
    load_document,
    sha256_file,
    sha256_text,
)
from app.ingest.metadata import DocumentMeta, extract_metadata
from app.ingest.ocr import OcrEngine, ocr_status
from app.ingest.pipeline import IngestPipeline, IngestResult

__all__ = [
    "IngestPipeline",
    "IngestResult",
    "ClauseChunk",
    "split_document",
    "split_by_clause",
    "split_by_recursive",
    "summarize_chunks",
    "clean_text",
    "dedupe_paragraphs",
    "estimate_tokens",
    "ParsedDocument",
    "ParsedPage",
    "load_document",
    "available_loaders",
    "sha256_file",
    "sha256_text",
    "SUPPORTED_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "PDF_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "DocumentMeta",
    "extract_metadata",
    "OcrEngine",
    "ocr_status",
]
