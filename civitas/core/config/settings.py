"""
civitas.core.config.settings
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Platform-wide configuration via Pydantic Settings.

Settings are loaded from environment variables with an optional
.env file. All secrets are excluded from repr/logging.

Usage:
    from civitas.core.config.settings import get_settings
    settings = get_settings()
    db_url = settings.postgres.connection_url
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────────────────────
#  SUB-SETTINGS
# ─────────────────────────────────────────────────────────────

class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=".env")

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    db: str = Field(default="civitas_knowledge")
    user: str = Field(default="civitas")
    password: SecretStr = Field(default=SecretStr("change_me"))
    pool_size: int = Field(default=10, ge=1, le=50)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout: int = Field(default=30, ge=5)

    @computed_field
    @property
    def connection_url(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )

    @computed_field
    @property
    def async_connection_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )


class VectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VECTOR_", env_file=".env")

    dimension: int = Field(default=1536, ge=64, le=4096)
    similarity_metric: str = Field(default="cosine")   # cosine | l2 | inner_product
    index_type: str = Field(default="ivfflat")          # ivfflat | hnsw
    ivfflat_lists: int = Field(default=100)
    hnsw_m: int = Field(default=16)
    hnsw_ef_construction: int = Field(default=64)


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=".env")

    provider: str = Field(default="openai")             # openai | huggingface | local
    model: str = Field(default="text-embedding-3-small")
    batch_size: int = Field(default=100, ge=1, le=2000)
    timeout_seconds: int = Field(default=60)
    retry_attempts: int = Field(default=3)


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env")

    provider: str = Field(default="openai")
    model: str = Field(default="gpt-4o-mini")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=256)


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", env_file=".env")

    api_key: Optional[SecretStr] = Field(default=None)
    organization: Optional[str] = Field(default=None)


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGESTION_", env_file=".env")

    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=512)
    batch_size: int = Field(default=50, ge=1, le=500)
    max_workers: int = Field(default=4, ge=1, le=32)
    watch_dirs: Optional[str] = Field(default=None)


class IndexingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INDEX_", env_file=".env")

    default_top_k: int = Field(default=10, ge=1, le=200)
    rebuild_on_startup: bool = Field(default=False)
    enable_keyword_index: bool = Field(default=True)
    enable_summary_index: bool = Field(default=True)
    enable_graph_index: bool = Field(default=False)


class GraphRAGSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRAPHRAG_", env_file=".env")

    enabled: bool = Field(default=True)
    entity_extraction_model: str = Field(default="gpt-4o-mini")
    max_cluster_size: int = Field(default=10, ge=2)
    community_reports: bool = Field(default=True)
    min_edge_weight: float = Field(default=0.5, ge=0.0, le=1.0)


class QualitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUALITY_", env_file=".env")

    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    auto_reject_below: float = Field(default=0.2, ge=0.0, le=1.0)
    check_on_ingestion: bool = Field(default=True)
    min_word_count: int = Field(default=50, ge=0)
    max_word_count: int = Field(default=500_000, ge=1)


class GovernanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOVERNANCE_", env_file=".env")

    audit_enabled: bool = Field(default=True)
    retention_default_days: int = Field(default=365, ge=1)
    require_approval: bool = Field(default=False)


# ─────────────────────────────────────────────────────────────
#  MAIN SETTINGS
# ─────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    Root settings object for the CIVITAS knowledge platform.

    All configuration is sourced from environment variables.
    Sensitive values are stored as SecretStr.
    """

    model_config = SettingsConfigDict(
        env_prefix="CIVITAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ────────────────────────────────────────────
    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="text")   # text | json
    secret_key: SecretStr = Field(default=SecretStr("change_me_in_production"))

    # ── Sub-settings ───────────────────────────────────────────
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    indexing: IndexingSettings = Field(default_factory=IndexingSettings)
    graphrag: GraphRAGSettings = Field(default_factory=GraphRAGSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @computed_field
    @property
    def is_development(self) -> bool:
        return self.env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.
    Cached after first call — reload by calling get_settings.cache_clear().
    """
    return Settings()
