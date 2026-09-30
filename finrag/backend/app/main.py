"""FinRAG FastAPI 应用入口。

职责：
* 应用生命周期管理（日志、依赖容器、MySQL/Milvus 连通性自检）；
* 中间件（CORS、请求耗时统计、请求 ID）；
* 全局异常处理（业务异常 / 参数校验 / 未知异常统一响应体）；
* 挂载 v1 路由与前端静态资源。
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1.router import api_router
from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.errors import ErrorCode, FinRAGError
from app.core.logging_conf import get_logger, setup_logging
from app.db.session import check_connection, get_engine
from app.services.container import get_container, reset_container

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# 前端会读取这些响应头做链路观测
HEADER_REQUEST_ID = "X-Request-ID"
HEADER_PROCESS_TIME = "X-Process-Time-Ms"


def _serialize_validation_errors(errors: list) -> list:
    """把 Pydantic 校验错误转为可 JSON 序列化的结构。

    ``ctx`` 中可能含有 ``ValueError`` 等不可序列化对象，
    ``input`` 可能是 bytes 等特殊类型，这里统一做安全转换。
    """
    cleaned = []
    for error in errors:
        item = {key: value for key, value in dict(error).items() if key != "url"}
        context = item.get("ctx")
        if isinstance(context, dict):
            item["ctx"] = {str(key): str(value) for key, value in context.items()}
        raw_input = item.get("input")
        if raw_input is not None and not isinstance(
            raw_input, (str, int, float, bool, list, dict)
        ):
            item["input"] = repr(raw_input)
        cleaned.append(item)
    return cleaned


def _log_startup_diagnostics(settings: Settings) -> None:
    """启动自检：把数据库/向量库/模型的状态一次性打清楚，降低部署排障成本。"""
    database = check_connection()
    if database.get("connected"):
        logger.info(
            "MySQL 连接正常：%s %s", database.get("dialect"), database.get("version")
        )
        try:
            tables = set(inspect(get_engine()).get_table_names())
        except Exception as exc:  # pragma: no cover
            logger.warning("读取数据表清单失败：%s", exc)
            tables = set()
        if not tables:
            logger.error(
                "MySQL 中尚未创建数据表。请先执行："
                "python scripts/init_mysql.py（或导入 sql/01_schema.sql）"
            )
    else:
        logger.error("MySQL 连接失败：%s", database.get("error"))
        logger.error("请检查 .env 中的 MYSQL_* 配置，并确认 MySQL 服务已启动。")

    try:
        vector = get_container().vector_store.health()
        if vector.get("connected"):
            logger.info(
                "Milvus 连接正常：%s collection=%s 向量数=%s",
                vector.get("uri"), vector.get("collection"), vector.get("vector_count"),
            )
        else:
            logger.warning("Milvus 连接异常：%s", vector.get("error"))
    except Exception as exc:  # pragma: no cover
        logger.warning("Milvus 自检失败：%s", exc)

    status = get_container().status()
    if not status.get("llm", {}).get("configured"):
        logger.warning(
            "未配置 DASHSCOPE_API_KEY，问答接口将返回 502。"
            "请在 .env 中填写通义千问 API Key。"
        )
    if not settings.ocr_enabled:
        logger.info("OCR 已在配置中关闭（OCR_ENABLED=false）")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动自检 + 关停资源释放。"""
    settings = get_settings()
    setup_logging(settings)
    logger.info("=" * 72)
    logger.info("%s v%s 启动中（env=%s）", settings.app_name, __version__, settings.app_env)
    logger.info("=" * 72)
    _log_startup_diagnostics(settings)
    logger.info("%s 启动完成，接口文档：/docs", settings.app_name)
    try:
        yield
    finally:
        logger.info("正在关闭 %s ...", settings.app_name)
        reset_container()
        logger.info("资源已释放，进程退出")


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂。"""
    cfg = settings or get_settings()

    app = FastAPI(
        title="FinRAG · 金融制度智能问答系统",
        description=(
            "基于 RAG（检索增强生成）的金融制度类知识问答系统。\n\n"
            "* **知识库**：MySQL 存结构化条文，Milvus 存向量，协同查询\n"
            "* **检索**：向量检索 + BM25 关键词检索，RRF 融合重排\n"
            "* **生成**：LangChain 编排通义千问，回答强制条文溯源\n"
            "* **时效性**：按生效/失效状态过滤，优先返回现行有效条文\n"
            "* **可信度**：0~100 可解释评分\n"
            "* **多模态**：支持 txt / pdf / docx / 图片(Tesseract OCR) 入库"
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------ 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER_REQUEST_ID, HEADER_PROCESS_TIME],
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next: Any) -> Any:
        """注入请求 ID 与耗时响应头，并记录慢请求。"""
        request_id = request.headers.get(HEADER_REQUEST_ID) or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers[HEADER_REQUEST_ID] = request_id
        response.headers[HEADER_PROCESS_TIME] = str(elapsed_ms)
        if elapsed_ms > cfg.response_time_budget_ms and request.url.path.startswith(
            cfg.api_prefix
        ):
            logger.warning(
                "慢请求：%s %s 耗时 %dms（超出 %dms 预算）",
                request.method, request.url.path, elapsed_ms,
                cfg.response_time_budget_ms,
            )
        return response

    # -------------------------------------------------------- 异常处理
    @app.exception_handler(FinRAGError)
    async def handle_finrag_error(request: Request, exc: FinRAGError) -> JSONResponse:
        """业务异常 -> 统一错误体。"""
        logger.warning("业务异常 [%s] %s %s -> %s", exc.code, request.method,
                       request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """参数校验失败 -> 422，并给出字段级明细。

        Pydantic v2 的 ``errors()`` 会在 ``ctx`` 中携带原始异常对象
        （自定义 ``field_validator`` 抛出的 ``ValueError``），
        直接 ``JSONResponse`` 序列化会抛 ``TypeError`` 并退化成 500。
        因此这里统一把 ``ctx`` 值转为字符串后再返回。
        """
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": "请求参数校验失败",
                "detail": _serialize_validation_errors(exc.errors()),
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """HTTP 异常 -> 统一错误体。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code * 100,
                "message": str(exc.detail),
                "detail": None,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """兜底异常处理，避免堆栈泄漏到客户端。"""
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "服务内部错误，请查看服务端日志",
                "detail": None,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    # ------------------------------------------------------------ 路由
    app.include_router(api_router, prefix=cfg.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> Any:
        """根路径：有构建产物则打开前端，否则跳转接口文档。"""
        if (FRONTEND_DIST / "index.html").is_file():
            return RedirectResponse(url="/app/")
        return RedirectResponse(url="/docs")

    @app.get("/api", include_in_schema=False)
    async def api_index() -> Dict[str, Any]:
        """接口索引，方便快速定位能力。"""
        return {
            "name": cfg.app_name,
            "version": __version__,
            "docs": "/docs",
            "prefix": cfg.api_prefix,
            "endpoints": {
                "health": f"{cfg.api_prefix}/health",
                "capabilities": f"{cfg.api_prefix}/capabilities",
                "ask": f"{cfg.api_prefix}/qa/ask",
                "ask_stream": f"{cfg.api_prefix}/qa/ask/stream",
                "history": f"{cfg.api_prefix}/qa/history",
                "favorites": f"{cfg.api_prefix}/favorites",
                "kb_stats": f"{cfg.api_prefix}/knowledge/stats",
                "upload": f"{cfg.api_prefix}/knowledge/documents/upload",
            },
        }

    # -------------------------------------------------------- 静态资源
    if STATIC_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
    if FRONTEND_DIST.is_dir():
        app.mount(
            "/app", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend"
        )
        logger.info("已挂载前端构建产物：%s", FRONTEND_DIST)

    return app


app = create_app()


__all__ = ["app", "create_app"]
