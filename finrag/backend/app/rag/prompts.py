"""RAG 提示词与上下文组装。

Prompt 设计目标是**抑制幻觉**并**强制溯源**，这是金融制度问答的底线要求：

* 只允许依据检索到的条文作答，资料不足必须如实说明；
* 必须用 ``[序号]`` 标注依据，序号与【参考资料】严格对应；
* 引用原文时必须保持数字、期限、比例、金额不变；
* 存在版本冲突时优先现行有效条文并明确提示。
"""

from __future__ import annotations

from typing import List, Sequence

from langchain_core.prompts import ChatPromptTemplate

from app.core.constants import DocStatus
from app.core.logging_conf import get_logger
from app.retrieval.types import RetrievedClause

logger = get_logger(__name__)

# 单条证据注入 Prompt 的正文上限（字符），控制上下文长度与响应时延
MAX_EVIDENCE_CHARS = 1200

SYSTEM_PROMPT = """你是「金融制度智能问答助手」，服务对象是金融从业者与高校学生。

请严格依据【参考资料】中检索到的制度条文回答问题，并遵守以下规则：

1. 只用【参考资料】中的内容作答。禁止凭记忆、常识或推理补充任何条文编号、\
数字、期限、比例、金额、处罚标准；禁止编造不存在的制度名称或条款。
2. 如果【参考资料】不足以回答问题，必须明确回答「现有知识库中未检索到相关规定」，\
并说明还缺少哪方面的制度依据；绝对不要给出推测性结论。
3. 在每处依据的句子末尾用 [1]、[2] 形式标注来源编号，编号必须与【参考资料】的\
序号完全一致。没有依据的句子不要标注编号，也不要写。
4. 引用条文原意时保持数字、期限、比例、金额与原文一致；直接引用原文请用「」括起。
5. 若检索到的条文之间时效性不同（现行有效 / 已修订 / 已废止 / 尚未生效），\
必须指出版本差异，并以现行有效条文为准；已被废止的条文只能作为历史沿革说明。
6. 用专业、简洁的中文作答。先给结论，再列依据；内容较多时分点说明。
7. 在回答最后另起一行提示：「以上回答依据公开制度文本生成，具体业务办理请以\
正式发文及监管口径为准。」"""

HUMAN_TEMPLATE = """【参考资料】
{context}

【用户问题】
{question}

请依据上述【参考资料】作答：
- 若资料能回答问题，给出结论并逐条标注来源编号（如 [1]）；
- 若资料不足以回答，请直接说明「现有知识库中未检索到相关规定」，并指出缺失的信息。"""

NO_EVIDENCE_ANSWER = (
    "现有知识库中未检索到与您问题相关的制度条文，因此无法给出有依据的回答。\n\n"
    "建议您：\n"
    "1. 换用更贴近制度原文的表述（如「资本充足率」「投资者适当性」「可疑交易报告」等术语）；"
    "或\n"
    "2. 通过「知识库管理」上传对应的制度文件后再提问。\n\n"
    "以上回答依据公开制度文本生成，具体业务办理请以正式发文及监管口径为准。"
)

# 检索命中文档虽存在、但时效性均不可用时的专门提示
STALE_EVIDENCE_ANSWER = (
    "检索到的相关条文均已被废止或已失效，为避免误导，系统不基于过时条文作答。\n\n"
    "建议您查阅该制度的最新版本，或通过「知识库管理」导入现行有效版本后重新提问。\n\n"
    "以上回答依据公开制度文本生成，具体业务办理请以正式发文及监管口径为准。"
)


def format_evidence(index: int, clause: RetrievedClause) -> str:
    """把单条条文格式化为 Prompt 中的一段参考资料。"""
    head_parts: List[str] = []
    if clause.doc_title:
        head_parts.append(f"《{clause.doc_title}》")
        # 文号跟随制度名放在括号里；没有制度名时直接展示文号，避免出现「（）」空括号
        if clause.doc_no:
            head_parts.append(f"（{clause.doc_no}）")
    elif clause.doc_no:
        head_parts.append(clause.doc_no)
    if clause.issuer:
        head_parts.append(f" 发文机关：{clause.issuer}")
    header = f"[{index}] " + (" ".join(head_parts) if head_parts else f"条文#{clause.chunk_id}")

    lines: List[str] = [header]

    meta_parts: List[str] = []
    if clause.clause_no:
        meta_parts.append(f"条文号：{clause.clause_no}")
    if clause.chapter:
        meta_parts.append(f"所属章节：{clause.chapter}")
    if clause.article_title:
        meta_parts.append(f"条文主题：{clause.article_title}")
    meta_parts.append(f"时效状态：{clause.status or DocStatus.UNKNOWN.value}")
    if clause.effective_date:
        meta_parts.append(f"施行日期：{clause.effective_date.isoformat()}")
    if clause.expiry_date:
        meta_parts.append(f"失效日期：{clause.expiry_date.isoformat()}")
    if clause.business_line:
        meta_parts.append(f"业务条线：{clause.business_line}")
    if meta_parts:
        lines.append("  · " + " ｜ ".join(meta_parts))

    content = clause.content or ""
    if len(content) > MAX_EVIDENCE_CHARS:
        content = content[:MAX_EVIDENCE_CHARS] + "……（条文过长已截断）"
    lines.append("  正文：" + content)
    return "\n".join(lines)


def format_context(evidences: Sequence[RetrievedClause]) -> str:
    """把全部证据拼成 Prompt 的 context 段落。"""
    if not evidences:
        return "（未检索到相关制度条文）"
    return "\n\n".join(
        format_evidence(index, clause) for index, clause in enumerate(evidences, start=1)
    )


def build_chat_prompt() -> ChatPromptTemplate:
    """构造 LangChain 对话提示模板。"""
    return ChatPromptTemplate.from_messages(
        [("system", SYSTEM_PROMPT), ("human", HUMAN_TEMPLATE)]
    )


def build_prompt_inputs(question: str, evidences: Sequence[RetrievedClause]) -> dict:
    """构造提示模板的输入变量。"""
    return {"context": format_context(evidences), "question": (question or "").strip()}


def build_messages(
    question: str, evidences: Sequence[RetrievedClause]
) -> List[tuple]:
    """返回 ``[(role, content), ...]``，便于不使用模板时直接调用模型。"""
    inputs = build_prompt_inputs(question, evidences)
    return [("system", SYSTEM_PROMPT), ("human", HUMAN_TEMPLATE.format(**inputs))]


def count_context_chars(evidences: Sequence[RetrievedClause]) -> int:
    """统计注入上下文的总字符数（用于可观测性与时延分析）。"""
    return len(format_context(evidences)) if evidences else 0


__all__ = [
    "SYSTEM_PROMPT",
    "HUMAN_TEMPLATE",
    "NO_EVIDENCE_ANSWER",
    "STALE_EVIDENCE_ANSWER",
    "MAX_EVIDENCE_CHARS",
    "format_evidence",
    "format_context",
    "build_chat_prompt",
    "build_prompt_inputs",
    "build_messages",
    "count_context_chars",
]
