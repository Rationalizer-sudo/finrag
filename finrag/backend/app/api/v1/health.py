"""健康检查与系统能力自检接口。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter

from app import __version__
from app.api.deps import ContainerDep, DbSession, SettingsDep
from app.db.session import check_connection
from app.ingest.loaders import available_loaders
from app.ingest.ocr import OcrEngine
from app.rag.credibility import describe_formula
from app.schemas.common import ApiResponse, HealthComponent, HealthResponse

router = APIRouter(tags=["系统"])

_STARTED_AT = time.time()


@router.get("/health", response_model=HealthResponse, summary="健康检查")
def health(
    db: DbSession,
    settings: SettingsDep,
    container: ContainerDep,
) -> HealthResponse:
    """返回各依赖组件（MySQL / Milvus / 大模型 / OCR）的连通状态。"""
    components: List[HealthComponent] = []

    # MySQL
    database = check_connection()
    components.append(
        HealthComponent(
            name="mysql",
            healthy=bool(database.get("connected")),
            detail=database,
        )
    )

    # Milvus
    vector = container.vector_store.health()
    components.append(
        HealthComponent(
            name="milvus",
            healthy=bool(vector.get("connected")),
            detail=vector,
        )
    )

    # 通义千问（对话 + 向量化）
    llm_status = container.status().get("llm", {})
    components.append(
        HealthComponent(
            name="qwen-chat",
            healthy=bool(llm_status.get("configured")),
            detail=llm_status,
        )
    )
    embedding_status = container.status().get("embedding", {})
    components.append(
        HealthComponent(
            name="qwen-embedding",
            healthy=bool(embedding_status.get("configured")),
            detail=embedding_status,
        )
    )

    # OCR
    ocr = OcrEngine(settings).status()
    components.append(
        HealthComponent(
            name="tesseract-ocr",
            healthy=bool(ocr.get("available")),
            detail=ocr,
        )
    )

    healthy_count = sum(1 for item in components if item.healthy)
    if healthy_count == len(components):
        overall = "ok"
    elif components[0].healthy:  # MySQL 是硬依赖
        overall = "degraded"
    else:
        overall = "down"

    return HealthResponse(
        status=overall,
        version=__version__,
        app_env=settings.app_env,
        components=components,
    )


@router.get("/health/live", summary="存活探针")
def liveness() -> Dict[str, Any]:
    """不访问外部依赖的存活探针（供容器编排使用）。"""
    return {"status": "alive", "uptime_seconds": round(time.time() - _STARTED_AT, 1)}


@router.get(
    "/capabilities",
    response_model=ApiResponse[Dict[str, Any]],
    summary="系统能力清单",
)
def capabilities(settings: SettingsDep) -> ApiResponse[Dict[str, Any]]:
    """汇总系统支持的文件类型、检索模式、评分口径等，便于前端自适应。

    说明：``/health``、``/health/live``、``/api`` 属于探针/自描述端点，
    刻意返回**裸对象**（便于容器编排与监控直接消费）；
    其余业务接口统一使用 ``{code, message, data}`` 信封，本接口也不例外。
    """
    from app.core.constants import BusinessLine, DocStatus, DocType, RetrievalMode
    from app.core.features import dependency_report, feature_flags
    from app.schemas.common import ApiResponse as _ApiResponse

    payload = {
        "version": __version__,
        "supported_extensions": sorted(available_loaders().keys()),
        "loaders": available_loaders(),
        "retrieval_modes": RetrievalMode.values(),
        "doc_statuses": DocStatus.values(),
        "valid_doc_statuses": DocStatus.valid_values(),
        "doc_types": DocType.values(),
        "business_lines": BusinessLine.values(),
        "defaults": {
            "top_k": settings.retrieve_top_k,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "retrieval_mode": settings.retrieve_mode,
            "only_valid": True,
            "min_vector_score": settings.min_vector_score,
        },
        "targets": {
            "response_time_ms": settings.response_time_budget_ms,
            "answer_accuracy": settings.verify_answer_accuracy_target,
            "knowledge_points": 1000,
            "test_coverage": 0.70,
        },
        "credibility_formula": describe_formula(),
        "features": feature_flags(settings),
        "dependencies": dependency_report(),
    }
    return _ApiResponse.ok(payload)
