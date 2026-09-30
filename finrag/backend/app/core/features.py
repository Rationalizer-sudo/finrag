"""可选依赖与运行能力探测。

有些依赖属于「有则更好、没有也不该拖垮主服务」的类型（如 ``python-multipart``
只服务文件上传、``pytesseract`` 只服务 OCR）。这里集中做能力探测，
让路由层与健康检查用同一套判断，避免各处重复实现导致口径不一致。
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# 可选依赖 -> 用途说明
OPTIONAL_DEPENDENCIES: Dict[str, str] = {
    "multipart": "文件上传（multipart/form-data）",
    "pytesseract": "图片 / 扫描件 OCR",
    "pdf2image": "扫描版 PDF 转图片（OCR 前置）",
    "docx": "Word(.docx) 文档解析",
    "pypdf": "PDF 文本层解析",
    "pymilvus": "Milvus 向量数据库客户端",
    "pymysql": "MySQL 驱动",
    "rank_bm25": "BM25 关键词检索",
}


def has_module(name: str) -> bool:
    """判断模块是否可导入（不触发实际导入开销）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - 命名空间包异常
        return False


def multipart_available() -> bool:
    """``python-multipart`` 是否可用（文件上传路由的前置依赖）。"""
    return has_module("multipart")


def ocr_available(settings: Settings | None = None) -> bool:
    """OCR 链路是否完整可用（开关 + pytesseract + tesseract 可执行文件）。"""
    from app.ingest.ocr import OcrEngine

    return OcrEngine(settings or get_settings()).available


def dependency_report() -> Dict[str, Any]:
    """返回可选依赖的安装情况，供 ``/health`` 与部署自检脚本使用。"""
    items: Dict[str, Dict[str, Any]] = {}
    for name, purpose in OPTIONAL_DEPENDENCIES.items():
        installed = has_module(name)
        items[name] = {"installed": installed, "purpose": purpose}
    missing = [name for name, item in items.items() if not item["installed"]]
    return {
        "dependencies": items,
        "missing": missing,
        "all_installed": not missing,
        "hint": (
            "缺少可选依赖："
            + ", ".join(missing)
            + "。执行 pip install -r requirements.txt 补全。"
            if missing
            else "全部可选依赖已安装。"
        ),
    }


def feature_flags(settings: Settings | None = None) -> Dict[str, bool]:
    """返回功能开关，供前端隐藏不可用入口。"""
    cfg = settings or get_settings()
    return {
        "file_upload": multipart_available(),
        "ocr": ocr_available(cfg),
        "pdf": has_module("pypdf"),
        "docx": has_module("docx"),
        "vector_store": has_module("pymilvus"),
        "mysql_driver": has_module("pymysql"),
        "bm25": has_module("rank_bm25"),
        "text_ingest": True,
        "hybrid_retrieval": True,
        "citation_tracing": True,
        "credibility_scoring": True,
        "incremental_ingest": True,
        "validity_filtering": True,
        "history": True,
        "favorites": True,
        "streaming": True,
    }


__all__ = [
    "has_module",
    "multipart_available",
    "ocr_available",
    "dependency_report",
    "feature_flags",
    "OPTIONAL_DEPENDENCIES",
]
