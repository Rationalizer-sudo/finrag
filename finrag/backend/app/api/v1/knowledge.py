"""知识库管理接口。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query

from app.api.deps import DbSession, KbService, SettingsDep
from app.core.logging_conf import get_logger
from app.schemas.common import ApiResponse, PageResult
from app.schemas.knowledge import (
    ChunkOut,
    DocumentOut,
    DocumentUpdateRequest,
    IngestResultOut,
    IngestTaskOut,
    IngestTextRequest,
    KnowledgeStats,
    TagOut,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["知识库"])


# ---------------------------------------------------------------- 总览
@router.get("/stats", response_model=ApiResponse[KnowledgeStats], summary="知识库总览")
def stats(db: DbSession, service: KbService) -> ApiResponse[KnowledgeStats]:
    """返回文档数、知识点数、向量数、标签数及各依赖组件状态与达标情况。"""
    return ApiResponse.ok(KnowledgeStats(**service.stats(db)))


# ---------------------------------------------------------------- 入库
@router.post(
    "/documents/text",
    response_model=ApiResponse[IngestResultOut],
    summary="以文本新增制度",
)
def ingest_text(
    payload: IngestTextRequest, db: DbSession, service: KbService
) -> ApiResponse[IngestResultOut]:
    """粘贴制度全文入库：自动清洗、切分、抽元数据、向量化。"""
    result = service.ingest_text(db, payload)
    return ApiResponse.ok(IngestResultOut(**result.to_dict()), message=result.message)


@router.get(
    "/documents/upload/limits",
    response_model=ApiResponse[dict],
    summary="上传限制说明",
)
def upload_limits(settings: SettingsDep) -> ApiResponse[dict]:
    """返回允许的扩展名与大小上限，供前端做前置校验。"""
    return ApiResponse.ok(
        {
            "allowed_extensions": sorted(settings.allowed_extension_set),
            "max_upload_mb": settings.max_upload_mb,
        }
    )


@router.post(
    "/vectors/rebuild",
    response_model=ApiResponse[dict],
    summary="重建向量索引",
)
def rebuild_vectors(db: DbSession, service: KbService) -> ApiResponse[dict]:
    """按 MySQL 现有条文重新生成 embedding 并写入 Milvus。

    用于: Milvus 集合被误删、向量写入曾失败、或调整了 embedding 模型后的恢复。
    """
    return ApiResponse.ok(service.rebuild_vectors(db), message="向量索引重建完成")


# ---------------------------------------------------------------- 文档
@router.get(
    "/documents",
    response_model=ApiResponse[PageResult[DocumentOut]],
    summary="制度文档列表",
)
def list_documents(
    db: DbSession,
    service: KbService,
    keyword: Optional[str] = Query(default=None, max_length=100, description="按名称/文号/机关搜索"),
    status: Optional[str] = Query(default=None, description="时效状态"),
    business_line: Optional[str] = Query(default=None, description="业务条线"),
    doc_type: Optional[str] = Query(default=None, description="文件类型"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PageResult[DocumentOut]]:
    """分页查询知识库中的制度文档。"""
    items, total = service.list_documents(
        db,
        keyword=keyword,
        status=status,
        business_line=business_line,
        doc_type=doc_type,
        limit=limit,
        offset=offset,
    )
    return ApiResponse.ok(PageResult.create(items, total, limit, offset))


@router.get(
    "/documents/{document_id}",
    response_model=ApiResponse[DocumentOut],
    summary="文档详情",
)
def get_document(document_id: int, db: DbSession, service: KbService) -> ApiResponse[DocumentOut]:
    """获取单个制度文档详情。"""
    return ApiResponse.ok(service.get_document(db, document_id))


@router.patch(
    "/documents/{document_id}",
    response_model=ApiResponse[DocumentOut],
    summary="修订文档元数据",
)
def update_document(
    document_id: int,
    payload: DocumentUpdateRequest,
    db: DbSession,
    service: KbService,
) -> ApiResponse[DocumentOut]:
    """人工修订元数据（如人工确认时效状态、补录文号）。

    修改时效字段时会同步更新条文表与向量库的过滤字段，
    确保下一次检索立即按新状态过滤。
    """
    return ApiResponse.ok(service.update_document(db, document_id, payload))


@router.delete(
    "/documents/{document_id}",
    response_model=ApiResponse[dict],
    summary="删除文档",
)
def delete_document(document_id: int, db: DbSession, service: KbService) -> ApiResponse[dict]:
    """删除文档，同时清理其条文与 Milvus 向量。"""
    return ApiResponse.ok(service.delete_document(db, document_id))


# ---------------------------------------------------------------- 条文
@router.get(
    "/chunks",
    response_model=ApiResponse[PageResult[ChunkOut]],
    summary="条文列表",
)
def list_chunks(
    db: DbSession,
    service: KbService,
    document_id: Optional[int] = Query(default=None),
    keyword: Optional[str] = Query(default=None, max_length=100),
    only_valid: bool = Query(default=False),
    business_line: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PageResult[ChunkOut]]:
    """分页查询知识点（条文级片段）。"""
    items, total = service.list_chunks(
        db,
        document_id=document_id,
        keyword=keyword,
        only_valid=only_valid,
        business_line=business_line,
        limit=limit,
        offset=offset,
    )
    return ApiResponse.ok(PageResult.create(items, total, limit, offset))


@router.get(
    "/chunks/{chunk_id}",
    response_model=ApiResponse[ChunkOut],
    summary="条文详情（溯源落地页）",
)
def get_chunk(chunk_id: int, db: DbSession, service: KbService) -> ApiResponse[ChunkOut]:
    """按主键获取条文正文，问答引用链接即指向此处。"""
    return ApiResponse.ok(service.get_chunk(db, chunk_id))


# ---------------------------------------------------------------- 标签 / 任务 / OCR
@router.get("/tags", response_model=ApiResponse[List[TagOut]], summary="知识点标签")
def list_tags(
    db: DbSession,
    service: KbService,
    category: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> ApiResponse[List[TagOut]]:
    """列出知识点标签（支撑标签分类管理）。"""
    return ApiResponse.ok(service.list_tags(db, category, limit))


@router.get(
    "/tasks",
    response_model=ApiResponse[List[IngestTaskOut]],
    summary="入库任务记录",
)
def list_tasks(
    db: DbSession,
    service: KbService,
    limit: int = Query(default=20, ge=1, le=200),
) -> ApiResponse[List[IngestTaskOut]]:
    """列出最近的入库任务（增量更新审计）。"""
    return ApiResponse.ok(service.list_tasks(db, limit))


@router.get("/ocr/status", response_model=ApiResponse[dict], summary="OCR 能力状态")
def ocr_status(settings: SettingsDep) -> ApiResponse[dict]:
    """查询 Tesseract OCR 是否就绪（图片/扫描件入库依赖）。"""
    from app.ingest.ocr import OcrEngine

    return ApiResponse.ok(OcrEngine(settings).status())
