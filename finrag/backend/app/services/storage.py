"""上传文件落盘与校验。

对应课题实施约束：
* 只接受白名单扩展名（文本 / PDF / docx / 图片）；
* 限制单文件大小，避免内存与磁盘被拖垮；
* 文件名净化 + 哈希前缀，防止路径穿越与重名覆盖。
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

from app.core.config import Settings, get_settings
from app.core.errors import FileTooLargeError, UnsupportedFileError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

RE_UNSAFE_NAME = re.compile(r"[^\w\u4e00-\u9fa5.\-]+")


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """净化文件名：去掉路径分隔符与特殊字符，保留中文。"""
    base = Path(name or "upload").name
    cleaned = RE_UNSAFE_NAME.sub("_", base).strip("._") or "upload"
    if len(cleaned) > max_length:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 8:
            cleaned = stem[: max_length - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:max_length]
    return cleaned


def validate_extension(filename: str, settings: Optional[Settings] = None) -> str:
    """校验扩展名是否在白名单内，返回小写扩展名。"""
    cfg = settings or get_settings()
    extension = Path(filename or "").suffix.lower()
    allowed = cfg.allowed_extension_set
    if not extension or extension not in allowed:
        raise UnsupportedFileError(
            f"不支持的文件类型 {extension or '(无扩展名)'}，"
            f"允许的类型：{', '.join(sorted(allowed))}"
        )
    return extension


def validate_size(size: int, filename: str, settings: Optional[Settings] = None) -> None:
    """校验文件字节数是否超限。"""
    cfg = settings or get_settings()
    if size > cfg.max_upload_bytes:
        raise FileTooLargeError(
            f"文件 {filename} 大小 {size / 1024 / 1024:.1f}MB，"
            f"超过上限 {cfg.max_upload_mb}MB"
        )


def save_upload(
    stream: BinaryIO,
    filename: str,
    size: int = 0,
    settings: Optional[Settings] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """把上传流写入本地上传目录。

    :param stream: 可读二进制流（如 FastAPI ``UploadFile.file``）。
    :param filename: 原始文件名。
    :param size: 已知大小（0 表示未知，落盘后再校验）。
    :returns: ``(落盘路径, 元信息)``
    """
    cfg = settings or get_settings()
    extension = validate_extension(filename, cfg)
    if size:
        validate_size(size, filename, cfg)

    target_dir = cfg.upload_path
    safe_name = sanitize_filename(filename)
    unique = f"{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}_{safe_name}"
    target = target_dir / unique

    written = 0
    with open(target, "wb") as handle:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            written += len(block)
            if written > cfg.max_upload_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise FileTooLargeError(
                    f"文件 {filename} 超过上限 {cfg.max_upload_mb}MB，已中止写入"
                )
            handle.write(block)

    logger.info("上传文件已保存：%s（%.2f MB）", target.name, written / 1024 / 1024)
    return target, {
        "original_name": filename,
        "stored_name": unique,
        "extension": extension,
        "size_bytes": written,
        "path": str(target),
    }


def remove_file(path: str | Path) -> bool:
    """删除文件（失败返回 False，不抛异常）。"""
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError as exc:  # pragma: no cover
        logger.warning("删除文件失败 %s：%s", path, exc)
        return False


def disk_usage(path: Optional[str | Path] = None) -> Dict[str, float]:
    """返回上传目录的磁盘使用情况（MB）。"""
    target = Path(path) if path else get_settings().upload_path
    try:
        usage = shutil.disk_usage(str(target))
        return {
            "total_mb": round(usage.total / 1024 / 1024, 2),
            "used_mb": round(usage.used / 1024 / 1024, 2),
            "free_mb": round(usage.free / 1024 / 1024, 2),
        }
    except OSError:  # pragma: no cover
        return {}


__all__ = [
    "sanitize_filename",
    "validate_extension",
    "validate_size",
    "save_upload",
    "remove_file",
    "disk_usage",
]
