"""
FlexSearch Backend - SQLAlchemy Models

Database models for Users, Projects, and Documents.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, TypeDecorator, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base

if TYPE_CHECKING:
    pass


class UserRole(str, enum.Enum):
    """INFRA_ADMIN: main_db.users (infra-hub). ADMIN: FlexSearch-only. USER: standard."""

    INFRA_ADMIN = "INFRA_ADMIN"
    ADMIN = "ADMIN"
    USER = "USER"


class RagMode(str, enum.Enum):
    """Mutually exclusive RAG pipeline mode per project."""

    VECTOR = "vector"
    GRAPH = "graph"



class StrEnumType(TypeDecorator):
    """Persist str enums by value; tolerate legacy rows stored as enum names."""

    impl = String
    cache_ok = True

    def __init__(self, enum_cls: type[enum.Enum], *, length: int) -> None:
        super().__init__(length)
        self.enum_cls = enum_cls
        self._by_value = {member.value: member for member in enum_cls}
        self._by_name = {member.name: member for member in enum_cls}

    def process_bind_param(self, value: enum.Enum | str | None, dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_cls):
            return value.value
        text = str(value)
        if text in self._by_name:
            return self._by_name[text].value
        return text.lower()

    def process_result_value(self, value: str | None, dialect) -> enum.Enum | None:
        if value is None:
            return None
        if value in self._by_value:
            return self._by_value[value]
        if value in self._by_name:
            return self._by_name[value]
        lowered = value.lower()
        if lowered in self._by_value:
            return self._by_value[lowered]
        raise LookupError(f"{value!r} is not a valid {self.enum_cls.__name__}")


class DocumentStatus(str, enum.Enum):
    """Document processing status pipeline."""

    UPLOADED = "uploaded"
    STORED = "stored"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    GRAPH_INDEXING = "graph_indexing"
    COMPLETED = "completed"
    FAILED = "failed"


class User(Base):
    """User model for authentication and authorization."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        default=UserRole.USER,
        nullable=False,
    )
    infra_hub_user_id: Mapped[int | None] = mapped_column(
        nullable=True,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    projects: Mapped[list["Project"]] = relationship(
        "Project",
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class Project(Base):
    """Project model - the unit of knowledge."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rag_config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    rag_mode: Mapped[RagMode] = mapped_column(
        StrEnumType(RagMode, length=16),
        default=RagMode.VECTOR,
        nullable=False,
    )
    graph_index_status: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner: Mapped["User"] = relationship(
        "User",
        back_populates="projects",
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Document(Base):
    """Document model - files uploaded to a project."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        StrEnumType(DocumentStatus, length=32),
        default=DocumentStatus.UPLOADED,
        nullable=False,
    )
    processing_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_text_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extraction_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="documents",
    )
