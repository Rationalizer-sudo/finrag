"""MySQL 数据访问层（Repository）。

把 SQLAlchemy 查询集中在此，业务层只调用语义化函数，
便于单测（可直接对 SQLite 跑）与后续替换存储实现。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.constants import DocStatus
from app.core.logging_conf import get_logger
from app.db.models import (
    ChunkTag,
    DocumentTag,
    IngestTask,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeTag,
    QaFavorite,
    QaHistory,
)
from app.retrieval.types import RetrievalFilters

logger = get_logger(__name__)


def _now_ts() -> int:
    """当前秒级时间戳。"""
    return int(time.time())


# ===========================================================================
#  文档
# ===========================================================================
def create_document(db: Session, **fields: Any) -> KnowledgeDocument:
    """新建制度文档记录。"""
    document = KnowledgeDocument(**fields)
    db.add(document)
    db.flush()
    return document


def get_document(db: Session, document_id: int) -> Optional[KnowledgeDocument]:
    """按主键取文档。"""
    return db.get(KnowledgeDocument, document_id)


def get_document_by_hash(db: Session, file_hash: str) -> Optional[KnowledgeDocument]:
    """按文件哈希取文档（增量入库判重）。"""
    if not file_hash:
        return None
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.file_hash == file_hash)
    return db.execute(stmt).scalars().first()


def get_document_by_file_name(db: Session, file_name: str) -> Optional[KnowledgeDocument]:
    """按原始文件名取文档（识别「同一制度的新版本」）。

    制度修订后重新上传时文件内容哈希会变化，但文件名通常不变。
    据此把新上传识别为同一文档的新版本，避免知识库里出现两份同名制度。
    """
    if not file_name:
        return None
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.file_name == file_name)
        .order_by(KnowledgeDocument.id.desc())
    )
    return db.execute(stmt).scalars().first()


def update_document(db: Session, document: KnowledgeDocument, **fields: Any) -> KnowledgeDocument:
    """更新文档字段。"""
    for key, value in fields.items():
        if hasattr(document, key):
            setattr(document, key, value)
    db.flush()
    return document


def delete_document(db: Session, document_id: int) -> bool:
    """删除文档（级联删除条文与关联）。"""
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        return False
    db.delete(document)
    db.flush()
    return True


def list_documents(
    db: Session,
    *,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    business_line: Optional[str] = None,
    doc_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[KnowledgeDocument], int]:
    """分页查询文档，返回 ``(列表, 总数)``。"""
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                KnowledgeDocument.title.like(like),
                KnowledgeDocument.doc_no.like(like),
                KnowledgeDocument.issuer.like(like),
            )
        )
    if status:
        conditions.append(KnowledgeDocument.status == status)
    if business_line:
        conditions.append(KnowledgeDocument.business_line == business_line)
    if doc_type:
        conditions.append(KnowledgeDocument.doc_type == doc_type)

    count_stmt = select(func.count()).select_from(KnowledgeDocument)
    stmt: Select = select(KnowledgeDocument).options(selectinload(KnowledgeDocument.tags))
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(db.execute(count_stmt).scalar() or 0)
    stmt = stmt.order_by(KnowledgeDocument.id.desc()).limit(limit).offset(offset)
    items = list(db.execute(stmt).scalars().all())
    return items, total


def document_status_counts(db: Session) -> Dict[str, int]:
    """按状态统计文档数（知识库总览用）。"""
    stmt = select(KnowledgeDocument.status, func.count()).group_by(KnowledgeDocument.status)
    return {str(row[0] or DocStatus.UNKNOWN.value): int(row[1]) for row in db.execute(stmt)}


def document_business_counts(db: Session) -> Dict[str, int]:
    """按业务条线统计文档数。"""
    stmt = select(
        KnowledgeDocument.business_line, func.count()
    ).group_by(KnowledgeDocument.business_line)
    return {str(row[0] or ""): int(row[1]) for row in db.execute(stmt)}


# ===========================================================================
#  条文
# ===========================================================================
def bulk_insert_chunks(db: Session, chunks: Sequence[KnowledgeChunk]) -> List[KnowledgeChunk]:
    """批量插入条文。"""
    if not chunks:
        return []
    db.add_all(list(chunks))
    db.flush()
    return list(chunks)


def get_chunk(db: Session, chunk_id: int) -> Optional[KnowledgeChunk]:
    """按主键取条文（带文档与标签）。"""
    stmt = (
        select(KnowledgeChunk)
        .options(
            selectinload(KnowledgeChunk.tags),
            selectinload(KnowledgeChunk.document).selectinload(KnowledgeDocument.tags),
        )
        .where(KnowledgeChunk.id == chunk_id)
    )
    return db.execute(stmt).scalars().first()


def get_chunks_by_ids(db: Session, chunk_ids: Sequence[int]) -> Dict[int, KnowledgeChunk]:
    """批量取条文，返回 ``{chunk_id: chunk}``（用于向量检索后回查正文）。"""
    ids = [int(i) for i in chunk_ids]
    if not ids:
        return {}
    stmt = (
        select(KnowledgeChunk)
        .options(
            selectinload(KnowledgeChunk.tags),
            selectinload(KnowledgeChunk.document).selectinload(KnowledgeDocument.tags),
        )
        .where(KnowledgeChunk.id.in_(ids))
    )
    return {chunk.id: chunk for chunk in db.execute(stmt).scalars().all()}


def apply_chunk_filters(stmt: Select, filters: Optional[RetrievalFilters]) -> Select:
    """把检索过滤条件翻译为 SQL 条件（与 Milvus 侧过滤口径保持一致）。"""
    if filters is None:
        return stmt

    if filters.only_valid and not filters.statuses:
        stmt = stmt.where(KnowledgeChunk.status.in_(DocStatus.valid_values()))
    if filters.statuses:
        stmt = stmt.where(KnowledgeChunk.status.in_(list(filters.statuses)))
    if filters.business_lines:
        stmt = stmt.where(KnowledgeChunk.business_line.in_(list(filters.business_lines)))
    if filters.doc_types:
        stmt = stmt.where(KnowledgeChunk.doc_type.in_(list(filters.doc_types)))
    if filters.document_ids:
        stmt = stmt.where(KnowledgeChunk.document_id.in_([int(i) for i in filters.document_ids]))

    moment = int(filters.as_of) if filters.as_of else _now_ts()
    if filters.only_valid or filters.as_of is not None:
        stmt = stmt.where(
            or_(KnowledgeChunk.effective_ts == 0, KnowledgeChunk.effective_ts <= moment)
        )
    if filters.exclude_expired:
        stmt = stmt.where(
            or_(KnowledgeChunk.expiry_ts == 0, KnowledgeChunk.expiry_ts >= moment)
        )
    return stmt


def list_chunks(
    db: Session,
    *,
    filters: Optional[RetrievalFilters] = None,
    document_id: Optional[int] = None,
    keyword: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[KnowledgeChunk], int]:
    """分页查询条文，返回 ``(列表, 总数)``。"""
    conditions = []
    if document_id is not None:
        conditions.append(KnowledgeChunk.document_id == int(document_id))
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(KnowledgeChunk.content.like(like), KnowledgeChunk.clause_no.like(like))
        )

    base = select(KnowledgeChunk)
    count_stmt = select(func.count()).select_from(KnowledgeChunk)
    for condition in conditions:
        base = base.where(condition)
        count_stmt = count_stmt.where(condition)
    base = apply_chunk_filters(base, filters)
    count_stmt = apply_chunk_filters(count_stmt, filters)

    total = int(db.execute(count_stmt).scalar() or 0)
    stmt = (
        base.options(selectinload(KnowledgeChunk.tags))
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all()), total


def count_chunks(db: Session, filters: Optional[RetrievalFilters] = None) -> int:
    """统计条文数量。"""
    stmt = apply_chunk_filters(select(func.count()).select_from(KnowledgeChunk), filters)
    return int(db.execute(stmt).scalar() or 0)


def iter_chunk_texts(
    db: Session,
    filters: Optional[RetrievalFilters] = None,
    limit: Optional[int] = None,
) -> List[Tuple[int, str]]:
    """返回 ``[(chunk_id, content), ...]``，用于构建 BM25 内存索引。"""
    stmt = apply_chunk_filters(
        select(KnowledgeChunk.id, KnowledgeChunk.content), filters
    ).order_by(KnowledgeChunk.id)
    if limit:
        stmt = stmt.limit(int(limit))
    return [(int(row[0]), row[1] or "") for row in db.execute(stmt).all()]


def delete_chunks_by_document(db: Session, document_id: int) -> int:
    """删除某文档的全部条文，返回删除条数。"""
    ids = list(
        db.execute(
            select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == int(document_id))
        ).scalars().all()
    )
    if not ids:
        return 0
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == int(document_id)))
    db.flush()
    return len(ids)


def chunk_tag_names(db: Session, chunk_id: int) -> List[str]:
    """查询条文的标签名。"""
    stmt = (
        select(KnowledgeTag.name)
        .join(ChunkTag, ChunkTag.tag_id == KnowledgeTag.id)
        .where(ChunkTag.chunk_id == int(chunk_id))
    )
    return [str(row[0]) for row in db.execute(stmt).all()]


# ===========================================================================
#  标签
# ===========================================================================
def get_or_create_tag(db: Session, name: str, category: str = "主题") -> Optional[KnowledgeTag]:
    """按 (名称, 分类) 取标签，不存在则创建。"""
    clean = (name or "").strip()
    if not clean:
        return None
    stmt = select(KnowledgeTag).where(
        KnowledgeTag.name == clean, KnowledgeTag.category == category
    )
    tag = db.execute(stmt).scalars().first()
    if tag is None:
        tag = KnowledgeTag(name=clean[:64], category=category[:32])
        db.add(tag)
        db.flush()
    return tag


def attach_tags_to_chunk(db: Session, chunk: KnowledgeChunk, names: Sequence[str]) -> None:
    """给条文打标签（幂等）。"""
    existing = {tag.name for tag in chunk.tags}
    for name in names:
        clean = (name or "").strip()
        if not clean or clean in existing:
            continue
        tag = get_or_create_tag(db, clean)
        if tag is None:
            continue
        chunk.tags.append(tag)
        tag.ref_count = (tag.ref_count or 0) + 1
        existing.add(clean)
    db.flush()


def attach_tags_to_document(db: Session, document: KnowledgeDocument, names: Sequence[str]) -> None:
    """给文档打标签（幂等）。"""
    existing = {tag.name for tag in document.tags}
    for name in names:
        clean = (name or "").strip()
        if not clean or clean in existing:
            continue
        tag = get_or_create_tag(db, clean)
        if tag is None:
            continue
        document.tags.append(tag)
        existing.add(clean)
    db.flush()


def list_tags(db: Session, category: Optional[str] = None, limit: int = 100) -> List[KnowledgeTag]:
    """列出标签。"""
    stmt = select(KnowledgeTag)
    if category:
        stmt = stmt.where(KnowledgeTag.category == category)
    stmt = stmt.order_by(KnowledgeTag.ref_count.desc(), KnowledgeTag.id).limit(limit)
    return list(db.execute(stmt).scalars().all())


def tag_names_of_document(db: Session, document_id: int) -> List[str]:
    """查询文档的标签名。"""
    stmt = (
        select(KnowledgeTag.name)
        .join(DocumentTag, DocumentTag.tag_id == KnowledgeTag.id)
        .where(DocumentTag.document_id == int(document_id))
    )
    return [str(row[0]) for row in db.execute(stmt).all()]


# ===========================================================================
#  入库任务
# ===========================================================================
def create_ingest_task(db: Session, **fields: Any) -> IngestTask:
    """创建入库任务记录。"""
    task = IngestTask(**fields)
    db.add(task)
    db.flush()
    return task


def update_ingest_task(db: Session, task: IngestTask, **fields: Any) -> IngestTask:
    """更新入库任务。"""
    for key, value in fields.items():
        if hasattr(task, key):
            setattr(task, key, value)
    db.flush()
    return task


def list_ingest_tasks(db: Session, limit: int = 20) -> List[IngestTask]:
    """列出最近的入库任务。"""
    stmt = select(IngestTask).order_by(IngestTask.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


# ===========================================================================
#  问答历史
# ===========================================================================
def create_history(db: Session, **fields: Any) -> QaHistory:
    """写入一条问答历史。"""
    record = QaHistory(**fields)
    db.add(record)
    db.flush()
    return record


def get_history(db: Session, history_id: int) -> Optional[QaHistory]:
    """按主键取问答历史。"""
    return db.get(QaHistory, history_id)


def list_history(
    db: Session,
    user_id: str,
    *,
    keyword: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[QaHistory], int]:
    """分页查询用户的问答历史。"""
    conditions = [QaHistory.user_id == user_id]
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(QaHistory.question.like(like), QaHistory.answer.like(like)))

    count_stmt = select(func.count()).select_from(QaHistory)
    stmt = select(QaHistory)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(db.execute(count_stmt).scalar() or 0)
    stmt = stmt.order_by(QaHistory.id.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all()), total


def delete_history(db: Session, history_id: int, user_id: Optional[str] = None) -> bool:
    """删除一条历史（可限定归属用户，避免越权删除）。"""
    record = db.get(QaHistory, history_id)
    if record is None:
        return False
    if user_id and record.user_id != user_id:
        return False
    db.delete(record)
    db.flush()
    return True


def clear_history(db: Session, user_id: str) -> int:
    """清空某用户全部历史，返回删除条数。"""
    result = db.execute(delete(QaHistory).where(QaHistory.user_id == user_id))
    db.flush()
    return int(result.rowcount or 0)


# ===========================================================================
#  收藏
# ===========================================================================
def create_favorite(db: Session, **fields: Any) -> QaFavorite:
    """新建收藏。"""
    record = QaFavorite(**fields)
    db.add(record)
    db.flush()
    return record


def get_favorite(db: Session, favorite_id: int) -> Optional[QaFavorite]:
    """按主键取收藏。"""
    return db.get(QaFavorite, favorite_id)


def find_favorite_by_history(
    db: Session, user_id: str, history_id: int
) -> Optional[QaFavorite]:
    """按 (用户, 历史) 查收藏，用于判断是否已收藏。"""
    stmt = select(QaFavorite).where(
        QaFavorite.user_id == user_id, QaFavorite.history_id == int(history_id)
    )
    return db.execute(stmt).scalars().first()


def list_favorites(
    db: Session,
    user_id: str,
    *,
    keyword: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[QaFavorite], int]:
    """分页查询用户收藏。"""
    conditions = [QaFavorite.user_id == user_id]
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(QaFavorite.question.like(like), QaFavorite.note.like(like)))

    count_stmt = select(func.count()).select_from(QaFavorite)
    stmt = select(QaFavorite)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(db.execute(count_stmt).scalar() or 0)
    stmt = stmt.order_by(QaFavorite.id.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all()), total


def delete_favorite(db: Session, favorite_id: int, user_id: Optional[str] = None) -> bool:
    """删除收藏。"""
    record = db.get(QaFavorite, favorite_id)
    if record is None:
        return False
    if user_id and record.user_id != user_id:
        return False
    db.delete(record)
    db.flush()
    return True


# ===========================================================================
#  总览统计
# ===========================================================================
def knowledge_base_stats(db: Session) -> Dict[str, Any]:
    """知识库总览统计。"""
    total_documents = int(
        db.execute(select(func.count()).select_from(KnowledgeDocument)).scalar() or 0
    )
    total_chunks = int(db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar() or 0)
    total_tags = int(db.execute(select(func.count()).select_from(KnowledgeTag)).scalar() or 0)
    ocr_documents = int(
        db.execute(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.ocr_used.is_(True)
            )
        ).scalar()
        or 0
    )
    latest = db.execute(
        select(func.max(KnowledgeDocument.updated_at))
    ).scalar()
    return {
        "document_count": total_documents,
        "chunk_count": total_chunks,
        "tag_count": total_tags,
        "ocr_document_count": ocr_documents,
        "status_counts": document_status_counts(db),
        "business_line_counts": document_business_counts(db),
        "latest_updated_at": latest.isoformat() if isinstance(latest, datetime) else None,
    }


__all__ = [
    "create_document",
    "get_document",
    "get_document_by_hash",
    "get_document_by_file_name",
    "update_document",
    "delete_document",
    "list_documents",
    "document_status_counts",
    "document_business_counts",
    "bulk_insert_chunks",
    "get_chunk",
    "get_chunks_by_ids",
    "list_chunks",
    "apply_chunk_filters",
    "count_chunks",
    "iter_chunk_texts",
    "delete_chunks_by_document",
    "chunk_tag_names",
    "get_or_create_tag",
    "attach_tags_to_chunk",
    "attach_tags_to_document",
    "list_tags",
    "tag_names_of_document",
    "create_ingest_task",
    "update_ingest_task",
    "list_ingest_tasks",
    "create_history",
    "get_history",
    "list_history",
    "delete_history",
    "clear_history",
    "create_favorite",
    "get_favorite",
    "find_favorite_by_history",
    "list_favorites",
    "delete_favorite",
    "knowledge_base_stats",
]
