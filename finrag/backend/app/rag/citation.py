"""条文溯源（答案 -> 制度条文引用）。

对应课题要求「支持答案溯源（标注对应制度条文）」与差异化要求
「提供答案可信度评分与条文溯源链接」。

流程：
1. 从模型回答中解析 ``[1] [2]`` 引用标记；
2. 按标记把证据映射为带链接、状态、条款号的引用对象；
3. 若模型未标注任何引用，则退化为「返回全部召回条文」并给出提示，
   让用户仍可自行核对，而不是丢失溯源信息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from app.core.logging_conf import get_logger
from app.retrieval.types import RetrievedClause

logger = get_logger(__name__)

# 支持 [1] / ［1］ / 【1】 三种标记形态
RE_CITATION = re.compile(r"[\[［【]\s*(\d{1,2})\s*[\]］】]")
# 连续的引用标记（如 [1][2]）合并处理
RE_CITATION_GROUP = re.compile(r"(?:[\[［【]\s*\d{1,2}\s*[\]］】】])+")
RE_SINGLE = re.compile(r"\d{1,2}")

# 这些句子即使没有引用标记也不应触发「未标注引用」告警（属于操作性提示）
BOILERPLATE_HINTS = (
    "以正式发文",
    "以监管口径为准",
    "以上回答依据",
    "仅供参考",
    "建议您",
    "现有知识库中未检索到",
)


@dataclass
class CitationBundle:
    """一次问答的引用集合。"""

    citations: List[Dict[str, Any]] = field(default_factory=list)
    cited_indexes: List[int] = field(default_factory=list)
    uncited_indexes: List[int] = field(default_factory=list)
    has_explicit_citation: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """引用条数。"""
        return len(self.citations)

    def to_list(self) -> List[Dict[str, Any]]:
        """返回可持久化的引用列表。"""
        return list(self.citations)


def extract_citation_indexes(answer: str, max_index: int = 0) -> List[int]:
    """从回答文本中解析引用编号（去重、保持出现顺序）。

    :param answer: 模型回答。
    :param max_index: 合法编号上限（证据条数）；为 0 表示不校验。
    """
    if not answer:
        return []
    found: List[int] = []
    seen: set = set()
    for match in RE_CITATION.finditer(answer):
        try:
            value = int(match.group(1))
        except ValueError:  # pragma: no cover - 正则已限制为数字
            continue
        if value <= 0:
            continue
        if max_index and value > max_index:
            continue
        if value not in seen:
            seen.add(value)
            found.append(value)
    return found


def has_uncited_claim(answer: str, min_chars: int = 40) -> bool:
    """粗略判断回答中是否存在「较长但未标注引用」的实质句子。

    用于提示模型可能未按规范溯源（不阻断返回，只作为告警与可信度扣分项）。
    """
    if not answer:
        return False
    for sentence in re.split(r"[。\n；;]", answer):
        text = sentence.strip()
        if len(text) < min_chars:
            continue
        if any(hint in text for hint in BOILERPLATE_HINTS):
            continue
        if RE_CITATION.search(text):
            continue
        return True
    return False


def build_citation_bundle(
    answer: str,
    evidences: Sequence[RetrievedClause],
    *,
    fallback_to_all: bool = True,
) -> CitationBundle:
    """构造引用集合。

    :param answer: 模型回答。
    :param evidences: 召回的条文（顺序即 Prompt 中的编号顺序）。
    :param fallback_to_all: 模型未标注引用时是否返回全部召回条文。
    """
    bundle = CitationBundle()
    if not evidences:
        return bundle

    total = len(evidences)
    indexes = extract_citation_indexes(answer, max_index=total)
    bundle.has_explicit_citation = bool(indexes)

    if not indexes and fallback_to_all:
        indexes = list(range(1, total + 1))
        bundle.warnings.append(
            "模型回答未显式标注条文编号，已列出全部召回条文供人工核对。"
        )
    elif not indexes:
        bundle.warnings.append("模型回答未标注任何条文编号，且未启用兜底引用。")

    cited = set(indexes)
    for index in indexes:
        bundle.citations.append(evidences[index - 1].to_citation(index))
    bundle.cited_indexes = list(indexes)
    bundle.uncited_indexes = [i for i in range(1, total + 1) if i not in cited]

    if bundle.has_explicit_citation and has_uncited_claim(answer):
        bundle.warnings.append("回答中存在未标注来源的实质陈述，请核对依据。")

    return bundle


def collect_source_links(citations: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """提取「制度名称 + 溯源链接」列表，便于前端渲染引用卡片。"""
    links: List[Dict[str, str]] = []
    for item in citations:
        links.append(
            {
                "citation_label": str(item.get("citation_label", "")),
                "doc_title": str(item.get("doc_title", "")),
                "clause_no": str(item.get("clause_no", "")),
                "source_link": str(item.get("source_link", "")),
            }
        )
    return links


def render_markdown_sources(citations: Sequence[Dict[str, Any]]) -> str:
    """把引用渲染为 Markdown 参考条文清单（用于导出/复制）。"""
    if not citations:
        return ""
    lines = ["**参考条文**", ""]
    for item in citations:
        label = item.get("citation_label") or f"条文#{item.get('chunk_id')}"
        status = item.get("status", "")
        link = item.get("source_link", "")
        suffix = f"（{status}）" if status else ""
        lines.append(f"[{item.get('index', '?')}] {label}{suffix} — {link}")
    return "\n".join(lines)


__all__ = [
    "CitationBundle",
    "extract_citation_indexes",
    "has_uncited_claim",
    "build_citation_bundle",
    "collect_source_links",
    "render_markdown_sources",
    "RE_CITATION",
]
