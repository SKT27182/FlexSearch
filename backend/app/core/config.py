"""
FlexSearch Backend - Core Configuration

All environment variables are loaded here. Other modules import settings
from this file - never use os.getenv() directly elsewhere.
"""

from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        # Look for .env in current dir, parent dir, or /app dir (for Docker)
        env_file=(".env", "../.env", "/app/.env"),
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
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )


# Singleton settings instance
settings = Settings()
