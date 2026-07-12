"""Discover URLs from sitemap.xml / sitemap index."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from app.utils.logger import create_logger

logger = create_logger(__name__)

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


async def discover_sitemap_urls(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    limit: int = 500,
) -> list[str]:
    parsed = urlparse(base_url)
    candidates = [
        f"{parsed.scheme}://{parsed.netloc}/sitemap.xml",
        f"{parsed.scheme}://{parsed.netloc}/sitemap_index.xml",
    ]
    found: list[str] = []
    for sitemap_url in candidates:
        try:
            resp = await client.get(sitemap_url, timeout=15)
            if resp.status_code != 200:
                continue
            found.extend(_parse_sitemap_xml(resp.text, limit=limit - len(found)))
            if len(found) >= limit:
                break
        except Exception as exc:
            logger.debug("Sitemap fetch failed for %s: %s", sitemap_url, exc)
    return found[:limit]


def _parse_sitemap_xml(xml_text: str, *, limit: int) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for loc in root.findall(".//sm:url/sm:loc", _NS):
        if loc.text:
            urls.append(loc.text.strip())
            if len(urls) >= limit:
                return urls
    for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        if loc.text and loc.text.strip() not in urls:
            text = loc.text.strip()
            if text.endswith(".xml") and "sitemap" in text.lower():
                continue
            urls.append(text)
            if len(urls) >= limit:
                return urls
    if not urls:
        for loc in root.iter():
            if loc.tag.endswith("loc") and loc.text:
                urls.append(loc.text.strip())
                if len(urls) >= limit:
                    break
    return urls
