"""检索结果的统一数据结构。

向量检索、BM25 检索、混合检索三条链路最终都归一到
:class:`RetrievedClause`，由它承载「条文溯源」所需的全部字段，
并可转换为 LangChain ``Document`` 供生成链使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.constants import DocStatus


@dataclass
class RetrievedClause:
    """一条被召回的条文（含分数与溯源元数据）。"""

    chunk_id: int
    document_id: int
    content: str
    score: float = 0.0
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    vector_rank: int = 0
    bm25_rank: int = 0
    rank: int = 0

    clause_no: str = ""
    chapter: str = ""
    article_title: str = ""
    status: str = DocStatus.UNKNOWN.value
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    business_line: str = ""
    doc_type: str = ""
    doc_title: str = ""
    doc_no: str = ""
    issuer: str = ""
    source_url: str = ""
    tags: List[str] = field(default_factory=list)
    matched_terms: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """是否为现行有效（或尚未生效但已发布）的条文。"""
        return self.status in DocStatus.valid_values()

    @property
    def citation_label(self) -> str:
        """人类可读的条文引用标签，如 ``《办法》第十二条``。"""
        parts: List[str] = []
        if self.doc_title:
            parts.append(f"《{self.doc_title}》")
        if self.clause_no:
            parts.append(self.clause_no)
        return "".join(parts) or f"条文#{self.chunk_id}"

    @property
    def source_link(self) -> str:
        """条文溯源链接：优先官方来源，其次回退到系统内条文详情页。"""
        if self.source_url:
            return self.source_url
        return f"/api/v1/knowledge/chunks/{self.chunk_id}"

    def to_citation(self, index: int = 1) -> Dict[str, Any]:
        """转换为前端展示 / 历史记录持久化的引用对象。"""
        return {
            "index": index,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "doc_title": self.doc_title,
            "doc_no": self.doc_no,
            "issuer": self.issuer,
            "clause_no": self.clause_no,
            "chapter": self.chapter,
            "article_title": self.article_title,
            "content": self.content,
            "status": self.status,
            "is_valid": self.is_valid,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "business_line": self.business_line,
            "doc_type": self.doc_type,
            "tags": list(self.tags),
            "source_url": self.source_url,
            "source_link": self.source_link,
            "score": round(float(self.score), 6),
            "vector_score": round(float(self.vector_score), 6),
            "bm25_score": round(float(self.bm25_score), 6),
            "rrf_score": round(float(self.rrf_score), 6),
            "citation_label": self.citation_label,
        }

    def to_dict(self) -> Dict[str, Any]:
        """同 :meth:`to_citation`（默认序号 1）。"""
        return self.to_citation(1)

    def to_langchain_document(self) -> Any:
        """转换为 LangChain ``Document``，供生成链作为上下文使用。

        ``page_content`` 带上制度名与条文号，让模型能自然地在回答中引用；
        ``metadata`` 保留 chunk_id 等字段，便于流式输出后回填引用。
        """
        from langchain_core.documents import Document

        prefix_parts: List[str] = []
        if self.doc_title:
            prefix_parts.append(f"《{self.doc_title}》")
        if self.doc_no:
            prefix_parts.append(f"（{self.doc_no}）")
        if self.clause_no:
            prefix_parts.append(f"{self.clause_no}")
        header = "".join(prefix_parts)
        page_content = f"{header}\n{self.content}" if header else self.content

        return Document(
            page_content=page_content,
            metadata={
                "chunk_id": self.chunk_id,
                "document_id": self.document_id,
                "doc_title": self.doc_title,
                "doc_no": self.doc_no,
                "issuer": self.issuer,
                "clause_no": self.clause_no,
                "chapter": self.chapter,
                "article_title": self.article_title,
                "status": self.status,
                "effective_date": self.effective_date.isoformat() if self.effective_date else None,
                "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
                "business_line": self.business_line,
                "doc_type": self.doc_type,
                "source_url": self.source_url,
                "source_link": self.source_link,
                "citation_label": self.citation_label,
                "score": round(float(self.score), 6),
                "vector_score": round(float(self.vector_score), 6),
                "bm25_score": round(float(self.bm25_score), 6),
                "rrf_score": round(float(self.rrf_score), 6),
            },
        )


@dataclass
class RetrievalFilters:
    """检索过滤条件（时效性 + 标签维度）。"""

    statuses: List[str] = field(default_factory=list)
    business_lines: List[str] = field(default_factory=list)
    doc_types: List[str] = field(default_factory=list)
    document_ids: List[int] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    only_valid: bool = True
    exclude_expired: bool = True
    as_of: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "statuses": self.statuses,
            "business_lines": self.business_lines,
            "doc_types": self.doc_types,
            "document_ids": self.document_ids,
            "tags": self.tags,
            "only_valid": self.only_valid,
            "exclude_expired": self.exclude_expired,
            "as_of": self.as_of,
        }


__all__ = ["RetrievedClause", "RetrievalFilters"]
