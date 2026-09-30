"""答案可信度评分（课题差异化要求中的技术创新点）。

设计动机
--------
RAG 系统最大的风险是「答得很像但依据不足」。金融制度问答一旦给出错误
条文或过期条款，后果远大于「答不出来」。因此本模块对每次回答给出
**0~100 的可解释可信度分**，并拆解为 5 个可核对的维度：

===================  =====  ==========================================
维度                  权重   含义
===================  =====  ==========================================
retrieval_strength     25   检索相关度（Top 条文的融合得分）
term_alignment         25   问题术语在证据中的覆盖比例
citation_coverage      20   回答标注的引用条数是否充分
evidence_validity      15   证据中现行有效条文占比
answer_grounding       15   回答用词有多少能在证据中找到出处
===================  =====  ==========================================

评分是**纯函数**，输入问答文本与证据即可复现，便于单测与阈值调优。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.core.constants import CredibilityLevel, DocStatus
from app.core.logging_conf import get_logger
from app.rag.citation import CitationBundle
from app.retrieval.tokenizer import coverage, tokenize_query
from app.retrieval.types import RetrievedClause

logger = get_logger(__name__)

WEIGHTS: Dict[str, float] = {
    "retrieval_strength": 25.0,
    "term_alignment": 25.0,
    "citation_coverage": 20.0,
    "evidence_validity": 15.0,
    "answer_grounding": 15.0,
}

# 可信度等级阈值（分数 >= 阈值即达到该等级）
LEVEL_THRESHOLDS = (
    (80.0, CredibilityLevel.HIGH.value),
    (60.0, CredibilityLevel.MEDIUM.value),
    (40.0, CredibilityLevel.LOW.value),
)

# 回答接地度映射：词汇覆盖率 >= 该值时给满分
GROUNDING_FULL_COVERAGE = 0.75
# 认为「引用充分」的最小条数
SUFFICIENT_CITATION_COUNT = 2
# 计算接地度时需要剔除的模板句（它们天然不会出现在制度条文里）
RE_TEMPLATE_SENTENCE = re.compile(
    r"(以正式发文|以监管口径为准|以上回答依据|仅供参考|建议您|现有知识库中未检索到)"
)


@dataclass
class CredibilityResult:
    """可信度评分结果。"""

    score: float = 0.0
    label: str = CredibilityLevel.UNRELIABLE.value
    components: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "score": round(float(self.score), 2),
            "label": self.label,
            "components": {k: round(float(v), 2) for k, v in self.components.items()},
            "reasons": list(self.reasons),
        }


def grade(score: float) -> str:
    """分数 -> 等级标签。"""
    for threshold, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return label
    return CredibilityLevel.UNRELIABLE.value


def _retrieval_strength(evidences: Sequence[RetrievedClause]) -> float:
    """检索相关度：Top 得分与平均得分的加权（0~1）。"""
    if not evidences:
        return 0.0
    scores = [max(0.0, min(1.0, float(item.score or 0.0))) for item in evidences[:5]]
    best = max(scores)
    mean = sum(scores) / len(scores)
    return 0.6 * best + 0.4 * mean


def _term_alignment(question: str, evidences: Sequence[RetrievedClause]) -> float:
    """问题术语在证据中的覆盖比例（0~1）。"""
    query_tokens = tokenize_query(question)
    if not query_tokens or not evidences:
        return 0.0
    haystack = tokenize_query(" ".join(item.content for item in evidences))
    return coverage(query_tokens, haystack)


def _citation_coverage(bundle: Optional[CitationBundle], evidence_count: int) -> float:
    """引用充分度（0~1）。"""
    if evidence_count <= 0:
        return 0.0
    if bundle is None or not bundle.citations:
        return 0.0
    cited = len(bundle.cited_indexes)
    if not bundle.has_explicit_citation:
        # 兜底列出全部条文：有信息但不算「主动溯源」
        return 0.4
    target = min(SUFFICIENT_CITATION_COUNT, evidence_count)
    return min(1.0, cited / max(target, 1))


def _evidence_validity(evidences: Sequence[RetrievedClause]) -> float:
    """现行有效条文占比（0~1）。"""
    if not evidences:
        return 0.0
    valid = sum(1 for item in evidences if item.is_valid)
    return valid / len(evidences)


def _answer_grounding(answer: str, evidences: Sequence[RetrievedClause]) -> float:
    """回答接地度：回答用词能在证据中找到出处的比例（0~1）。"""
    if not answer or not evidences:
        return 0.0
    stripped = RE_TEMPLATE_SENTENCE.sub(" ", answer)
    answer_tokens = tokenize_query(stripped)
    if not answer_tokens:
        return 0.0
    evidence_tokens = tokenize_query(" ".join(item.content for item in evidences))
    raw = coverage(answer_tokens, evidence_tokens)
    return min(1.0, raw / GROUNDING_FULL_COVERAGE)


def _build_reasons(
    components: Dict[str, float],
    evidences: Sequence[RetrievedClause],
    bundle: Optional[CitationBundle],
    has_answer: bool,
) -> List[str]:
    """生成人类可读的评分说明（帮助用户判断是否采信）。"""
    reasons: List[str] = []
    if not has_answer:
        reasons.append("未基于知识库条文作答，可信度不予评定。")
        return reasons

    if not evidences:
        reasons.append("未检索到任何相关条文。")
        return reasons

    if components["retrieval_strength"] < 0.5:
        reasons.append("检索相关度偏低，问题表述可能与制度用语差异较大。")
    if components["term_alignment"] < 0.5:
        reasons.append("问题中的部分术语未在召回条文中出现，可能存在概念偏差。")
    if components["citation_coverage"] < 0.6:
        if bundle is not None and not bundle.has_explicit_citation:
            reasons.append("回答未显式标注条文编号，溯源的直接性不足。")
        else:
            reasons.append("回答引用的条文数量偏少，依据不够充分。")
    if components["evidence_validity"] < 1.0:
        stale = sum(1 for item in evidences if not item.is_valid)
        reasons.append(f"召回条文中有 {stale} 条非现行有效（已修订/已废止/尚未生效）。")
    if components["answer_grounding"] < 0.5:
        reasons.append("回答用词与制度原文重合度较低，存在表述失真的风险。")

    if bundle is not None:
        reasons.extend(bundle.warnings)

    statuses = {item.status for item in evidences}
    if statuses - {DocStatus.EFFECTIVE.value}:
        reasons.append(
            "证据集合包含非现行有效条文，请以标注为「现行有效」的条文为准。"
        )
    return reasons


def compute_credibility(
    question: str,
    answer: str,
    evidences: Sequence[RetrievedClause],
    bundle: Optional[CitationBundle] = None,
    *,
    has_answer: bool = True,
) -> CredibilityResult:
    """计算答案可信度。

    :param question: 用户问题。
    :param answer: 模型回答。
    :param evidences: 召回条文。
    :param bundle: 引用集合；为 ``None`` 时该项按 0 计。
    :param has_answer: 是否给出了基于知识库的回答（未检索到时应传 ``False``）。
    """
    if not has_answer:
        return CredibilityResult(
            score=0.0,
            label=CredibilityLevel.UNRELIABLE.value,
            components={key: 0.0 for key in WEIGHTS},
            reasons=["未基于知识库条文作答，可信度不予评定。"],
        )

    components = {
        "retrieval_strength": _retrieval_strength(evidences),
        "term_alignment": _term_alignment(question, evidences),
        "citation_coverage": _citation_coverage(bundle, len(evidences)),
        "evidence_validity": _evidence_validity(evidences),
        "answer_grounding": _answer_grounding(answer, evidences),
    }

    total = sum(WEIGHTS[key] * value for key, value in components.items())
    score = round(max(0.0, min(100.0, total)), 2)

    return CredibilityResult(
        score=score,
        label=grade(score),
        components=components,
        reasons=_build_reasons(components, evidences, bundle, has_answer),
    )


def describe_formula() -> Dict[str, Any]:
    """返回评分口径说明（供接口自解释，便于答辩演示）。"""
    return {
        "weights": WEIGHTS,
        "thresholds": [
            {"min_score": threshold, "label": label}
            for threshold, label in LEVEL_THRESHOLDS
        ]
        + [{"min_score": 0.0, "label": CredibilityLevel.UNRELIABLE.value}],
        "note": (
            "综合得分 = Σ(维度权重 × 维度得分)，满分 100。"
            "权重与阈值可通过调参文件集中调整，评分过程完全可复现。"
        ),
    }


__all__ = [
    "CredibilityResult",
    "compute_credibility",
    "grade",
    "describe_formula",
    "WEIGHTS",
    "LEVEL_THRESHOLDS",
]
