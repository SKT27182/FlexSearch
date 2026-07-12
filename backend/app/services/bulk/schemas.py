"""Schemas for .ragpack bulk import/export."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, HttpUrl


class FileDocumentReference(BaseModel):
    type: Literal["file"] = "file"
    path: str
    title: str | None = None


class UrlDocumentReference(BaseModel):
    type: Literal["url"] = "url"
    url: HttpUrl
    title: str | None = None


class TextDocumentReference(BaseModel):
    type: Literal["text"] = "text"
    path: str
    title: str | None = None
    content_type: str = "text/markdown"


DocumentReference = Annotated[
    Union[FileDocumentReference, UrlDocumentReference, TextDocumentReference],
    Field(discriminator="type"),
]


class ProjectImportDef(BaseModel):
    name: str
    description: str | None = None
    documents: list[DocumentReference] = Field(default_factory=list)


class BulkImportManifest(BaseModel):
    version: str = "1.0"
    projects: list[ProjectImportDef] = Field(default_factory=list)


class BulkImportSubmitResponse(BaseModel):
    job_id: str
    status: str = "queued"
    project_id: str | None = None
