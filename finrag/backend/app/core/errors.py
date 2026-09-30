"""业务异常与统一错误码。

接口层据此返回一致的错误结构体 ``{"code": ..., "message": ..., "detail": ...}``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ErrorCode:
    """业务错误码常量表。"""

    OK = 0
    BAD_REQUEST = 40000
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    UNSUPPORTED_FILE = 41500
    FILE_TOO_LARGE = 41300
    VALIDATION_ERROR = 42200
    DB_ERROR = 50010
    VECTOR_DB_ERROR = 50020
    LLM_ERROR = 50030
    OCR_ERROR = 50040
    PARSER_ERROR = 50050
    INTERNAL_ERROR = 50000


class FinRAGError(Exception):
    """所有业务异常的基类。"""

    code: int = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    message: str = "服务内部错误"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        detail: Optional[Any] = None,
        code: Optional[int] = None,
    ) -> None:
        self.message = message or self.message
        self.detail = detail
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为接口返回体。"""
        return {"code": self.code, "message": self.message, "detail": self.detail}


class BadRequestError(FinRAGError):
    code = ErrorCode.BAD_REQUEST
    http_status = 400
    message = "请求参数不合法"


class NotFoundError(FinRAGError):
    code = ErrorCode.NOT_FOUND
    http_status = 404
    message = "资源不存在"


class UnsupportedFileError(FinRAGError):
    code = ErrorCode.UNSUPPORTED_FILE
    http_status = 415
    message = "不支持的文件类型"


class FileTooLargeError(FinRAGError):
    code = ErrorCode.FILE_TOO_LARGE
    http_status = 413
    message = "上传文件超过大小限制"


class ParserError(FinRAGError):
    code = ErrorCode.PARSER_ERROR
    http_status = 500
    message = "文档解析失败"


class OCRError(FinRAGError):
    code = ErrorCode.OCR_ERROR
    http_status = 500
    message = "OCR 识别失败"


class VectorStoreError(FinRAGError):
    code = ErrorCode.VECTOR_DB_ERROR
    http_status = 500
    message = "向量数据库操作失败"


class LLMServiceError(FinRAGError):
    code = ErrorCode.LLM_ERROR
    http_status = 502
    message = "大模型服务调用失败"


class DatabaseError(FinRAGError):
    code = ErrorCode.DB_ERROR
    http_status = 500
    message = "数据库操作失败"


__all__ = [
    "ErrorCode",
    "FinRAGError",
    "BadRequestError",
    "NotFoundError",
    "UnsupportedFileError",
    "FileTooLargeError",
    "ParserError",
    "OCRError",
    "VectorStoreError",
    "LLMServiceError",
    "DatabaseError",
]
