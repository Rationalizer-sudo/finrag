"""通义千问（DashScope）Embedding 接入。

通过 LangChain 的 ``OpenAIEmbeddings`` 指向 DashScope 的 OpenAI 兼容端点，
模型为 ``text-embedding-v3``（默认 1024 维，与 Milvus 集合维度保持一致）。

要点
----
* ``check_embedding_ctx_length=False``：DashScope 兼容端点接收**原始文本**，
  不接收 OpenAI 的 token 数组，必须关闭该优化；
* ``chunk_size=10``：text-embedding-v3 单次请求批上限为 10 条；
* 维度必须与 ``QWEN_EMBEDDING_DIM`` 严格一致，否则 Milvus 写入会失败。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.errors import LLMServiceError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# text-embedding-v3 支持的输出维度
SUPPORTED_DIMENSIONS = (1024, 768, 512)
MAX_BATCH_SIZE = 10
MAX_INPUT_CHARS = 8000


@runtime_checkable
class Embedder(Protocol):
    """向量化接口。

    生产实现为 :class:`QwenEmbedder`；测试可注入等价替身，
    使检索与入库逻辑无需真实调用大模型即可验证。
    """

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化文档。"""
        ...

    def embed_query(self, text: str) -> List[float]:
        """向量化单条查询。"""
        ...


class QwenEmbedder:
    """通义千问 embedding 客户端。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._client: Any = None

    # ------------------------------------------------------------ 配置校验
    @property
    def dimension(self) -> int:
        """向量维度。"""
        return int(self.settings.qwen_embedding_dim)

    def is_configured(self) -> bool:
        """API Key 是否已配置。"""
        key = self.settings.dashscope_api_key or ""
        return bool(key) and not key.startswith("sk-xxx")

    def status(self) -> Dict[str, Any]:
        """返回模型接入状态，供健康检查展示。"""
        return {
            "provider": "dashscope",
            "base_url": self.settings.qwen_base_url,
            "model": self.settings.qwen_embedding_model,
            "dimension": self.dimension,
            "configured": self.is_configured(),
            "batch_size": MAX_BATCH_SIZE,
        }

    # -------------------------------------------------------------- 客户端
    @property
    def client(self) -> Any:
        """惰性构造 LangChain ``OpenAIEmbeddings``。"""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        """构造并校验 embedding 客户端。"""
        if not self.is_configured():
            raise LLMServiceError(
                "未配置 DASHSCOPE_API_KEY，无法调用通义千问向量化服务。"
                "请在 .env 中填写 API Key（阿里云百炼控制台获取）。"
            )
        if self.dimension not in SUPPORTED_DIMENSIONS:
            raise LLMServiceError(
                f"QWEN_EMBEDDING_DIM={self.dimension} 不受支持，"
                f"可选：{', '.join(map(str, SUPPORTED_DIMENSIONS))}"
            )
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:  # pragma: no cover
            raise LLMServiceError("未安装 langchain-openai，无法调用向量化服务") from exc

        logger.info(
            "初始化通义千问向量化模型 model=%s dim=%d",
            self.settings.qwen_embedding_model,
            self.dimension,
        )
        return OpenAIEmbeddings(
            model=self.settings.qwen_embedding_model,
            api_key=self.settings.dashscope_api_key,
            base_url=self.settings.qwen_base_url,
            dimensions=self.dimension,
            chunk_size=MAX_BATCH_SIZE,
            check_embedding_ctx_length=False,
            max_retries=self.settings.qwen_max_retries,
            timeout=self.settings.qwen_timeout,
        )

    # ---------------------------------------------------------------- 调用
    @staticmethod
    def _truncate(text: str) -> str:
        """超长文本截断，避免请求被服务端拒绝。"""
        value = (text or "").strip()
        return value[:MAX_INPUT_CHARS] if len(value) > MAX_INPUT_CHARS else value

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化，自动按 10 条一批切片。

        :raises LLMServiceError: 调用失败或返回维度不符合预期。
        """
        items = [self._truncate(t) for t in texts]
        if not items:
            return []

        vectors: List[List[float]] = []
        started = time.perf_counter()
        for start in range(0, len(items), MAX_BATCH_SIZE):
            batch = items[start:start + MAX_BATCH_SIZE]
            try:
                part = self.client.embed_documents(list(batch))
            except Exception as exc:
                raise LLMServiceError(
                    f"通义千问向量化调用失败（第 {start} 批，共 {len(batch)} 条）：{exc}"
                ) from exc
            vectors.extend(part)

        self._validate(vectors, len(items))
        logger.info(
            "向量化完成：%d 条，维度 %d，耗时 %.0f ms",
            len(vectors), self.dimension, (time.perf_counter() - started) * 1000,
        )
        return vectors

    def embed_query(self, text: str) -> List[float]:
        """向量化单条查询。"""
        value = self._truncate(text)
        if not value:
            raise LLMServiceError("查询文本为空，无法向量化")
        try:
            vector = self.client.embed_query(value)
        except Exception as exc:
            raise LLMServiceError(f"通义千问查询向量化失败：{exc}") from exc
        return self._validate([vector], 1)[0]

    def _validate(self, vectors: List[List[float]], expected: int) -> List[List[float]]:
        """校验返回条数与维度。"""
        if len(vectors) != expected:
            raise LLMServiceError(
                f"向量化返回条数异常：期望 {expected} 条，实际 {len(vectors)} 条"
            )
        for vector in vectors:
            if len(vector) != self.dimension:
                raise LLMServiceError(
                    f"向量维度异常：期望 {self.dimension}，实际 {len(vector)}。"
                    "请确认 QWEN_EMBEDDING_DIM 与 Milvus 集合维度一致。"
                )
        return vectors


__all__ = ["QwenEmbedder", "Embedder", "SUPPORTED_DIMENSIONS", "MAX_BATCH_SIZE"]
