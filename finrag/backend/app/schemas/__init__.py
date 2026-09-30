"""schemas 包：接口请求/响应模型。"""

from app.schemas.common import (
    ApiResponse,
    HealthComponent,
    HealthResponse,
    PageMeta,
    PageResult,
)
from app.schemas.knowledge import (
    ChunkOut,
    DocumentOut,
    DocumentUpdateRequest,
    IngestResultOut,
    IngestTaskOut,
    IngestTextRequest,
    KnowledgeStats,
    TagOut,
)
from app.schemas.qa import (
    AskRequest,
    AskResponse,
    Citation,
    Credibility,
    FavoriteCreateRequest,
    FavoriteItem,
    FavoriteUpdateRequest,
    HistoryItem,
    RetrievalTrace,
)

__all__ = [
    "ApiResponse",
    "PageMeta",
    "PageResult",
    "HealthComponent",
    "HealthResponse",
    "AskRequest",
    "AskResponse",
    "Citation",
    "Credibility",
    "RetrievalTrace",
    "HistoryItem",
    "FavoriteCreateRequest",
    "FavoriteUpdateRequest",
    "FavoriteItem",
    "DocumentOut",
    "ChunkOut",
    "TagOut",
    "KnowledgeStats",
    "IngestTextRequest",
    "IngestResultOut",
    "IngestTaskOut",
    "DocumentUpdateRequest",
]
