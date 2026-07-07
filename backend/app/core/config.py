"""
FlexSearch Backend - Core Configuration

All environment variables are loaded here. Other modules import settings
from this file - never use os.getenv() directly elsewhere.
"""

import json
from typing import Literal, Optional
from urllib.parse import quote_plus, urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.model_ids import is_local_embedding_model


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        # Look for backend-local .env first, then common fallbacks
        env_file=("backend/.env", ".env", "../.env", "/app/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # =========================================================================
    # DATABASE
    # =========================================================================
    # No defaults for sensitive/environment-specific fields to force picking from .env
    postgres_user: str = Field(description="PostgreSQL user")
    postgres_password: str = Field(description="PostgreSQL password")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="flexsearch")

    # This will be constructed if not provided
    postgres_url: Optional[str] = Field(
        default=None,
        description="Full PostgreSQL connection URL (overrides individual components if provided)",
    )

    # infra-hub main_db (read-only for admin auth; credentials never stored in FlexSearch)
    infra_hub_postgres_db: str = Field(default="main_db")
    infra_hub_postgres_url: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def assemble_postgres_urls(self) -> "Settings":
        """Construct postgres_url and infra-hub URL (same host/credentials, main_db)."""
        from sqlalchemy.engine import make_url

        if not self.postgres_url:
            self.postgres_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@"
                f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )

        if not self.infra_hub_postgres_url:
            # Use credentials from POSTGRES_URL when set (avoids POSTGRES_PASSWORD drift).
            base = make_url(self.postgres_url)
            infra = base.set(database=self.infra_hub_postgres_db)
            driver = infra.drivername.split("+", 1)[0]
            self.infra_hub_postgres_url = infra.set(
                drivername=driver
            ).render_as_string(hide_password=False)

        if not self.redis_url:
            pwd = quote_plus(self.redis_password)
            self.redis_url = (
                f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
            )

        return self

    # =========================================================================
    # QDRANT
    # =========================================================================
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="Qdrant server URL",
    )
    qdrant_api_key: str = Field(
        default="",
        description="Qdrant API key (optional)",
    )
    qdrant_http_port: int = Field(
        default=6333,
        description="Qdrant HTTP port for public/admin links",
    )
    qdrant_grpc_port: int = Field(
        default=6334,
        description="Qdrant gRPC port",
    )
    qdrant_hnsw_m: int = Field(
        default=16,
        description="HNSW graph connections parameter",
    )
    qdrant_hnsw_ef: int = Field(
        default=100,
        description="HNSW search parameter",
    )

    # =========================================================================
    # MINIO (S3-compatible storage)
    # =========================================================================
    minio_endpoint: str = Field(
        default="localhost:9000",
        description="MinIO server endpoint",
    )
    minio_access_key: str = Field(
        description="MinIO access key",
    )
    minio_secret_key: str = Field(
        description="MinIO secret key",
    )
    minio_bucket: str = Field(
        default="flexsearch",
        description="MinIO bucket name",
    )
    minio_api_port: int = Field(
        default=9000,
        description="MinIO API port for public/admin links",
    )
    minio_console_port: int = Field(
        default=9001,
        description="MinIO Console port for public/admin links",
    )
    minio_secure: bool = Field(
        default=False,
        description="Use HTTPS for MinIO",
    )

    # =========================================================================
    # REDIS (document status pub/sub + SSE; align with infra-hub backend/.env)
    # =========================================================================
    redis_host: str = Field(
        default="127.0.0.1",
        description="Redis host (infra-hub: localhost from host machine)",
    )
    redis_port: int = Field(
        default=63791,
        description="Redis port (infra-hub REDIS_PORT, host-mapped)",
    )
    redis_password: str = Field(
        default="password",
        description="Redis password (infra-hub REDIS_PASSWORD)",
    )
    redis_db: int = Field(
        default=0,
        description="Redis database index",
    )
    redis_url: Optional[str] = Field(
        default=None,
        description="Full Redis URL (overrides host/port/password/db if set)",
    )

    # =========================================================================
    # NEO4J (Graph RAG; provisioned in infra-hub)
    # =========================================================================
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j Bolt URI",
    )
    neo4j_user: str = Field(
        default="neo4j",
        description="Neo4j username",
    )
    neo4j_password: str = Field(
        default="password",
        description="Neo4j password",
    )
    neo4j_http_port: int = Field(
        default=7474,
        description="Neo4j Browser HTTP port (infra-hub admin)",
    )

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================
    jwt_secret: str = Field(
        description="JWT signing secret",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm",
    )
    jwt_expire_minutes: int = Field(
        default=60,
        description="JWT token expiration in minutes",
    )

    # =========================================================================
    # LLM (via LiteLLM) - VLM extraction and Graph RAG entity extraction
    # =========================================================================
    llm_api_base: str = Field(
        default="",
        description="LiteLLM base URL/proxy for MODEL_NAME (blank = provider default)",
    )
    model_name: str = Field(
        default="gpt-4o-mini",
        description="LLM model name (LiteLLM format)",
    )
    api_key: str = Field(
        default="",
        description="LLM API key",
    )

    # =========================================================================
    # EMBEDDING (vector RAG via LiteLLM API or local sentence-transformers)
    # =========================================================================
    embedding_api_base: str = Field(
        default="",
        description="LiteLLM base URL for EMBEDDING_MODEL API calls",
    )
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description=(
            "Embedding model for vector RAG. Use sentence-transformers/... for local "
            "models or a LiteLLM id (e.g. openai/text-embedding-3-small)."
        ),
    )
    embedding_api_key: str = Field(
        default="",
        description="API key for LiteLLM embedding models (defaults to API_KEY)",
    )

    # =========================================================================
    # RAG STRATEGIES
    # =========================================================================
    extraction_strategy: Literal["ocr", "vlm"] = Field(
        default="ocr",
        description="Document extraction strategy",
    )
    chunking_strategy: Literal[
        "fixed_window", "recursive", "semantic", "parent_child"
    ] = Field(
        default="fixed_window",
        description="Text chunking strategy",
    )
    retrieval_strategy: Literal["dense", "parent_child", "hybrid", "bm25"] = Field(
        default="dense",
        description="Retrieval strategy",
    )
    reranking_strategy: Literal["none", "cross_encoder"] = Field(
        default="none",
        description="Reranking strategy",
    )

    # =========================================================================
    # GRAPH RAG (Microsoft GraphRAG)
    # =========================================================================
    graph_indexing_enabled: bool = Field(
        default=True,
        description="Global kill switch for GraphRAG indexing jobs",
    )
    graphrag_community_level: int = Field(
        default=2,
        ge=0,
        le=4,
        description="Default GraphRAG community level for indexing and search",
    )
    graphrag_embedding_api_base: str = Field(
        default="",
        description=(
            "LiteLLM base URL for GraphRAG embeddings (defaults to EMBEDDING_API_BASE)"
        ),
    )
    graphrag_embedding_model: str = Field(
        default="openai/text-embedding-3-small",
        description=(
            "LiteLLM embedding model for Microsoft GraphRAG indexing "
            "(API-only; separate from MODEL_NAME and EMBEDDING_MODEL)"
        ),
    )
    graphrag_embedding_api_key: str = Field(
        default="",
        description=(
            "API key for GraphRAG embedding model (defaults to EMBEDDING_API_KEY "
            "then API_KEY; separate from vector embedding when set)"
        ),
    )
    graphrag_concurrent_requests: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Max parallel LLM calls during Microsoft GraphRAG indexing",
    )
    graphrag_rate_limit_max_retries: int = Field(
        default=30,
        ge=1,
        le=200,
        description="How many times to sleep-and-retry after HTTP 429 during GraphRAG",
    )
    graphrag_rate_limit_default_wait_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Fallback wait when a 429 response has no Retry-After header",
    )
    graphrag_rate_limit_max_wait_seconds: float = Field(
        default=300.0,
        ge=1.0,
        le=3600.0,
        description="Cap on Retry-After sleep duration for GraphRAG rate limits",
    )

    # =========================================================================
    # APPLICATION
    # =========================================================================
    debug: bool = Field(
        default=True,
        description="Debug mode",
    )
    api_port: int = Field(
        default=8889,
        description="Backend API port",
    )
    app_public_url: Optional[str] = Field(
        default=None,
        description="Public HTTPS URL for this app (added to CORS automatically)",
    )
    app_public_host: Optional[str] = Field(
        default=None,
        description="Public hostname for generated links (overrides SERVICE_PUBLIC_HOST)",
    )
    service_public_host: str = Field(
        default="localhost",
        description="Public host used in generated service links",
    )
    cors_origins: str = Field(
        default="http://localhost:5144,http://127.0.0.1:5144",
        description="Allowed CORS origins (comma-separated or JSON list)",
    )
    # Service metadata (only services used by FlexSearch)
    postgres_service_name: str = Field(default="postgres")
    qdrant_service_name: str = Field(default="qdrant")
    minio_service_name: str = Field(default="minio")
    postgres_display_name: str = Field(default="PostgreSQL")
    qdrant_display_name: str = Field(default="Qdrant")
    minio_display_name: str = Field(default="MinIO")
    postgres_container_name: str = Field(default="infra-postgres")
    qdrant_container_name: str = Field(default="infra-qdrant")
    minio_container_name: str = Field(default="infra-minio")
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )

    @model_validator(mode="after")
    def apply_blank_env_defaults(self) -> "Settings":
        """Treat blank .env values as unset so Field defaults apply."""
        if not self.graphrag_embedding_model.strip():
            self.graphrag_embedding_model = "openai/text-embedding-3-small"
        if (
            not self.embedding_api_key.strip()
            and not self.api_key.strip()
            and not is_local_embedding_model(self.embedding_model)
        ):
            self.embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        return self

    @model_validator(mode="after")
    def apply_public_app_settings(self) -> "Settings":
        if self.app_public_host:
            self.service_public_host = self.app_public_host
        elif self.app_public_url:
            hostname = urlparse(self.app_public_url).hostname
            if hostname:
                self.service_public_host = hostname
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS from comma-separated text or JSON array."""
        raw = self.cors_origins.strip()
        if not raw:
            origins: list[str] = []
        elif raw.startswith("["):
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("CORS_ORIGINS JSON value must be a list")
            origins = [str(item).strip() for item in parsed if str(item).strip()]
        else:
            origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

        if self.app_public_url:
            public_origin = self.app_public_url.rstrip("/")
            if public_origin not in origins:
                origins.append(public_origin)
        return origins

    @property
    def qdrant_public_url(self) -> str:
        """Public Qdrant URL based on deploy host and configured port."""
        return f"http://{self.service_public_host}:{self.qdrant_http_port}"

    @property
    def minio_public_url(self) -> str:
        """Public MinIO API URL based on deploy host and configured port."""
        return f"http://{self.service_public_host}:{self.minio_api_port}"

    @property
    def minio_console_url(self) -> str:
        """Public MinIO Console URL based on deploy host and configured port."""
        return f"http://{self.service_public_host}:{self.minio_console_port}"

    @property
    def admin_urls(self) -> dict[str, str]:
        """Centralized service links for admin/UI usage."""
        return {
            "qdrant": self.qdrant_public_url,
            "minio_api": self.minio_public_url,
            "minio_console": self.minio_console_url,
        }


# Singleton settings instance
settings = Settings()
