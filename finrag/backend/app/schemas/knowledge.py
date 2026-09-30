"""知识库与文档管理相关请求/响应模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.constants import BusinessLine, DocStatus, DocType


class DocumentOut(BaseModel):
    """制度文档。"""

    id: int
    doc_no: str = ""
    title: str
    issuer: str = ""
    doc_type: str = DocType.OTHER.value
    business_line: str = BusinessLine.GENERAL.value
    status: str = DocStatus.UNKNOWN.value
    publish_date: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    source_url: str = ""
    file_name: str = ""
    file_type: str = ""
    ocr_used: bool = False
    chunk_count: int = 0
    version: int = 1
    remark: str = ""
    tags: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ChunkOut(BaseModel):
    """条文片段。"""

    id: int
    document_id: int
    chunk_index: int = 0
    clause_no: str = ""
    chapter: str = ""
    article_title: str = ""
    content: str = ""
    char_count: int = 0
    token_count: int = 0
    status: str = DocStatus.UNKNOWN.value
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    business_line: str = ""
    doc_type: str = ""
    milvus_pk: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    doc_title: str = ""
    doc_no: str = ""
    source_url: str = ""


class TagOut(BaseModel):
    """标签。"""

    id: int
    name: str
    category: str = "主题"
    description: str = ""
    ref_count: int = 0


class KnowledgeStats(BaseModel):
    """知识库总览。"""

    document_count: int = 0
    chunk_count: int = 0
    tag_count: int = 0
    ocr_document_count: int = 0
    status_counts: Dict[str, int] = Field(default_factory=dict)
    business_line_counts: Dict[str, int] = Field(default_factory=dict)
    latest_updated_at: Optional[str] = None
    vector_count: int = 0
    vector_ready: bool = False
    ocr_status: Dict[str, Any] = Field(default_factory=dict)
    llm_status: Dict[str, Any] = Field(default_factory=dict)
    embedding_status: Dict[str, Any] = Field(default_factory=dict)
    meets_target: Dict[str, Any] = Field(
        default_factory=dict, description="是否满足课题指标（1000+ 知识点等）"
    )


class IngestTextRequest(BaseModel):
    """以文本方式新增知识（便于演示与批量导入）。"""

    text: str = Field(min_length=10, description="制度全文")
    file_name: str = Field(default="inline.txt", max_length=255)
    source_url: str = Field(default="", max_length=512)
    doc_type: str = Field(default="", description="留空则自动推断")
    business_line: str = Field(default="", description="留空则自动推断")
    status: str = Field(default="", description="留空则自动推断")
    replace: bool = Field(default=True, description="同一文件是否覆盖旧版本")
    force: bool = Field(default=False, description="内容未变时是否强制重新入库")


class IngestResultOut(BaseModel):
    """入库结果。"""

    file_name: str = ""
    status: str = ""
    stage: str = ""
    document_id: Optional[int] = None
    task_id: Optional[int] = None
    title: str = ""
    doc_no: str = ""
    issuer: str = ""
    doc_type: str = ""
    business_line: str = ""
    doc_status: str = ""
    chunk_count: int = 0
    vector_count: int = 0
    ocr_used: bool = False
    parser_name: str = ""
    file_hash: str = ""
    cost_ms: int = 0
    message: str = ""
    warnings: List[str] = Field(default_factory=list)


class IngestTaskOut(BaseModel):
    """入库任务。"""

    id: int
    task_no: str = ""
    file_name: str = ""
    document_id: Optional[int] = None
    status: str = ""
    stage: str = ""
    total_chunks: int = 0
    processed_chunks: int = 0
    ocr_used: bool = False
    cost_ms: int = 0
    message: str = ""
    created_at: Optional[str] = None


class DocumentUpdateRequest(BaseModel):
    """人工修订文档元数据（如人工确认时效状态）。"""

    title: Optional[str] = Field(default=None, max_length=512)
    doc_no: Optional[str] = Field(default=None, max_length=128)
    issuer: Optional[str] = Field(default=None, max_length=255)
    doc_type: Optional[str] = None
    business_line: Optional[str] = None
    status: Optional[str] = None
    publish_date: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    source_url: Optional[str] = Field(default=None, max_length=512)
    remark: Optional[str] = Field(default=None, max_length=512)


__all__ = [
    "DocumentOut",
    "ChunkOut",
    "TagOut",
    "KnowledgeStats",
    "IngestTextRequest",
    "IngestResultOut",
    "IngestTaskOut",
    "DocumentUpdateRequest",
]
