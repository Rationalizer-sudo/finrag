"""全局配置模块。

所有可调参数集中在 :class:`Settings` 中，通过环境变量 / ``.env`` 文件注入，
避免散落在代码里的魔法数字（课题要求：PEP8 + 工程化规范）。

配置对象通过 :func:`get_settings` 以单例方式获取，便于测试时用
``get_settings.cache_clear()`` 重置。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/app/core/config.py -> backend/app/core -> backend/app
#             -> backend -> <项目根>
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """FinRAG 全量运行配置。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ 应用
    app_name: str = "FinRAG"
    app_env: Literal["dev", "test", "prod"] = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"
    )

    # ------------------------------------------------------- 通义千问 / LLM
    dashscope_api_key: str = Field(default="", repr=False)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen-plus"
    qwen_embedding_model: str = "text-embedding-v3"
    qwen_embedding_dim: int = 1024
    qwen_temperature: float = 0.1
    qwen_timeout: int = 60
    qwen_max_retries: int = 2

    # ------------------------------------------------------------- MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "finrag"
    mysql_password: str = Field(default="finrag_pwd", repr=False)
    mysql_db: str = "finrag"
    mysql_charset: str = "utf8mb4"
    mysql_pool_size: int = 10
    mysql_max_overflow: int = 20
    mysql_echo: bool = False
    # 测试用：直接指定完整连接串（如 sqlite:///:memory:），优先级高于上面各项
    database_url_override: str = ""

    # ------------------------------------------------------------ Milvus
    milvus_host: str = "127.0.0.1"
    milvus_port: int = 19530
    milvus_user: str = ""
    milvus_password: str = Field(default="", repr=False)
    milvus_collection: str = "finrag_clause"
    milvus_index_type: Literal["HNSW", "IVF_FLAT", "IVF_SQ8"] = "HNSW"
    milvus_metric_type: Literal["COSINE", "IP", "L2"] = "COSINE"
    milvus_hnsw_m: int = 16
    milvus_hnsw_ef_construction: int = 200
    milvus_hnsw_ef_search: int = 64
    milvus_consistency_level: Literal["Strong", "Bounded", "Eventually"] = "Bounded"

    # --------------------------------------------------- 切分与检索参数
    chunk_size: int = 500
    chunk_overlap: int = 80
    retrieve_top_k: int = 5
    retrieve_candidate_k: int = 20
    retrieve_mode: Literal["vector", "bm25", "hybrid"] = "hybrid"
    rrf_k: int = 60
    # 向量相似度下限：低于该值的候选视为「不相关」直接丢弃。
    # 这是「资料不足必须如实说明」的工程保障——宁可回答「未检索到相关规定」，
    # 也不要拿一段不相关的条文去让大模型硬凑答案。
    # 语义相关对通常在 0.5 以上，不相关对多在 0.2~0.4，故默认取 0.30，可按模型实测调整。
    min_vector_score: float = 0.30
    verify_answer_accuracy_target: float = 0.85
    response_time_budget_ms: int = 2000

    # -------------------------------------------------------------- OCR
    ocr_enabled: bool = True
    tesseract_cmd: str = ""
    tesseract_lang: str = "chi_sim+eng"
    ocr_dpi: int = 300
    pdf_image_fallback: bool = True

    # ------------------------------------------------------ 上传与安全
    upload_dir: str = "./var/uploads"
    max_upload_mb: int = 50
    allowed_extensions: str = ".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.bmp,.tif,.tiff"
    jwt_secret: str = Field(default="change-me-in-production", repr=False)
    jwt_expire_minutes: int = 720

    # -------------------------------------------------------- 派生属性
    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_lt_size(cls, v: int, info) -> int:
        """overlap 必须小于 chunk_size，否则切分将死循环。"""
        size = info.data.get("chunk_size", 500)
        if v >= size:
            raise ValueError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE")
        return v

    @property
    def cors_origin_list(self) -> List[str]:
        """CORS 白名单列表。"""
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def allowed_extension_set(self) -> set[str]:
        """允许上传的扩展名集合（统一小写、带点）。"""
        return {
            item.strip().lower()
            for item in self.allowed_extensions.split(",")
            if item.strip()
        }

    @property
    def max_upload_bytes(self) -> int:
        """上传大小上限（字节）。"""
        return self.max_upload_mb * 1024 * 1024

    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串；生产为 MySQL，测试可整体覆盖。"""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"
            f"?charset={self.mysql_charset}"
        )

    @property
    def upload_path(self) -> Path:
        """上传目录的绝对路径（不存在时自动创建）。"""
        raw = Path(self.upload_dir)
        path = raw if raw.is_absolute() else (PROJECT_ROOT / raw)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_testing(self) -> bool:
        """是否处于测试环境。"""
        return self.app_env == "test"

    def ensure_dashscope_key(self) -> None:
        """调用大模型前校验 API Key，缺失时快速失败并给出可操作提示。"""
        if not self.dashscope_api_key or self.dashscope_api_key.startswith("sk-xxx"):
            raise RuntimeError(
                "未配置有效的 DASHSCOPE_API_KEY。请在 .env 中填写通义千问 API Key，"
                "或在阿里云百炼控制台申请后重新启动服务。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一配置实例（带缓存）。"""
    return Settings()


def reset_settings_cache() -> None:
    """清空配置缓存，供测试修改环境变量后重新加载。"""
    get_settings.cache_clear()


# 兼容以 ``from app.core.config import settings`` 方式直接引用
settings = get_settings()


def env_flag(name: str, default: bool = False) -> bool:
    """读取布尔型环境变量（``1/true/yes/on`` 视为真）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["Settings", "get_settings", "reset_settings_cache", "settings", "env_flag",
           "PROJECT_ROOT"]
