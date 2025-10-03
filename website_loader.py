from __future__ import annotations
import datetime as dt
from typing import Iterable, Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

import trafilatura
import trafilatura.sitemaps as sitemaps
from langchain_core.document_loaders import BaseLoader
from lxml import html as lxml_html, etree

# LangChain import (supports new and old names)
try:
    from langchain_core.documents import Document
except ImportError:
    from langchain.schema import Document  # older LangChain versions


# ----------------------- logging setup -----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("trafilatura_ingest")
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


# ----------------------- config -----------------------
INGEST_DATE = dt.date.today().isoformat()

# Central place to tune Trafilatura for RAG
TRAFILATURA_DEFAULTS: Dict[str, Any] = {
    "output_format": "markdown",   # preserve headings/lists -> better chunking & retrieval
    "include_comments": False,
    "include_tables": True,       # flip to True if tables are important for your data
    "include_images": False,
    "include_formatting": False,   # only useful for XML outputs
    "include_links": True,        # links often add noise to embeddings
    "favor_precision": True,       # prefer cleaner/shorter over noisy/long
    "favor_recall": False,
    "deduplicate": True,
    "target_language": None,       # unknown ahead of time; accept both de/en/etc.
}

def _extract_markdown(html_or_fragment: str, url: Optional[str], opts: Optional[Dict[str, Any]]) -> Optional[str]:
    """Trafilatura extraction with centralized, overrideable defaults."""
    params = {**TRAFILATURA_DEFAULTS, **(opts or {})}
    return trafilatura.extract(html_or_fragment, url=url, **params)


# ----------------------- helpers -----------------------
def _to_doc(url: str, text: str, meta_obj) -> Document:
    """Standardize LangChain Document + metadata."""
    title = getattr(meta_obj, "title", None) if meta_obj else None
    author = getattr(meta_obj, "author", None) if meta_obj else None
    published = getattr(meta_obj, "date", None) if meta_obj else None
    return Document(
        page_content=text.strip(),
        metadata={
            "source": url,
            "title": title,
            "author": author,
            "published": published,
            "ingested_at": INGEST_DATE,
        },
    )


def _scoped_text(downloaded_html: str, css_selector: Optional[str], url: Optional[str],
                 t_opts: Optional[Dict[str, Any]]) -> Optional[str]:
    """Use lxml to scope to CSS selector; run the matched fragment(s) through Trafilatura for cleanup."""
    if not css_selector:
        return None
    try:
        root = lxml_html.fromstring(downloaded_html)
        nodes = root.cssselect(css_selector)
        if not nodes:
            return None

        parts: List[str] = []
        for n in nodes:
            fragment_html = etree.tostring(n, encoding="unicode")  # HTML of just that subtree
            cleaned = _extract_markdown(fragment_html, url, t_opts)
            if cleaned:
                parts.append(cleaned.strip())

        return "\n\n".join(parts).strip() if parts else None
    except Exception as e:
        logger.error("Selector parse/extract failed (%s): %s", css_selector, e)
        return None


def _extract_one(
    url: str,
    min_words: int = 40,
    css_selector: Optional[str] = None,
    t_opts: Optional[Dict[str, Any]] = None,
) -> Optional[Document]:
    """Fetch + extract for a single URL, optionally scoping to a CSS selector."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("Skipping (no content fetched): %s", url)
            return None

        # 1) Try scoped extraction if selector provided
        text: Optional[str] = _scoped_text(downloaded, css_selector, url, t_opts)

        # 2) Fallback to Trafilatura full extraction if no scoped text
        if not text:
            text = _extract_markdown(downloaded, url, t_opts)

        if not text or len(text.split()) < min_words:
            logger.debug("Too short/empty: %s", url)
            return None

        meta = trafilatura.extract_metadata(downloaded, default_url=url)
        logger.info("✓ Extracted %d words from %s", len(text.split()), url)
        return _to_doc(url, text, meta)
    except Exception as e:
        logger.error("Error extracting %s: %s", url, e)
        return None


def _extract_many(
    urls: Iterable[str],
    max_workers: int,
    min_words: int,
    css_selector: Optional[str],
    t_opts: Optional[Dict[str, Any]],
) -> List[Document]:
    """Concurrent extraction, deduping by URL."""
    docs: List[Document] = []
    seen = set()
    urls = list(urls)
    logger.info("Starting extraction for %d URLs", len(urls))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_extract_one, u, min_words, css_selector, t_opts): u for u in urls}
        for i, fut in enumerate(as_completed(futures), 1):
            doc = fut.result()
            if doc:
                src = doc.metadata.get("source")
                if src and src not in seen:
                    seen.add(src)
                    docs.append(doc)
            if i % 10 == 0 or i == len(urls):
                logger.info("Progress: %d/%d URLs processed", i, len(urls))
    logger.info("Finished: %d documents extracted (from %d URLs)", len(docs), len(urls))
    return docs


# ----------------------- public API -----------------------
class WebsiteLoader(BaseLoader):
    def __init__(
        self,
        urls,
        sitemap_url,
        site_root,
        limit,
        max_workers,
        min_words,
        css_selector: Optional[str] = None,
        trafilatura_options: Optional[Dict[str, Any]] = None,  # NEW: override defaults if needed
    ):
        """
        css_selector: Optional CSS selector to scope extraction to a specific element.
                      Example: "div.cms-block.pos-1.cms-block-text.position-relative"
        trafilatura_options: Optional overrides for extraction (e.g., {"include_tables": True})
        """
        self._urls = urls
        self._sitemap_url = sitemap_url
        self._site_root = site_root
        self._limit = limit
        self._max_workers = max_workers
        self._min_words = min_words
        self._css_selector = css_selector
        self._t_opts = trafilatura_options or None  # keep None to use defaults

    def load(self) -> List[Document]:
        docs: List[Document] = []
        try:
            if self._urls:
                docs.extend(
                    get_by_urls(
                        self._urls,
                        max_workers=self._max_workers,
                        min_words=self._min_words,
                        css_selector=self._css_selector,
                        trafilatura_options=self._t_opts,
                    )
                )
            if self._sitemap_url:
                docs.extend(
                    get_by_sitemap(
                        self._sitemap_url,
                        limit=self._limit,
                        max_workers=self._max_workers,
                        min_words=self._min_words,
                        css_selector=self._css_selector,
                        trafilatura_options=self._t_opts,
                    )
                )
            if self._site_root:
                docs.extend(
                    get_by_site(
                        self._site_root,
                        pick_first=True,
                        limit=self._limit,
                        max_workers=self._max_workers,
                        min_words=self._min_words,
                        css_selector=self._css_selector,
                        trafilatura_options=self._t_opts,
                    )
                )
        except Exception as e:
            logging.error(f"Website loader failed: {e}")
        return docs


def get_by_urls(
    urls: list[str],
    *,
    max_workers: int = 8,
    min_words: int = 40,
    css_selector: Optional[str] = None,
    trafilatura_options: Optional[Dict[str, Any]] = None,
) -> list[Document]:
    """
    Extract main text from specific URLs.
    If css_selector is provided, only text from that element (and descendants) is kept.
    """
    return _extract_many(
        urls,
        max_workers=max_workers,
        min_words=min_words,
        css_selector=css_selector,
        t_opts=trafilatura_options,
    )


def get_by_sitemap(
    sitemap_url: str,
    *,
    limit: Optional[int] = None,
    max_workers: int = 8,
    min_words: int = 40,
    css_selector: Optional[str] = None,
    trafilatura_options: Optional[Dict[str, Any]] = None,
) -> list[Document]:
    """
    Expand a sitemap using Trafilatura's built-in sitemap utilities, then extract.
    If css_selector is provided, only text from that element (and descendants) is kept.
    """
    urls = sitemaps.sitemap_search(sitemap_url) or []
    logger.info("Discovered %d URLs in sitemap: %s", len(urls), sitemap_url)
    if limit is not None:
        urls = urls[:limit]
        logger.info("Limiting to first %d URLs", limit)
    return _extract_many(
        urls,
        max_workers=max_workers,
        min_words=min_words,
        css_selector=css_selector,
        t_opts=trafilatura_options,
    )


def get_by_site(
    site_root: str,
    *,
    pick_first: bool = True,
    limit: Optional[int] = None,
    max_workers: int = 8,
    min_words: int = 40,
    css_selector: Optional[str] = None,
    trafilatura_options: Optional[Dict[str, Any]] = None,
) -> list[Document]:
    """
    Discover sitemaps from robots.txt and ingest.
    If css_selector is provided, only text from that element (and descendants) is kept.
    """
    sitemaps_urls = sitemaps.sitemap_discovery(site_root) or []
    logger.info("Discovered %d sitemap(s) from robots.txt: %s", len(sitemaps_urls), site_root)
    if not sitemaps_urls:
        return []
    if pick_first:
        logger.info("Using first sitemap: %s", sitemaps_urls[0])
        return get_by_sitemap(
            sitemaps_urls[0],
            limit=limit,
            max_workers=max_workers,
            min_words=min_words,
            css_selector=css_selector,
            trafilatura_options=trafilatura_options,
        )
    # merge all
    merged_urls: list[str] = []
    for sm in sitemaps_urls:
        urls = sitemaps.sitemap_search(sm) or []
        merged_urls.extend(urls)
        logger.info("Added %d URLs from sitemap %s", len(urls), sm)
    if limit is not None:
        merged_urls = merged_urls[:limit]
        logger.info("Limiting to first %d URLs total", limit)
    return get_by_urls(
        merged_urls,
        max_workers=max_workers,
        min_words=min_words,
        css_selector=css_selector,
        trafilatura_options=trafilatura_options,
    )
