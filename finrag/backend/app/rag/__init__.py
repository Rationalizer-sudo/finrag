"""rag 包：Prompt 组装、LangChain 生成链、条文溯源、可信度评分。"""

from app.rag.answer_chain import RagAnswer, RagAnswerEngine, create_rag_engine, resolve_chat_model
from app.rag.citation import (
    CitationBundle,
    build_citation_bundle,
    collect_source_links,
    extract_citation_indexes,
    has_uncited_claim,
    render_markdown_sources,
)
from app.rag.credibility import CredibilityResult, compute_credibility, describe_formula, grade

__all__ = [
    "RagAnswer",
    "RagAnswerEngine",
    "create_rag_engine",
    "resolve_chat_model",
    "CitationBundle",
    "build_citation_bundle",
    "collect_source_links",
    "extract_citation_indexes",
    "has_uncited_claim",
    "render_markdown_sources",
    "CredibilityResult",
    "compute_credibility",
    "describe_formula",
    "grade",
]
