"""Clean content extraction from HTML (trafilatura + BeautifulSoup fallback)."""

from __future__ import annotations

from app.utils.logger import create_logger

logger = create_logger(__name__)

_BOILERPLATE_TAGS = {"nav", "header", "footer", "aside", "script", "style", "noscript"}


def extract_clean_content(html: str, url: str | None = None) -> str:
    """Extract main page content as Markdown (or plain text fallback)."""
    if not html or not html.strip():
        return ""

    try:
        import trafilatura

        content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            url=url,
        )
        if content and content.strip():
            return content.strip()
    except Exception as exc:
        logger.debug("trafilatura extract failed: %s", exc)

    return _fallback_extract(html)


def _fallback_extract(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Last resort: strip tags naively
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return " ".join(text.split())

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_BOILERPLATE_TAGS):
        tag.decompose()

    # Prefer article / main
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines: list[str] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "th"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        name = el.name.lower()
        if name == "h1":
            lines.append(f"# {text}")
        elif name == "h2":
            lines.append(f"## {text}")
        elif name == "h3":
            lines.append(f"### {text}")
        elif name == "h4":
            lines.append(f"#### {text}")
        elif name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    if lines:
        return "\n\n".join(lines)
    return root.get_text("\n", strip=True)
