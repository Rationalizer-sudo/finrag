"""知识库服务：文档管理、增量入库、知识库总览。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.constants import BusinessLine, DocStatus, DocType
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging_conf import get_logger
from app.db import repository
from app.db.models import IngestTask, KnowledgeChunk, KnowledgeDocument, KnowledgeTag
from app.ingest.ocr import OcrEngine
from app.ingest.pipeline import IngestPipeline, IngestResult
from app.schemas.knowledge import (
    ChunkOut,
    DocumentOut,
    DocumentUpdateRequest,
    IngestTaskOut,
    IngestTextRequest,
    TagOut,
)
from app.services.container import Container
from app.services.storage import remove_file, save_upload

logger = get_logger(__name__)

# 课题核心指标
TARGET_KNOWLEDGE_POINTS = 1000
TARGET_RESPONSE_MS = 2000
TARGET_ACCURACY = 0.85

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y%m%d")


def parse_date(value: Optional[str]) -> Optional[date]:
    """宽松解析日期字符串；空串返回 ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise BadRequestError(f"日期格式无法识别：{value}（建议 YYYY-MM-DD）")


def document_to_out(document: KnowledgeDocument) -> DocumentOut:
    """ORM 文档 -> 响应模型。"""
    data = document.to_dict()
    data["tags"] = [tag.name for tag in (document.tags or [])]
    return DocumentOut(**data)


def chunk_to_out(chunk: KnowledgeChunk) -> ChunkOut:
    """ORM 条文 -> 响应模型。"""
    data = chunk.to_dict(with_content=True)
    data["doc_title"] = getattr(chunk.document, "title", "") or ""
    data["doc_no"] = getattr(chunk.document, "doc_no", "") or ""
    data["source_url"] = getattr(chunk.document, "source_url", "") or ""
    return ChunkOut(**data)


def task_to_out(task: IngestTask) -> IngestTaskOut:
    """ORM 任务 -> 响应模型。"""
    return IngestTaskOut(**task.to_dict())


def tag_to_out(tag: KnowledgeTag) -> TagOut:
    """ORM 标签 -> 响应模型。"""
    return TagOut(
        id=tag.id,
        name=tag.name,
        category=tag.category or "主题",
        description=tag.description or "",
        ref_count=tag.ref_count or 0,
    )


class KnowledgeBaseService:
    """知识库业务服务。"""

    def __init__(self, container: Container, settings: Optional[Settings] = None) -> None:
        self.container = container
        self.settings: Settings = settings or container.settings

    # ==================================================================
    #  入库
    # ==================================================================
    @property
    def pipeline(self) -> IngestPipeline:
        """入库流水线。"""
        return self.container.ingest_pipeline

    def ingest_text(self, db: Session, payload: IngestTextRequest) -> IngestResult:
        """以文本形式入库一条制度。"""
        return self.pipeline.ingest_text(
            db,
            payload.text,
            payload.file_name or "inline.txt",
            source_url=payload.source_url,
            replace=payload.replace,
            force=payload.force,
            doc_type=payload.doc_type,
            business_line=payload.business_line,
            status=payload.status,
        )

    def ingest_file(
        self,
        db: Session,
        path: str | Path,
        *,
        source_url: str = "",
        replace: bool = True,
        force: bool = False,
    ) -> IngestResult:
        """以文件形式入库。"""
        return self.pipeline.ingest_file(
            db, path, source_url=source_url, replace=replace, force=force
        )

    def ingest_upload(
        self,
        db: Session,
        stream: BinaryIO,
        filename: str,
        size: int = 0,
        *,
        source_url: str = "",
        replace: bool = True,
        force: bool = False,
        keep_file: bool = True,
    ) -> Tuple[IngestResult, Dict[str, Any]]:
        """处理上传文件并入库。

        :param keep_file: 入库后是否保留落盘文件（关闭则自动清理）。
        :returns: ``(入库结果, 文件元信息)``
        """
        path, file_info = save_upload(stream, filename, size, self.settings)
        try:
            result = self.ingest_file(
                db, path, source_url=source_url, replace=replace, force=force
            )
        except Exception:
            if not keep_file:
                remove_file(path)
            raise
        if not keep_file:
            remove_file(path)
        return result, file_info

    def rebuild_vectors(self, db: Session) -> Dict[str, Any]:
        """重建全部向量索引。"""
        return self.pipeline.rebuild_vectors(db)

    # ==================================================================
    #  文档
    # ==================================================================
    def list_documents(
        self,
        db: Session,
        *,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        business_line: Optional[str] = None,
        doc_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[DocumentOut], int]:
        """分页列出制度文档。"""
        documents, total = repository.list_documents(
            db,
            keyword=keyword,
            status=status,
            business_line=business_line,
            doc_type=doc_type,
            limit=limit,
            offset=offset,
        )
        return [document_to_out(item) for item in documents], total

    def get_document(self, db: Session, document_id: int) -> DocumentOut:
        """获取单个文档，不存在抛 404。"""
        document = repository.get_document(db, document_id)
        if document is None:
            raise NotFoundError(f"文档 {document_id} 不存在")
        return document_to_out(document)

    def update_document(
        self, db: Session, document_id: int, payload: DocumentUpdateRequest
    ) -> DocumentOut:
        """人工修订文档元数据（人工校核优先于自动抽取）。"""
        document = repository.get_document(db, document_id)
        if document is None:
            raise NotFoundError(f"文档 {document_id} 不存在")

        fields: Dict[str, Any] = {}
        for name in ("title", "doc_no", "issuer", "doc_type", "business_line",
                     "status", "source_url", "remark"):
            value = getattr(payload, name)
            if value is not None:
                fields[name] = value
        for name in ("publish_date", "effective_date", "expiry_date"):
            value = getattr(payload, name)
            if value is not None:
                fields[name] = parse_date(value)

        if fields.get("status") and fields["status"] not in DocStatus.values():
            raise BadRequestError(
                f"无效的时效状态：{fields['status']}，可选：{DocStatus.values()}"
            )
        if fields.get("doc_type") and fields["doc_type"] not in DocType.values():
            raise BadRequestError(f"无效的文件类型：{fields['doc_type']}")
        if fields.get("business_line") and fields["business_line"] not in BusinessLine.values():
            raise BadRequestError(f"无效的业务条线：{fields['business_line']}")

        repository.update_document(db, document, **fields)

        # 时效/状态变更需同步到条文与向量库，否则检索过滤会用到旧值
        if {"status", "effective_date", "expiry_date"} & set(fields):
            self._sync_chunk_validity(db, document, fields)
        db.commit()
        logger.info("文档 %s 元数据已更新：%s", document_id, list(fields))
        return document_to_out(document)

    def _sync_chunk_validity(
        self, db: Session, document: KnowledgeDocument, fields: Dict[str, Any]
    ) -> None:
        """把文档的时效字段同步到条文表与向量库。"""
        from app.ingest.metadata import effective_timestamp

        chunks, _ = repository.list_chunks(db, document_id=document.id, limit=100000)
        for chunk in chunks:
            if "status" in fields:
                chunk.status = document.status
            chunk.effective_date = document.effective_date
            chunk.expiry_date = document.expiry_date
            chunk.effective_ts = effective_timestamp(document.effective_date)
            chunk.expiry_ts = effective_timestamp(document.expiry_date)
        db.flush()
        logger.info("文档 %s 的 %d 条条文时效字段已同步", document.id, len(chunks))

    def delete_document(self, db: Session, document_id: int) -> Dict[str, Any]:
        """删除文档及其条文与向量。"""
        result = self.pipeline.remove_document(db, document_id)
        if not result.get("deleted"):
            raise NotFoundError(f"文档 {document_id} 不存在")
        db.commit()
        return result

    # ==================================================================
    #  条文 / 标签
    # ==================================================================
    def list_chunks(
        self,
        db: Session,
        *,
        document_id: Optional[int] = None,
        keyword: Optional[str] = None,
        only_valid: bool = False,
        business_line: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[ChunkOut], int]:
        """分页列出条文。"""
        from app.retrieval.types import RetrievalFilters

        filters = None
        if only_valid or business_line:
            filters = RetrievalFilters(
                only_valid=only_valid,
                business_lines=[business_line] if business_line else [],
            )
        chunks, total = repository.list_chunks(
            db,
            filters=filters,
            document_id=document_id,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        return [chunk_to_out(item) for item in chunks], total

    def get_chunk(self, db: Session, chunk_id: int) -> ChunkOut:
        """获取单条条文（作为溯源链接的落地页数据）。"""
        chunk = repository.get_chunk(db, chunk_id)
        if chunk is None:
            raise NotFoundError(f"条文 {chunk_id} 不存在")
        return chunk_to_out(chunk)

    def list_tags(
        self, db: Session, category: Optional[str] = None, limit: int = 100
    ) -> List[TagOut]:
        """列出知识点标签。"""
        return [tag_to_out(item) for item in repository.list_tags(db, category, limit)]

    def list_tasks(self, db: Session, limit: int = 20) -> List[IngestTaskOut]:
        """列出最近的入库任务。"""
        return [task_to_out(item) for item in repository.list_ingest_tasks(db, limit)]

    # ==================================================================
    #  总览
    # ==================================================================
    def stats(self, db: Session) -> Dict[str, Any]:
        """知识库总览统计与达标情况。"""
        base = repository.knowledge_base_stats(db)
        ocr = OcrEngine(self.settings).status()
        component_status = self.container.status()

        vector_count = 0
        vector_ready = False
        vector_error = ""
        try:
            health = self.container.vector_store.health()
            vector_count = int(health.get("vector_count", 0) or 0)
            vector_ready = bool(health.get("connected") and health.get("collection_exists"))
            vector_error = str(health.get("error", "") or "")
        except Exception as exc:  # pragma: no cover - 需要真实 Milvus
            vector_error = str(exc)

        base.update(
            {
                "vector_count": vector_count,
                "vector_ready": vector_ready,
                "vector_error": vector_error,
                "ocr_status": ocr,
                "llm_status": component_status.get("llm", {}),
                "embedding_status": component_status.get("embedding", {}),
                "meets_target": {
                    "knowledge_points_target": TARGET_KNOWLEDGE_POINTS,
                    "knowledge_points_actual": base.get("chunk_count", 0),
                    "knowledge_points_ok": base.get("chunk_count", 0)
                    >= TARGET_KNOWLEDGE_POINTS,
                    "response_time_target_ms": TARGET_RESPONSE_MS,
                    "accuracy_target": TARGET_ACCURACY,
                    "vector_index_ok": vector_ready,
                },
            }
        )
        return base


__all__ = [
    "KnowledgeBaseService",
    "document_to_out",
    "chunk_to_out",
    "task_to_out",
    "tag_to_out",
    "parse_date",
    "TARGET_KNOWLEDGE_POINTS",
]
