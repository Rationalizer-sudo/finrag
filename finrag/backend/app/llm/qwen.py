"""通义千问（DashScope）对话模型接入。

技术选型说明
------------
课题「核心技术栈」要求 **LangChain 搭建 RAG 架构**、LLM 选用**开源可商用的
通义千问**。生产链路即：

    通义千问 OpenAI 兼容端点  ──LangChain ChatOpenAI──▶  RAG 生成链

因此本模块用 ``langchain_openai.ChatOpenAI`` 指向 DashScope 兼容端点，
既满足「LangChain 整合 LLM」的要求，又避免额外引入非标准 SDK。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from app.core.config import Settings, get_settings
from app.core.errors import LLMServiceError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# 对话角色 -> LangChain 消息类型
ROLE_SYSTEM = "system"
ROLE_HUMAN = "human"
ROLE_AI = "ai"

MessageLike = Tuple[str, str]


class QwenChatClient:
    """通义千问对话客户端（LangChain ChatModel 封装）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._model: Any = None

    # ------------------------------------------------------------ 配置校验
    def is_configured(self) -> bool:
        """API Key 是否已配置。"""
        key = self.settings.dashscope_api_key or ""
        return bool(key) and not key.startswith("sk-xxx")

    def status(self) -> Dict[str, Any]:
        """返回模型接入状态。"""
        return {
            "provider": "dashscope",
            "base_url": self.settings.qwen_base_url,
            "model": self.settings.qwen_chat_model,
            "temperature": self.settings.qwen_temperature,
            "timeout": self.settings.qwen_timeout,
            "configured": self.is_configured(),
        }

    # -------------------------------------------------------------- 模型
    @property
    def model(self) -> Any:
        """惰性构造 LangChain ChatModel。"""
        if self._model is None:
            self._model = self._build_model()
        return self._model

    def _build_model(self) -> Any:
        """构造 ``ChatOpenAI`` 实例。"""
        if not self.is_configured():
            raise LLMServiceError(
                "未配置 DASHSCOPE_API_KEY，无法调用通义千问对话模型。"
                "请在 .env 中填写 API Key（阿里云百炼控制台获取）。"
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMServiceError("未安装 langchain-openai，无法调用对话模型") from exc

        logger.info("初始化通义千问对话模型 model=%s", self.settings.qwen_chat_model)
        return ChatOpenAI(
            model=self.settings.qwen_chat_model,
            api_key=self.settings.dashscope_api_key,
            base_url=self.settings.qwen_base_url,
            temperature=self.settings.qwen_temperature,
            timeout=self.settings.qwen_timeout,
            max_retries=self.settings.qwen_max_retries,
        )

    # ---------------------------------------------------------- 消息转换
    @staticmethod
    def build_messages(messages: Sequence[MessageLike]) -> List[Any]:
        """把 ``(role, content)`` 元组转换为 LangChain 消息对象。

        未知角色按 human 处理，避免上游拼装错误导致整链崩溃。
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        mapping = {
            ROLE_SYSTEM: SystemMessage,
            ROLE_HUMAN: HumanMessage,
            ROLE_AI: AIMessage,
        }
        result: List[Any] = []
        for role, content in messages:
            cls = mapping.get(role, HumanMessage)
            result.append(cls(content=content))
        return result

    # -------------------------------------------------------------- 调用
    def invoke(self, messages: Sequence[MessageLike]) -> str:
        """同步生成完整回答。

        :param messages: ``[("system", "..."), ("human", "...")]``
        :raises LLMServiceError: 调用失败。
        """
        payload = self.build_messages(messages)
        started = time.perf_counter()
        try:
            response = self.model.invoke(payload)
        except Exception as exc:
            raise LLMServiceError(f"通义千问对话调用失败：{exc}") from exc
        text = self._extract_text(response)
        logger.info(
            "对话生成完成：%d 字，耗时 %.0f ms",
            len(text), (time.perf_counter() - started) * 1000,
        )
        return text

    def stream(self, messages: Sequence[MessageLike]) -> Iterator[str]:
        """流式生成，逐块产出增量文本。"""
        payload = self.build_messages(messages)
        try:
            for chunk in self.model.stream(payload):
                piece = self._extract_text(chunk, partial=True)
                if piece:
                    yield piece
        except LLMServiceError:
            raise
        except Exception as exc:
            raise LLMServiceError(f"通义千问流式调用失败：{exc}") from exc

    @staticmethod
    def _extract_text(response: Any, partial: bool = False) -> str:
        """从 LangChain 响应对象中提取文本。

        兼容 ``AIMessage.content`` 为字符串或内容块列表两种形态
        （多模态/推理模型会返回 list）。
        """
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if content is None and partial:  # pragma: no cover
            return ""
        return str(content) if content is not None else ""


__all__ = ["QwenChatClient", "ROLE_SYSTEM", "ROLE_HUMAN", "ROLE_AI", "MessageLike"]
