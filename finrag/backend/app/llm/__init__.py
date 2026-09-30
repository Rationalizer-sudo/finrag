"""llm 包：通义千问（DashScope）对话与向量化接入。"""

from app.llm.embeddings import Embedder, QwenEmbedder
from app.llm.qwen import (
    ROLE_AI,
    ROLE_HUMAN,
    ROLE_SYSTEM,
    MessageLike,
    QwenChatClient,
)

__all__ = [
    "QwenChatClient",
    "QwenEmbedder",
    "Embedder",
    "MessageLike",
    "ROLE_SYSTEM",
    "ROLE_HUMAN",
    "ROLE_AI",
]
