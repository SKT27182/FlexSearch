"""Website crawler (BFS + robots) → shared document ingest."""

from app.services.website.schemas import CrawledPage, WebsiteCrawlRequest

__all__ = ["CrawledPage", "WebsiteCrawlRequest"]
