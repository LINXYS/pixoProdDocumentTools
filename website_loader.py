from __future__ import annotations
import datetime as dt
from typing import Iterable, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

import trafilatura
import trafilatura.sitemaps as sitemaps
from langchain_core.document_loaders import BaseLoader

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


def _extract_one(url: str, min_words: int = 40) -> Optional[Document]:
    """Fetch + extract with Trafilatura for a single URL."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("Skipping (no content fetched): %s", url)
            return None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            url=url,
        )
        if not text or len(text.split()) < min_words:
            logger.debug("Too short/empty: %s", url)
            return None
        meta = trafilatura.extract_metadata(downloaded, default_url=url)
        logger.info("✓ Extracted %d words from %s", len(text.split()), url)
        return _to_doc(url, text, meta)
    except Exception as e:
        logger.error("Error extracting %s: %s", url, e)
        return None


def _extract_many(urls: Iterable[str], max_workers: int, min_words: int) -> List[Document]:
    """Concurrent extraction, deduping by URL."""
    docs: List[Document] = []
    seen = set()
    urls = list(urls)
    logger.info("Starting extraction for %d URLs", len(urls))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_extract_one, u, min_words): u for u in urls}
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
    def __init__(self, urls, sitemap_url, site_root, limit, max_workers, min_words):
        self._urls = urls
        self._sitemap_url = sitemap_url
        self._site_root = site_root
        self._limit = limit
        self._max_workers = max_workers
        self._min_words = min_words

    def load(self) -> List[Document]:
        docs: List[Document] = []
        try:
            if self._urls:
                docs.extend(get_by_urls(self._urls, max_workers=self._max_workers, min_words=self._min_words))
            if self._sitemap_url:
                docs.extend(
                    get_by_sitemap(
                        self._sitemap_url,
                        limit=self._limit,
                        max_workers=self._max_workers,
                        min_words=self._min_words,
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
) -> list[Document]:
    """
    Extract main text from specific URLs using Trafilatura.
    Returns a list[Document] with metadata.source=url and metadata.ingested_at=today.
    """
    return _extract_many(urls, max_workers=max_workers, min_words=min_words)


def get_by_sitemap(
    sitemap_url: str,
    *,
    limit: Optional[int] = None,
    max_workers: int = 8,
    min_words: int = 40,
) -> list[Document]:
    """
    Expand a sitemap using Trafilatura's built-in sitemap utilities, then extract with Trafilatura.
    - sitemap_url: e.g., "https://example.com/sitemap.xml"
    - limit: optionally cap number of page URLs to ingest.
    """
    urls = sitemaps.sitemap_search(sitemap_url) or []
    logger.info("Discovered %d URLs in sitemap: %s", len(urls), sitemap_url)
    if limit is not None:
        urls = urls[:limit]
        logger.info("Limiting to first %d URLs", limit)
    return _extract_many(urls, max_workers=max_workers, min_words=min_words)


def get_by_site(
    site_root: str,
    *,
    pick_first: bool = True,
    limit: Optional[int] = None,
    max_workers: int = 8,
    min_words: int = 40,
) -> list[Document]:
    """
    Discover sitemaps from robots.txt and ingest.
    - site_root: e.g., "https://example.com/"
    - pick_first: if multiple sitemaps advertised, use the first; otherwise merge all.
    """
    sitemaps_urls = sitemaps.sitemap_discovery(site_root) or []
    logger.info("Discovered %d sitemap(s) from robots.txt: %s", len(sitemaps_urls), site_root)
    if not sitemaps_urls:
        return []
    if pick_first:
        logger.info("Using first sitemap: %s", sitemaps_urls[0])
        return get_by_sitemap(sitemaps_urls[0], limit=limit, max_workers=max_workers, min_words=min_words)
    # merge all
    merged_urls: list[str] = []
    for sm in sitemaps_urls:
        urls = sitemaps.sitemap_search(sm) or []
        merged_urls.extend(urls)
        logger.info("Added %d URLs from sitemap %s", len(urls), sm)
    if limit is not None:
        merged_urls = merged_urls[:limit]
        logger.info("Limiting to first %d URLs total", limit)
    return get_by_urls(merged_urls, max_workers=max_workers, min_words=min_words)
