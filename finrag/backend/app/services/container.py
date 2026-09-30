"""依赖容器（轻量 DI）。

把「有状态、构建昂贵」的组件（Milvus 连接、embedding 客户端、检索器、
入库流水线、RAG 引擎）集中装配为进程级单例，接口层通过
:func:`app.api.deps.get_container` 取用。

这样做的好处：
* **可测试**：单测可用 :func:`set_container` 注入测试替身，
  生产链路仍然严格是「Milvus + 通义千问」，不需要在业务代码里写分支；
* **生命周期清晰**：应用关停时统一 :meth:`Container.close` 释放连接。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import Settings, get_settings
from app.core.logging_conf import get_logger
from app.db.milvus_store import MilvusVectorStore
from app.llm.embeddings import Embedder, QwenEmbedder
from app.llm.qwen import QwenChatClient
from app.rag.answer_chain import RagAnswerEngine
from app.retrieval.bm25_retriever import Bm25Retriever, get_bm25_retriever
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_retriever import VectorRetriever

logger = get_logger(__name__)


@dataclass
class Container:
    """进程级依赖容器（惰性构建重组件）。"""

    settings: Settings
    vector_store: MilvusVectorStore
    embedder: Embedder
    chat_client: Any
    bm25_retriever: Bm25Retriever
    _vector_retriever: Optional[VectorRetriever] = field(default=None, repr=False)
    _hybrid_retriever: Optional[HybridRetriever] = field(default=None, repr=False)
    _rag_engine: Optional[RagAnswerEngine] = field(default=None, repr=False)
    _ingest_pipeline: Any = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------ 检索链路
    @property
    def vector_retriever(self) -> VectorRetriever:
        """向量检索器。"""
        if self._vector_retriever is None:
            self._vector_retriever = VectorRetriever(
                self.vector_store, self.embedder, self.settings
            )
        return self._vector_retriever

    @property
    def hybrid_retriever(self) -> HybridRetriever:
        """混合检索器（向量 + BM25 + RRF）。"""
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever(
                self.vector_retriever, self.bm25_retriever, self.settings
            )
        return self._hybrid_retriever

    # ------------------------------------------------------------ 生成链路
    @property
    def rag_engine(self) -> RagAnswerEngine:
        """RAG 生成引擎。

        需要有效的 DASHSCOPE_API_KEY；未配置时会在首次调用抛出
        ``LLMServiceError``，由接口层转换为 502 并给出可操作提示。
        """
        if self._rag_engine is None:
            with self._lock:
                if self._rag_engine is None:
                    model = getattr(self.chat_client, "model", self.chat_client)
                    self._rag_engine = RagAnswerEngine(model, self.settings)
        return self._rag_engine

    # ------------------------------------------------------------ 入库链路
    @property
    def ingest_pipeline(self) -> Any:
        """知识库入库流水线。"""
        if self._ingest_pipeline is None:
            from app.ingest.pipeline import IngestPipeline

            self._ingest_pipeline = IngestPipeline(
                self.settings, self.vector_store, self.embedder
            )
        return self._ingest_pipeline

    # ---------------------------------------------------------------- 状态
    def status(self) -> dict:
        """汇总各组件接入状态（供 /health 与知识库总览使用）。"""
        return {
            "llm": _safe_status(self.chat_client),
            "embedding": _safe_status(self.embedder),
            "vector_store": {
                "backend": "milvus",
                "uri": self.vector_store.uri,
                "collection": self.vector_store.collection,
                "dim": self.vector_store.dim,
            },
        }

    def close(self) -> None:
        """释放连接资源。"""
        try:
            self.vector_store.close()
        except Exception:  # pragma: no cover
            logger.warning("关闭向量库连接异常", exc_info=True)


def _safe_status(component: Any) -> dict:
    """安全调用组件的 status()，避免健康检查本身失败。"""
    status = getattr(component, "status", None)
    if not callable(status):
        return {"configured": False, "reason": "组件不支持状态查询"}
    try:
        return dict(status())
    except Exception as exc:  # pragma: no cover
        return {"configured": False, "reason": str(exc)}


def build_container(
    settings: Optional[Settings] = None,
    *,
    vector_store: Optional[MilvusVectorStore] = None,
    embedder: Optional[Embedder] = None,
    chat_client: Any = None,
    bm25_retriever: Optional[Bm25Retriever] = None,
) -> Container:
    """按需覆盖地构建容器（未传参则使用生产实现）。"""
    cfg = settings or get_settings()
    return Container(
        settings=cfg,
        vector_store=vector_store or MilvusVectorStore(cfg),
        embedder=embedder or QwenEmbedder(cfg),
        chat_client=chat_client or QwenChatClient(cfg),
        bm25_retriever=bm25_retriever or get_bm25_retriever(),
    )


_container: Optional[Container] = None
_container_lock = threading.Lock()


def get_container() -> Container:
    """获取全局容器（首次调用时构建）。"""
    global _container
    if _container is None:
        with _container_lock:
            if _container is None:
                _container = build_container()
                logger.info("依赖容器初始化完成")
    return _container


def set_container(container: Container) -> None:
    """替换全局容器（测试或运行期重配置用）。"""
    global _container
    _container = container


def reset_container() -> None:
    """释放并清空全局容器。"""
    global _container
    if _container is not None:
        _container.close()
    _container = None


__all__ = [
    "Container",
    "build_container",
    "get_container",
    "set_container",
    "reset_container",
]
