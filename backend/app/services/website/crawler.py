"""Async website crawler using BFS traversal (robots-aware)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from fnmatch import fnmatch
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.website.content_extractor import extract_clean_content
from app.services.website.schemas import CrawledPage
from app.utils.logger import create_logger

logger = create_logger(__name__)

USER_AGENT = "FlexSearch-Crawler/1.0"

_SKIP_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".mp4",
    ".mp3",
    ".avi",
}


def normalise_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _is_same_domain(base_url: str, candidate_url: str) -> bool:
    return urlparse(base_url).netloc == urlparse(candidate_url).netloc


def _is_crawlable_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)


def _matches_exclude_pattern(
    url: str, exclude_patterns: list[str] | None = None
) -> bool:
    if not exclude_patterns:
        return False
    path = urlparse(url).path
    return any(fnmatch(path, pattern) for pattern in exclude_patterns)


def _extract_links(html: str, base_url: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = str(tag["href"])
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        normalised = normalise_url(absolute)
        if _is_same_domain(base_url, normalised) and _is_crawlable_url(normalised):
            links.append(normalised)
    return links


def _extract_title(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else ""
    except Exception:
        return ""


async def _fetch_robots_parser(
    base_url: str, client: httpx.AsyncClient
) -> Optional[RobotFileParser]:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=10)
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
    except Exception as exc:
        logger.warning("Could not fetch robots.txt from %s: %s", robots_url, exc)
    return None


async def crawl_website(
    start_url: str,
    *,
    max_depth: int = 2,
    max_pages: int = 50,
    rate_limit: float = 0.5,
    respect_robots: bool = True,
    exclude_patterns: list[str] | None = None,
    use_sitemap: bool = True,
) -> AsyncGenerator[CrawledPage, None]:
    """BFS crawl yielding each successfully fetched HTML page."""
    from app.core.config import settings
    from app.services.url_safety import UnsafeURLError, assert_public_url

    if settings.crawl_block_private_urls:
        try:
            assert_public_url(start_url)
        except UnsafeURLError as exc:
            raise ValueError(f"Unsafe crawl start URL: {exc}") from exc

    normalised_start = normalise_url(start_url)
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(normalised_start, 0)])
    pages_fetched = 0

    async with httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    ) as client:
        robots: Optional[RobotFileParser] = None
        if respect_robots:
            robots = await _fetch_robots_parser(normalised_start, client)

        if use_sitemap:
            from app.services.website.sitemap import discover_sitemap_urls

            sitemap_urls = await discover_sitemap_urls(normalised_start, client)
            logger.info("Found %d URLs from sitemap.xml", len(sitemap_urls))
            for surl in sitemap_urls:
                normalised_surl = normalise_url(surl)
                if (
                    normalised_surl not in visited
                    and _is_crawlable_url(normalised_surl)
                    and not _matches_exclude_pattern(normalised_surl, exclude_patterns)
                    and _is_same_domain(normalised_start, normalised_surl)
                ):
                    if settings.crawl_block_private_urls and not _url_is_safe(
                        normalised_surl
                    ):
                        continue
                    queue.append((normalised_surl, 0))

        while queue and pages_fetched < max_pages:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            if _matches_exclude_pattern(url, exclude_patterns):
                continue
            if robots and not robots.can_fetch(USER_AGENT, url):
                logger.debug("Blocked by robots.txt: %s", url)
                continue
            if settings.crawl_block_private_urls and not _url_is_safe(url):
                logger.warning("SSRF blocked crawl URL: %s", url)
                continue

            try:
                resp = await client.get(url, timeout=30)
                # Re-validate redirect targets
                hops = 0
                while resp.is_redirect and hops < 5:
                    location = resp.headers.get("location")
                    if not location:
                        break
                    next_url = normalise_url(str(httpx.URL(url).join(location)))
                    if not _is_same_domain(normalised_start, next_url):
                        logger.warning("Redirect left domain: %s → %s", url, next_url)
                        resp = None  # type: ignore[assignment]
                        break
                    if settings.crawl_block_private_urls and not _url_is_safe(next_url):
                        logger.warning("SSRF blocked redirect: %s", next_url)
                        resp = None  # type: ignore[assignment]
                        break
                    url = next_url
                    if url in visited:
                        resp = None  # type: ignore[assignment]
                        break
                    visited.add(url)
                    resp = await client.get(url, timeout=30)
                    hops += 1
                if resp is None:
                    continue
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    continue
                if resp.status_code != 200:
                    logger.warning("Non-200 (%s) for %s", resp.status_code, url)
                    continue
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", url, exc)
                continue

            html = resp.text
            title = _extract_title(html) or url
            content = extract_clean_content(html)
            pages_fetched += 1
            logger.info(
                "Crawled [%d/%d] (depth=%d): %s",
                pages_fetched,
                max_pages,
                depth,
                url,
            )
            yield CrawledPage(
                url=url,
                title=title,
                content=content,
                depth=depth,
                html=html,
            )

            if rate_limit > 0:
                await asyncio.sleep(rate_limit)

            if depth < max_depth:
                for link in _extract_links(html, url):
                    if (
                        link not in visited
                        and not _matches_exclude_pattern(link, exclude_patterns)
                        and _is_same_domain(normalised_start, link)
                        and _is_crawlable_url(link)
                    ):
                        if settings.crawl_block_private_urls and not _url_is_safe(link):
                            continue
                        queue.append((link, depth + 1))


def _url_is_safe(url: str) -> bool:
    from app.services.url_safety import is_safe_public_url

    return is_safe_public_url(url)
