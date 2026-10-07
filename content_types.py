"""
Folder-based content classification and structured .docx parsers.

Projects can declare `ingestion.content_types` in config.yaml to tag every chunk
with metadata derived from the file's folder (relative to files/), e.g.:

    content_types:
      - path: Guidelines
        content_type: guideline
        is_evidence: true
      - path: Additional Information/Sciarc
        content_type: sciarc_webcast
        is_evidence: false
        parser: sciarc_webcast
      - path: Additional Information/ThinkAldo
        content_type: thinkaldo
        is_evidence: false
        parser: linked_item
        default_link: https://www.thinkaldo.com/

Supported parsers (applied to .docx files only; other files go through Docling):
  - linked_item:    title line(s), a URL line, then description/article text
  - sciarc_webcast: title, speaker, event, "Embed Code" + iframe, "Video Transcript" + text
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

STRUCTURED_PARSERS = {"linked_item", "sciarc_webcast"}

_URL_LINE_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_YEAR_PREFIX_RE = re.compile(r"^((?:19|20)\d{2})[_\-\s]")
_IFRAME_SRC_RE = re.compile(r"""<iframe[^>]*\ssrc=["']([^"']+)["']""", re.IGNORECASE)


@dataclass
class ContentRule:
    path: str
    content_type: str
    is_evidence: bool = True
    parser: Optional[str] = None
    default_link: Optional[str] = None

    @property
    def prefix(self) -> str:
        return self.path.strip().strip("/").replace("\\", "/").lower()


@dataclass
class ParsedItem:
    """Result of a structured parser: per-item metadata plus the text to embed."""
    metadata: Dict[str, Any]
    header: str
    body: str


def parse_content_rules(raw: Any) -> List[ContentRule]:
    rules: List[ContentRule] = []
    for entry in raw or []:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("content_type"):
            logging.warning("Ignoring invalid content_types entry: %r", entry)
            continue
        parser = (entry.get("parser") or "").strip().lower() or None
        if parser and parser not in STRUCTURED_PARSERS:
            logging.warning("Unknown parser %r for content_types path %r; ignoring parser.", parser, entry["path"])
            parser = None
        rules.append(ContentRule(
            path=str(entry["path"]),
            content_type=str(entry["content_type"]),
            is_evidence=bool(entry.get("is_evidence", True)),
            parser=parser,
            default_link=entry.get("default_link") or None,
        ))
    # Longest prefix first so nested folders win over their parents.
    rules.sort(key=lambda r: len(r.prefix), reverse=True)
    return rules


def match_rule(rules: List[ContentRule], rel_key: str) -> Optional[ContentRule]:
    key = (rel_key or "").replace("\\", "/").lower()
    for rule in rules:
        if key == rule.prefix or key.startswith(rule.prefix + "/"):
            return rule
    return None


def uses_structured_parser(rule: Optional[ContentRule], path: Path) -> bool:
    return bool(rule and rule.parser and path.suffix.lower() == ".docx")


def year_from_filename(name: str) -> Optional[int]:
    m = _YEAR_PREFIX_RE.match(name or "")
    return int(m.group(1)) if m else None


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = _YEAR_PREFIX_RE.sub("", stem)
    return stem.replace("_", " ").strip()


def base_metadata(rule: Optional[ContentRule], file_name: str) -> Dict[str, Any]:
    """Metadata applied to every chunk of a file covered by a content rule."""
    if rule is None:
        return {}
    meta: Dict[str, Any] = {
        "content_type": rule.content_type,
        "is_evidence": rule.is_evidence,
        "file_name": file_name,
        "title": title_from_filename(file_name),
        "year": year_from_filename(file_name),
    }
    if rule.default_link:
        meta["link"] = rule.default_link
    return meta


# --- .docx parsing ----------------------------------------------------------

def _docx_blocks(path: Path) -> List[str]:
    """Paragraph and table texts in document order (tables as ' | '-joined rows)."""
    import docx  # python-docx (installed with docling)
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    blocks: List[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = []
                for cell in row.cells:
                    t = cell.text.strip()
                    if t and (not cells or cells[-1] != t):  # merged cells repeat text
                        cells.append(t)
                if cells:
                    blocks.append(" | ".join(cells))
    return blocks


def _docx_hyperlinks(path: Path) -> List[str]:
    import docx

    document = docx.Document(str(path))
    return [r.target_ref for r in document.part.rels.values() if "hyperlink" in r.reltype]


def parse_linked_item(path: Path, rule: ContentRule) -> ParsedItem:
    blocks = _docx_blocks(path)
    url_idx = next((i for i, b in enumerate(blocks) if _URL_LINE_RE.match(b)), None)

    if url_idx is not None:
        link = blocks[url_idx].strip()
        header_lines = blocks[:url_idx]
        body_lines = blocks[url_idx + 1:]
    else:
        links = _docx_hyperlinks(path)
        link = links[0] if links else rule.default_link
        header_lines = blocks[:1]
        body_lines = blocks[1:]

    meta = base_metadata(rule, path.name)
    if header_lines:
        meta["title"] = header_lines[-1].rstrip(":").strip()
        if len(header_lines) > 1:
            meta["series"] = " – ".join(h.rstrip(":").strip() for h in header_lines[:-1])
    if link:
        meta["link"] = link

    header = meta["title"] if not meta.get("series") else f"{meta['series']} – {meta['title']}"
    return ParsedItem(metadata=meta, header=header, body="\n\n".join(body_lines))


def parse_sciarc_webcast(path: Path, rule: ContentRule) -> ParsedItem:
    blocks = _docx_blocks(path)
    lower = [b.lower() for b in blocks]
    embed_idx = next((i for i, b in enumerate(lower) if b == "embed code"), None)
    transcript_idx = next((i for i, b in enumerate(lower) if b in ("video transcript", "transcript")), None)

    if embed_idx is None or transcript_idx is None or transcript_idx < embed_idx:
        logging.warning(
            "Webcast file %s does not follow the 'Embed Code' / 'Video Transcript' layout; "
            "parsing it as a linked item instead.", path.name,
        )
        return parse_linked_item(path, rule)

    header_lines = blocks[:embed_idx]
    embed_html = "\n".join(blocks[embed_idx + 1:transcript_idx]).strip()
    transcript = "\n\n".join(blocks[transcript_idx + 1:])

    meta = base_metadata(rule, path.name)
    if len(header_lines) >= 1:
        meta["title"] = header_lines[0]
    if len(header_lines) >= 2:
        meta["speaker"] = header_lines[1]
    if len(header_lines) >= 3:
        meta["event"] = " – ".join(header_lines[2:])

    meta["embed_html"] = embed_html
    m = _IFRAME_SRC_RE.search(embed_html)
    if m:
        embed_url = html.unescape(m.group(1))
        meta["embed_url"] = embed_url
        meta["link"] = embed_url
        video_id = (parse_qs(urlparse(embed_url).query).get("photo_id") or [None])[0]
        if video_id:
            meta["video_id"] = video_id
    else:
        logging.warning("No iframe src found in embed code of %s.", path.name)

    header_parts = [meta["title"]] + [meta[k] for k in ("speaker", "event") if meta.get(k)]
    return ParsedItem(metadata=meta, header="\n".join(header_parts), body=transcript)


def parse_structured_file(path: Path, rule: ContentRule) -> ParsedItem:
    if rule.parser == "sciarc_webcast":
        return parse_sciarc_webcast(path, rule)
    return parse_linked_item(path, rule)
