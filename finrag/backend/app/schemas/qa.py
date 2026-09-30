"""问答相关请求/响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.constants import BusinessLine, DocStatus, DocType, RetrievalMode


class AskRequest(BaseModel):
    """提问请求。"""

    question: str = Field(min_length=1, max_length=500, description="用户问题")
    top_k: int = Field(default=5, ge=1, le=20, description="返回的证据条数")
    retrieval_mode: Literal["vector", "bm25", "hybrid"] = Field(
        default=RetrievalMode.HYBRID.value, description="检索模式"
    )
    only_valid: bool = Field(default=True, description="仅检索现行有效条文")
    exclude_expired: bool = Field(default=True, description="过滤已过时条文")
    statuses: List[str] = Field(default_factory=list, description="按时效状态过滤")
    business_lines: List[str] = Field(default_factory=list, description="按业务条线过滤")
    doc_types: List[str] = Field(default_factory=list, description="按文件类型过滤")
    document_ids: List[int] = Field(default_factory=list, description="限定文档范围")
    as_of: Optional[date] = Field(default=None, description="以指定日期判断时效性")
    user_id: str = Field(default="anonymous", max_length=64, description="用户标识")
    session_id: str = Field(default="", max_length=64, description="会话标识")
    save_history: bool = Field(default=True, description="是否保存问答历史")
    include_all_citations: bool = Field(
        default=False, description="是否连未引用的召回条文一并返回"
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("问题不能为空")
        return text

    @field_validator("statuses")
    @classmethod
    def _validate_statuses(cls, value: List[str]) -> List[str]:
        allowed = set(DocStatus.values())
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError(f"无效的时效状态：{invalid}，可选：{sorted(allowed)}")
        return value

    @field_validator("business_lines")
    @classmethod
    def _validate_business_lines(cls, value: List[str]) -> List[str]:
        allowed = set(BusinessLine.values())
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError(f"无效的业务条线：{invalid}")
        return value

    @field_validator("doc_types")
    @classmethod
    def _validate_doc_types(cls, value: List[str]) -> List[str]:
        allowed = set(DocType.values())
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError(f"无效的文件类型：{invalid}")
        return value


class Citation(BaseModel):
    """条文溯源对象。"""

    index: int = Field(description="引用序号，与回答中的 [n] 对应")
    chunk_id: int
    document_id: int
    doc_title: str = ""
    doc_no: str = ""
    issuer: str = ""
    clause_no: str = ""
    chapter: str = ""
    article_title: str = ""
    content: str = ""
    status: str = ""
    is_valid: bool = True
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    business_line: str = ""
    doc_type: str = ""
    tags: List[str] = Field(default_factory=list)
    source_url: str = ""
    source_link: str = ""
    score: float = 0.0
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    citation_label: str = ""


class Credibility(BaseModel):
    """答案可信度评分。"""

    score: float = Field(description="0~100 综合得分")
    label: str = Field(description="高 / 中 / 低 / 不可信")
    components: Dict[str, float] = Field(default_factory=dict, description="各维度得分")
    reasons: List[str] = Field(default_factory=list, description="扣分说明")


class RetrievalTrace(BaseModel):
    """检索链路的可观测信息（用于性能分析与答辩演示）。"""

    retrieval_ms: int = 0
    generation_ms: int = 0
    total_ms: int = 0
    candidate_k: int = 0
    evidence_count: int = 0
    vector_used: bool = False
    bm25_used: bool = False
    context_chars: int = 0


class AskResponse(BaseModel):
    """提问响应。"""

    question: str
    answer: str
    has_answer: bool = Field(description="是否基于知识库条文作答")
    citations: List[Citation] = Field(default_factory=list)
    credibility: Credibility
    evidence_count: int = 0
    retrieval_mode: str = RetrievalMode.HYBRID.value
    top_k: int = 5
    latency_ms: int = Field(default=0, description="端到端耗时（毫秒）")
    within_budget: bool = Field(default=True, description="是否满足 ≤2s 响应要求")
    history_id: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)


class HistoryItem(BaseModel):
    """历史记录条目。"""

    id: int
    session_id: str = ""
    user_id: str = "anonymous"
    question: str
    answer: str = ""
    retrieval_mode: str = ""
    top_k: int = 0
    evidence_count: int = 0
    latency_ms: int = 0
    credibility_score: float = 0.0
    credibility_label: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    has_answer: bool = True
    created_at: Optional[str] = None
    favorited: bool = False


class FavoriteCreateRequest(BaseModel):
    """新增收藏请求。"""

    history_id: Optional[int] = Field(default=None, description="来源历史记录 ID")
    question: str = Field(default="", max_length=500)
    answer: str = Field(default="")
    note: str = Field(default="", max_length=512)
    user_id: str = Field(default="anonymous", max_length=64)


class FavoriteUpdateRequest(BaseModel):
    """更新收藏备注。"""

    note: str = Field(default="", max_length=512)


class FavoriteItem(BaseModel):
    """收藏条目。"""

    id: int
    user_id: str
    history_id: Optional[int] = None
    question: str
    answer: str = ""
    note: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    credibility_score: float = 0.0
    created_at: Optional[str] = None


__all__ = [
    "AskRequest",
    "AskResponse",
    "Citation",
    "Credibility",
    "RetrievalTrace",
    "HistoryItem",
    "FavoriteCreateRequest",
    "FavoriteUpdateRequest",
    "FavoriteItem",
]
