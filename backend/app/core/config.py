"""
FlexSearch Backend - Core Configuration

All environment variables are loaded here. Other modules import settings
from this file - never use os.getenv() directly elsewhere.
"""

import json
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @model_validator(mode="after")
    def assemble_postgres_url(self) -> "Settings":
        """Construct postgres_url if not provided explicitly."""
        if not self.postgres_url:
            self.postgres_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@"
                f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
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
    # EMBEDDING
    # =========================================================================
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Sentence transformer model for embeddings",
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
    retrieval_strategy: Literal["dense", "parent_child", "hybrid"] = Field(
        default="dense",
        description="Retrieval strategy",
    )
    reranking_strategy: Literal["none", "cross_encoder"] = Field(
        default="none",
        description="Reranking strategy",
    )

    # =========================================================================
    # LLM (via LiteLLM) - used only for VLM extraction strategy
    # =========================================================================
    model_name: str = Field(
        default="gpt-4o-mini",
        description="LLM model name (LiteLLM format)",
    )
    api_key: str = Field(
        default="",
        description="LLM API key",
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

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS from comma-separated text or JSON array."""
        raw = self.cors_origins.strip()
        if not raw:
            return []
        if raw.startswith("["):
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("CORS_ORIGINS JSON value must be a list")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

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
