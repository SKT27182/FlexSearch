"""Website crawl request / page schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class WebsiteCrawlRequest(BaseModel):
    url: HttpUrl
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=500)
    exclude_patterns: list[str] | None = None
    respect_robots: bool | None = None
    use_sitemap: bool | None = None
    rate_limit: float | None = Field(default=None, ge=0, le=30)


class CrawledPage(BaseModel):
    url: str
    title: str
    depth: int
    content: str
    html: str = ""


class WebsiteCrawlSubmitResponse(BaseModel):
    job_id: str
    status: str = "queued"
    project_id: str
