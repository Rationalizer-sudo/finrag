"""RAG 生成链（LangChain 组装）。

链路形态::

    ChatPromptTemplate ──▶ ChatModel(通义千问) ──▶ StrOutputParser
        │
        └── 输入：{context}(检索到的制度条文) + {question}(用户问题)

生成后处理（非 LLM 部分，保证确定性）：
1. :mod:`app.rag.citation` 解析 ``[序号]`` 引用并映射为条文溯源对象；
2. :mod:`app.rag.credibility` 计算 0~100 可信度分；
3. 无证据 / 全为过时条文时**不调用大模型**，直接返回确定性提示，
   既省成本又避免模型自由发挥。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableSequence

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger
from app.rag import prompts
from app.rag.citation import CitationBundle, build_citation_bundle
from app.rag.credibility import CredibilityResult, compute_credibility
from app.retrieval.types import RetrievedClause

logger = get_logger(__name__)


@dataclass
class RagAnswer:
    """一次 RAG 生成的完整结果。"""

    question: str = ""
    answer: str = ""
    has_answer: bool = True
    citations: List[Dict[str, Any]] = field(default_factory=list)
    cited_indexes: List[int] = field(default_factory=list)
    credibility: CredibilityResult = field(default_factory=CredibilityResult)
    evidence_count: int = 0
    context_chars: int = 0
    llm_called: bool = False
    elapsed_ms: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "question": self.question,
            "answer": self.answer,
            "has_answer": self.has_answer,
            "citations": self.citations,
            "cited_indexes": self.cited_indexes,
            "credibility": self.credibility.to_dict(),
            "evidence_count": self.evidence_count,
            "context_chars": self.context_chars,
            "llm_called": self.llm_called,
            "elapsed_ms": self.elapsed_ms,
            "warnings": self.warnings,
        }


def resolve_chat_model(candidate: Any) -> Any:
    """从 :class:`QwenChatClient` 或 LangChain ChatModel 中解析出模型对象。

    这样上层既可传通义千问客户端，也可直接传任意 LangChain ChatModel
    （含测试替身），无需改动生成链。
    """
    if candidate is None:
        raise ValueError("chat_model 不能为空")
    model = getattr(candidate, "model", None)
    if model is not None and hasattr(model, "invoke"):
        return model
    if hasattr(candidate, "invoke"):
        return candidate
    raise TypeError("传入对象既不是 LangChain ChatModel，也不包含 .model 属性")


class RagAnswerEngine:
    """RAG 问答生成引擎。"""

    def __init__(self, chat_model: Any, settings: Optional[Settings] = None) -> None:
        self.chat_model = resolve_chat_model(chat_model)
        self.settings: Settings = settings or get_settings()
        self.prompt = prompts.build_chat_prompt()
        self.parser = StrOutputParser()
        self._chain: Optional[Runnable] = None

    # ------------------------------------------------------------------ 链
    @property
    def chain(self) -> Runnable:
        """LangChain 生成链：Prompt | ChatModel | StrOutputParser。"""
        if self._chain is None:
            self._chain = RunnableSequence(
                first=self.prompt,
                middle=[self.chat_model],
                last=self.parser,
            )
            logger.debug("RAG 生成链构建完成")
        return self._chain

    # ------------------------------------------------------------- 前置判断
    @staticmethod
    def valid_evidences(evidences: Sequence[RetrievedClause]) -> List[RetrievedClause]:
        """筛出可用的（现行有效/尚未生效）证据。"""
        return [item for item in evidences if item.is_valid]

    def _short_circuit(
        self, question: str, answer: str, evidences: Sequence[RetrievedClause]
    ) -> RagAnswer:
        """不调用大模型的确定性返回路径。"""
        bundle = build_citation_bundle(answer, [])
        credibility = compute_credibility(
            question, answer, [], bundle, has_answer=False
        )
        return RagAnswer(
            question=question,
            answer=answer,
            has_answer=False,
            citations=[],
            cited_indexes=[],
            credibility=credibility,
            evidence_count=len(evidences),
            context_chars=0,
            llm_called=False,
            warnings=["未调用大模型：知识库中没有可用的现行有效条文。"],
        )

    # ---------------------------------------------------------------- 生成
    def build_inputs(
        self, question: str, evidences: Sequence[RetrievedClause]
    ) -> Dict[str, Any]:
        """构造链的输入变量。"""
        return prompts.build_prompt_inputs(question, evidences)

    def invoke(self, question: str, evidences: Sequence[RetrievedClause]) -> str:
        """调用 LangChain 链生成原始回答文本。"""
        return self.chain.invoke(self.build_inputs(question, evidences))

    def stream(self, question: str, evidences: Sequence[RetrievedClause]) -> Iterator[str]:
        """流式生成，逐块产出增量文本。"""
        yield from self.chain.stream(self.build_inputs(question, evidences))

    def finalize(
        self,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedClause],
    ) -> RagAnswer:
        """对模型输出做溯源与可信度后处理。"""
        bundle: CitationBundle = build_citation_bundle(answer, evidences)
        credibility = compute_credibility(
            question, answer, evidences, bundle, has_answer=True
        )
        return RagAnswer(
            question=question,
            answer=answer,
            has_answer=True,
            citations=bundle.to_list(),
            cited_indexes=bundle.cited_indexes,
            credibility=credibility,
            evidence_count=len(evidences),
            context_chars=prompts.count_context_chars(evidences),
            llm_called=True,
            warnings=list(bundle.warnings),
        )

    def generate(
        self,
        question: str,
        evidences: Sequence[RetrievedClause],
        *,
        strict_validity: bool = True,
    ) -> RagAnswer:
        """完整生成流程（含短路保护）。

        :param strict_validity: 为 True（默认）时只依据**现行有效**条文作答，
            全为过时条文时拒绝作答；为 False 时允许依据用户显式索取的历史
            （已修订/已废止）条文作答，用于制度沿革研究。
            该开关来自请求参数 ``only_valid``，保证「默认防误导」与
            「显式查历史」两种诉求都能被满足。
        """
        started = time.perf_counter()
        text = (question or "").strip()

        if not evidences:
            result = self._short_circuit(text, prompts.NO_EVIDENCE_ANSWER, evidences)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

        usable = self.valid_evidences(evidences) if strict_validity else list(evidences)
        if not usable:
            result = self._short_circuit(text, prompts.STALE_EVIDENCE_ANSWER, evidences)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

        raw = self.invoke(text, usable)
        result = self.finalize(text, raw, usable)
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "RAG 生成完成：证据=%d 引用=%d 可信度=%.1f(%s) 耗时=%dms",
            result.evidence_count,
            len(result.citations),
            result.credibility.score,
            result.credibility.label,
            result.elapsed_ms,
        )
        return result


def create_rag_engine(
    chat_model: Any, settings: Optional[Settings] = None
) -> RagAnswerEngine:
    """工厂函数。"""
    return RagAnswerEngine(chat_model, settings)


__all__ = ["RagAnswer", "RagAnswerEngine", "create_rag_engine", "resolve_chat_model"]
