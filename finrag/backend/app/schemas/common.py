"""通用响应模型。"""

from __future__ import annotations

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装。

    成功：``{"code": 0, "message": "ok", "data": {...}}``
    失败：``{"code": 40400, "message": "资源不存在", "detail": ...}``
    """

    code: int = Field(default=0, description="业务错误码，0 表示成功")
    message: str = Field(default="ok", description="提示信息")
    data: Optional[T] = Field(default=None, description="业务数据")

    @classmethod
    def ok(cls, data: T = None, message: str = "ok") -> "ApiResponse[T]":
        """构造成功响应。"""
        return cls(code=0, message=message, data=data)

    @classmethod
    def fail(cls, code: int, message: str, detail: object = None) -> dict:
        """构造失败响应体（供异常处理器直接返回 dict）。"""
        return {"code": code, "message": message, "detail": detail}


class PageMeta(BaseModel):
    """分页元信息。"""

    total: int = Field(description="总条数")
    limit: int = Field(description="每页条数")
    offset: int = Field(description="偏移量")
    has_more: bool = Field(description="是否还有下一页")


class PageResult(BaseModel, Generic[T]):
    """分页结果。"""

    items: List[T] = Field(default_factory=list, description="数据列表")
    meta: PageMeta = Field(description="分页元信息")

    @classmethod
    def create(cls, items: List[T], total: int, limit: int, offset: int) -> "PageResult[T]":
        """按总数与分页参数构造。"""
        return cls(
            items=items,
            meta=PageMeta(
                total=total,
                limit=limit,
                offset=offset,
                has_more=(offset + len(items)) < total,
            ),
        )


class HealthComponent(BaseModel):
    """单个依赖组件的健康状态。"""

    name: str
    healthy: bool
    detail: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = Field(description="ok / degraded / down")
    version: str
    app_env: str
    components: List[HealthComponent] = Field(default_factory=list)


__all__ = [
    "ApiResponse",
    "PageMeta",
    "PageResult",
    "HealthComponent",
    "HealthResponse",
]
