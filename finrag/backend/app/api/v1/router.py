"""v1 路由汇总。

上传路由依赖 ``python-multipart``，采用能力探测后按需挂载，
避免可选依赖缺失导致整个服务启动失败。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import favorites, health, knowledge, qa
from app.core.features import multipart_available
from app.core.logging_conf import get_logger
from app.schemas.common import ApiResponse

logger = get_logger(__name__)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(qa.router)
api_router.include_router(knowledge.router)
api_router.include_router(favorites.router)


def _register_upload_fallback() -> None:
    """依赖缺失时注册占位接口，返回可操作的安装提示而不是 404。"""
    fallback = APIRouter(prefix="/knowledge", tags=["知识库"])

    @fallback.post(
        "/documents/upload",
        response_model=ApiResponse[dict],
        summary="上传制度文件（依赖缺失）",
    )
    def upload_unavailable() -> ApiResponse[dict]:
        """``python-multipart`` 未安装时的提示接口。"""
        return ApiResponse.fail(
            code=50010,
            message=(
                "文件上传功能不可用：服务端缺少依赖 python-multipart。"
                "请在后端环境执行 pip install python-multipart 后重启服务；"
                "临时可改用 POST /api/v1/knowledge/documents/text 以文本方式入库。"
            ),
        )

    api_router.include_router(fallback)


if multipart_available():
    from app.api.v1 import upload

    api_router.include_router(upload.router)
else:
    logger.warning(
        "python-multipart 未安装：/knowledge/documents/upload 已切换为提示接口。"
        "执行 pip install python-multipart 后自动恢复完整上传能力。"
    )
    _register_upload_fallback()


__all__ = ["api_router", "multipart_available"]
