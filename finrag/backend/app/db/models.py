"""MySQL 结构化数据模型（SQLAlchemy 2.0 Declarative）。

职责边界（对应课题「MySQL 存储结构化数据，Milvus 作为向量数据库」）：
* MySQL 负责制度文档、条文正文、标签、问答历史、收藏、入库任务；
* Milvus 只存 embedding + 可过滤标量字段，检索命中后按 pk 回查本层取正文，
  即「结构化数据与向量数据协同查询」。

类型选择上刻意保持可移植（``String`` 替代 ``ENUM``、``Text`` 替代 ``MEDIUMTEXT``），
使单元测试可以直接落 SQLite 而不改动业务代码。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.constants import BusinessLine, DocStatus, DocType

# SQLite 只有 `INTEGER PRIMARY KEY` 才具备自增语义，`BIGINT PRIMARY KEY` 不会自增。
# 用 `with_variant` 让同一个模型在 MySQL 上仍为 BIGINT、在 SQLite 上退化为 INTEGER，
# 从而可以在不修改业务代码的前提下用 SQLite 跑完整单元测试。
BigIntType = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _sha256(text: str) -> str:
    """计算文本 SHA-256，用于增量入库去重。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TimestampMixin:
    """创建/更新时间戳混入。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )


class KnowledgeDocument(Base, TimestampMixin):
    """金融制度文档（一条制度文件一行）。"""

    __tablename__ = "kb_document"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(128), default="", comment="文号")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="制度名称")
    issuer: Mapped[str] = mapped_column(String(255), default="", comment="发文机关")
    doc_type: Mapped[str] = mapped_column(
        String(32), default=DocType.NORMATIVE.value, comment="文件类型"
    )
    business_line: Mapped[str] = mapped_column(
        String(32), default=BusinessLine.GENERAL.value, comment="业务条线"
    )
    status: Mapped[str] = mapped_column(
        String(16), default=DocStatus.UNKNOWN.value, comment="时效性状态"
    )
    publish_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="发布日期")
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="生效日期")
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="失效日期")

    source_url: Mapped[str] = mapped_column(String(512), default="", comment="来源链接")
    file_name: Mapped[str] = mapped_column(String(255), default="", comment="原始文件名")
    file_type: Mapped[str] = mapped_column(String(16), default="", comment="文件扩展名")
    file_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="文件内容 SHA-256，用于增量去重"
    )
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否经过 OCR")
    parser_name: Mapped[str] = mapped_column(String(64), default="", comment="使用的解析器")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="条文数量")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="修订版本号")
    remark: Mapped[str] = mapped_column(String(512), default="", comment="备注")

    chunks: Mapped[List["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tags: Mapped[List["KnowledgeTag"]] = relationship(
        secondary="kb_document_tag", back_populates="documents", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("file_hash", name="uk_document_hash"),
        Index("idx_document_status", "status"),
        Index("idx_document_business", "business_line"),
        Index("idx_document_effective", "effective_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<KnowledgeDocument id={self.id} title={self.title!r} status={self.status}>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "id": self.id,
            "doc_no": self.doc_no,
            "title": self.title,
            "issuer": self.issuer,
            "doc_type": self.doc_type,
            "business_line": self.business_line,
            "status": self.status,
            "publish_date": self.publish_date.isoformat() if self.publish_date else None,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "source_url": self.source_url,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "ocr_used": bool(self.ocr_used),
            "chunk_count": self.chunk_count,
            "version": self.version,
            "remark": self.remark,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class KnowledgeChunk(Base, TimestampMixin):
    """条文级知识片段（可直接回答的最小知识单元）。"""

    __tablename__ = "kb_chunk"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("kb_document.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, comment="文内顺序号")
    clause_no: Mapped[str] = mapped_column(String(64), default="", comment="条文号，如 第十二条")
    chapter: Mapped[str] = mapped_column(String(255), default="", comment="所属章节")
    article_title: Mapped[str] = mapped_column(String(255), default="", comment="条文小标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="条文正文")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="正文 SHA-256")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0, comment="jieba 分词后词数")

    status: Mapped[str] = mapped_column(
        String(16), default=DocStatus.UNKNOWN.value, comment="冗余时效状态，便于过滤"
    )
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_ts: Mapped[int] = mapped_column(BigIntType, default=0, comment="生效时间戳(秒)")
    expiry_ts: Mapped[int] = mapped_column(BigIntType, default=0, comment="失效时间戳(秒)，0=长期有效")
    business_line: Mapped[str] = mapped_column(String(32), default=BusinessLine.GENERAL.value)
    doc_type: Mapped[str] = mapped_column(String(32), default=DocType.NORMATIVE.value)
    milvus_pk: Mapped[Optional[int]] = mapped_column(
        BigIntType, nullable=True, comment="Milvus 主键，等于本表 id"
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")
    tags: Mapped[List["KnowledgeTag"]] = relationship(
        secondary="kb_chunk_tag", back_populates="chunks", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uk_chunk_doc_hash"),
        Index("idx_chunk_document", "document_id"),
        Index("idx_chunk_status", "status"),
        Index("idx_chunk_business", "business_line"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<KnowledgeChunk id={self.id} clause={self.clause_no!r}>"

    @property
    def tag_names(self) -> List[str]:
        """标签名列表。"""
        return [tag.name for tag in self.tags]

    def to_dict(self, with_content: bool = True) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        data: Dict[str, Any] = {
            "id": self.id,
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "clause_no": self.clause_no,
            "chapter": self.chapter,
            "article_title": self.article_title,
            "char_count": self.char_count,
            "token_count": self.token_count,
            "status": self.status,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "business_line": self.business_line,
            "doc_type": self.doc_type,
            "milvus_pk": self.milvus_pk,
            "tags": self.tag_names,
        }
        if with_content:
            data["content"] = self.content
        return data


class KnowledgeTag(Base, TimestampMixin):
    """知识点标签（支撑「知识点标签分类管理」）。"""

    __tablename__ = "kb_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="标签名")
    category: Mapped[str] = mapped_column(
        String(32), default="主题", comment="标签分类：业务条线/主题/风险类型"
    )
    description: Mapped[str] = mapped_column(String(255), default="")
    ref_count: Mapped[int] = mapped_column(Integer, default=0, comment="引用计数")

    documents: Mapped[List[KnowledgeDocument]] = relationship(
        secondary="kb_document_tag", back_populates="tags"
    )
    chunks: Mapped[List[KnowledgeChunk]] = relationship(
        secondary="kb_chunk_tag", back_populates="tags"
    )

    __table_args__ = (UniqueConstraint("name", "category", name="uk_tag_name_category"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeTag {self.category}/{self.name}>"


class DocumentTag(Base):
    """文档-标签关联表。"""

    __tablename__ = "kb_document_tag"

    document_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("kb_document.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kb_tag.id", ondelete="CASCADE"), primary_key=True
    )


class ChunkTag(Base):
    """条文-标签关联表。"""

    __tablename__ = "kb_chunk_tag"

    chunk_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("kb_chunk.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kb_tag.id", ondelete="CASCADE"), primary_key=True
    )


class QaHistory(Base, TimestampMixin):
    """问答历史记录（支撑「问答历史记录」差异化要求）。"""

    __tablename__ = "qa_history"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    retrieval_mode: Mapped[str] = mapped_column(String(16), default="hybrid")
    top_k: Mapped[int] = mapped_column(Integer, default=5)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    credibility_score: Mapped[float] = mapped_column(Float, default=0.0)
    credibility_label: Mapped[str] = mapped_column(String(16), default="")
    citations: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True, comment="条文溯源列表")
    has_answer: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否给出有效答案")

    __table_args__ = (
        Index("idx_history_user_time", "user_id", "created_at"),
        Index("idx_history_session", "session_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QaHistory id={self.id} q={self.question[:20]!r}>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "question": self.question,
            "answer": self.answer,
            "retrieval_mode": self.retrieval_mode,
            "top_k": self.top_k,
            "evidence_count": self.evidence_count,
            "latency_ms": self.latency_ms,
            "credibility_score": round(float(self.credibility_score or 0.0), 2),
            "credibility_label": self.credibility_label,
            "citations": self.citations or [],
            "has_answer": bool(self.has_answer),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class QaFavorite(Base, TimestampMixin):
    """问答收藏（支撑「收藏功能」差异化要求）。"""

    __tablename__ = "qa_favorite"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    history_id: Mapped[Optional[int]] = mapped_column(
        BigIntType, ForeignKey("qa_history.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(String(512), default="", comment="用户备注")
    citations: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    credibility_score: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        UniqueConstraint("user_id", "history_id", name="uk_favorite_user_history"),
        Index("idx_favorite_user", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QaFavorite id={self.id} user={self.user_id}>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "history_id": self.history_id,
            "question": self.question,
            "answer": self.answer,
            "note": self.note,
            "citations": self.citations or [],
            "credibility_score": round(float(self.credibility_score or 0.0), 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IngestTask(Base, TimestampMixin):
    """知识库增量入库任务（支撑「知识库增量更新」创新点）。"""

    __tablename__ = "kb_ingest_task"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    task_no: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_name: Mapped[str] = mapped_column(String(255), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    document_id: Mapped[Optional[int]] = mapped_column(BigIntType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    stage: Mapped[str] = mapped_column(String(32), default="", comment="当前阶段")
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IngestTask {self.task_no} status={self.status}>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "id": self.id,
            "task_no": self.task_no,
            "file_name": self.file_name,
            "document_id": self.document_id,
            "status": self.status,
            "stage": self.stage,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "ocr_used": bool(self.ocr_used),
            "cost_ms": self.cost_ms,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SysUser(Base, TimestampMixin):
    """系统用户（轻量账号体系，用于历史/收藏的用户隔离）。"""

    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="user", comment="user / admin")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SysUser {self.username}>"


__all__ = [
    "Base",
    "TimestampMixin",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "KnowledgeTag",
    "DocumentTag",
    "ChunkTag",
    "QaHistory",
    "QaFavorite",
    "IngestTask",
    "SysUser",
    "_sha256",
]
