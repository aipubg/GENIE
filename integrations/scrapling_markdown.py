"""Scrapling HTML-to-Markdown pipeline (integrations/scrapling_markdown.py).

Uses Scrapling's real Adaptor parser to extract clean Markdown from HTML,
preserving document structure, links, images, and metadata while stripping
scripts, styles, and navigation chrome.

Pipeline: HTML -> Adaptor parse -> structural extraction -> clean Markdown
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from core.logging_setup import get_logger

log = get_logger("integrations.scrapling.markdown")


def _ensure_scrapling():
    """Return Scrapling's Adaptor, or None when scrapling is not installed here.

    Never mutates ``sys.path``: doing so shadowed the system ``vosk`` package with
    the managed venv's copy and broke unrelated tests via a Windows MAX_PATH error.
    The managed venv is a separate interpreter used by tests/external_optional,
    where scrapling is already importable.
    """
    try:
        from scrapling.parser import Adaptor
        return Adaptor
    except ImportError:
        return None


# Tags to strip entirely (with their content)
_STRIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template"}

# Tags that represent navigation/chrome to skip
_CHROME_SELECTORS = [
    "nav", "[role='navigation']", "header", "footer",
    ".sidebar", ".nav", ".menu", ".ad", ".advertisement",
    "#cookie-banner", ".cookie-consent",
]


def html_to_markdown(html: str, url: Optional[str] = None) -> Dict[str, Any]:
    """Convert raw HTML to clean Markdown using Scrapling's Adaptor.

    Args:
        html: Raw HTML string.
        url: Source URL for resolving relative links and metadata.

    Returns:
        Dict with keys: markdown, title, url, metadata, headings
    """
    Adaptor = _ensure_scrapling()
    page = Adaptor(html, auto_match=False)

    # Extract title
    title = ""
    title_els = page.css("title")
    if title_els:
        title = title_els[0].get_all_text(strip=True) if hasattr(title_els[0], 'get_all_text') else ""
    if not title:
        h1_els = page.css("h1")
        if h1_els:
            title = h1_els[0].get_all_text(strip=True) if hasattr(h1_els[0], 'get_all_text') else ""

    # Extract metadata
    metadata = _extract_metadata(page, url)

    # Extract body content, stripping chrome
    body_els = page.css("body")
    root = body_els[0] if body_els else page

    # Build markdown from content elements
    markdown = _element_to_markdown(root, url)

    # Clean up
    markdown = _clean_markdown(markdown)

    # Extract heading outline
    headings = _extract_headings(markdown)

    return {
        "markdown": markdown,
        "title": title,
        "url": url or "",
        "metadata": metadata,
        "headings": headings,
    }


def _extract_metadata(page: Any, url: Optional[str]) -> Dict[str, str]:
    """Extract page metadata from meta tags."""
    meta = {}
    meta_els = page.css("meta")
    for el in meta_els:
        attrs = el.attrib if hasattr(el, 'attrib') else {}
        name = attrs.get("name", "") or attrs.get("property", "")
        content = attrs.get("content", "")
        if name and content:
            meta[name] = content

    # Add canonical URL
    link_els = page.css("link[rel='canonical']")
    if link_els:
        href = link_els[0].attrib.get("href", "") if hasattr(link_els[0], 'attrib') else ""
        if href:
            meta["canonical"] = href
    elif url:
        meta["source_url"] = url

    return meta


def _element_to_markdown(element: Any, base_url: Optional[str]) -> str:
    """Recursively convert a Scrapling element tree to Markdown."""
    parts = []

    # Get all text content as a starting point
    # We use get_all_text for leaf-level extraction
    text = element.get_all_text(separator="\n", strip=True) if hasattr(element, 'get_all_text') else ""

    if not text:
        return ""

    # Process headings
    for level in range(1, 7):
        tag = f"h{level}"
        els = element.css(tag) if hasattr(element, 'css') else []
        for el in els:
            heading_text = el.get_all_text(strip=True) if hasattr(el, 'get_all_text') else ""
            if heading_text:
                prefix = "#" * level
                parts.append(f"\n{prefix} {heading_text}\n")

    # Process paragraphs
    p_els = element.css("p") if hasattr(element, 'css') else []
    for el in p_els:
        p_text = _inline_format(el, base_url)
        if p_text.strip():
            parts.append(f"\n{p_text.strip()}\n")

    # Process lists
    for list_tag in ("ul", "ol"):
        list_els = element.css(list_tag) if hasattr(element, 'css') else []
        for list_el in list_els:
            li_els = list_el.css("li") if hasattr(list_el, 'css') else []
            for i, li in enumerate(li_els):
                li_text = _inline_format(li, base_url)
                if list_tag == "ol":
                    parts.append(f"{i + 1}. {li_text.strip()}")
                else:
                    parts.append(f"- {li_text.strip()}")

    # Process code blocks
    pre_els = element.css("pre") if hasattr(element, 'css') else []
    for el in pre_els:
        code_text = el.get_all_text(strip=False) if hasattr(el, 'get_all_text') else ""
        if code_text.strip():
            parts.append(f"\n```\n{code_text.strip()}\n```\n")

    # Process blockquotes
    bq_els = element.css("blockquote") if hasattr(element, 'css') else []
    for el in bq_els:
        bq_text = el.get_all_text(strip=True) if hasattr(el, 'get_all_text') else ""
        if bq_text.strip():
            quoted = "\n".join(f"> {line}" for line in bq_text.strip().split("\n"))
            parts.append(f"\n{quoted}\n")

    # If no structured elements found, fall back to inline-formatted text.
    # This preserves links/images that live directly under the root (e.g. a body
    # that is a single anchor) instead of discarding the href via plain get_all_text.
    if not parts and text.strip():
        inline = _inline_format(element, base_url).strip()
        return inline or text.strip()

    return "\n".join(parts)


def _inline_format(element: Any, base_url: Optional[str]) -> str:
    """Format inline elements (links, bold, italic, images) within an element."""
    text = element.get_all_text(separator=" ", strip=True) if hasattr(element, 'get_all_text') else ""

    # Process links
    a_els = element.css("a") if hasattr(element, 'css') else []
    for a in a_els:
        attrs = a.attrib if hasattr(a, 'attrib') else {}
        href = attrs.get("href", "")
        link_text = a.get_all_text(strip=True) if hasattr(a, 'get_all_text') else ""
        if href and link_text:
            if base_url and not href.startswith(("http://", "https://", "mailto:", "#")):
                href = urljoin(base_url, href)
            md_link = f"[{link_text}]({href})"
            text = text.replace(link_text, md_link, 1)

    # Process images
    img_els = element.css("img") if hasattr(element, 'css') else []
    for img in img_els:
        attrs = img.attrib if hasattr(img, 'attrib') else {}
        src = attrs.get("src", "")
        alt = attrs.get("alt", "")
        if src:
            if base_url and not src.startswith(("http://", "https://", "data:")):
                src = urljoin(base_url, src)
            md_img = f"![{alt}]({src})"
            text += f"\n{md_img}\n"

    return text


def _clean_markdown(md: str) -> str:
    """Post-process generated Markdown for cleanliness."""
    # Remove excessive blank lines
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    # Remove trailing whitespace on lines
    md = re.sub(r"[ \t]+$", "", md, flags=re.MULTILINE)
    # Remove empty links
    md = re.sub(r"\[([^\]]*)\]\(\s*\)", r"\1", md)
    return md.strip()


def _extract_headings(markdown: str) -> list:
    """Extract heading outline from generated Markdown."""
    headings = []
    for line in markdown.split("\n"):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            headings.append({
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
            })
    return headings
