import json
import logging
from pathlib import Path
from typing import Dict, Any


class IngestionState:
    """
    Tracks per-project ingestion progress so runs can be resumed.

    Stored as JSON, e.g.:
    {
      "pixodoc_files": { "subdir/doc1.pixodoc": true, ... },
      "doc_files":     { "subdir/file1.pdf":   true, ... },
      "urls":          { "https://example/":   true, ... },
      "file_fingerprints": { "subdir/file1.pdf": "size=123;modify=20260310091500", ... },
      "remote_sources": { "subdir/file1.pdf": "/remote/path/file1.pdf", ... }
    }
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Dict[str, Any]] = {
            "pixodoc_files": {},
            "doc_files": {},
            "urls": {},
            "file_fingerprints": {},
            "remote_sources": {},
        }
        self._load()

    # --- internal helpers -------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            for key in ("pixodoc_files", "doc_files", "urls", "file_fingerprints", "remote_sources"):
                section = raw.get(key) or {}
                if isinstance(section, dict):
                    if key in ("file_fingerprints", "remote_sources"):
                        self.data[key] = {str(k): str(v) for k, v in section.items()}
                    else:
                        self.data[key] = {str(k): bool(v) for k, v in section.items()}
            logging.info("Loaded ingestion state from %s", self.path)
        except Exception as e:
            logging.warning("Failed to load ingestion state from %s: %s. Starting fresh.", self.path, e)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error("Failed to write ingestion state to %s: %s", self.path, e)

    # --- pixodoc files ----------------------------------------------------

    def is_pixodoc_done(self, rel_path: str) -> bool:
        return rel_path in self.data["pixodoc_files"]

    def mark_pixodoc_done(self, rel_path: str) -> None:
        self.data["pixodoc_files"][rel_path] = True
        self._save()

    def clear_pixodoc_done(self, rel_path: str) -> None:
        if rel_path in self.data["pixodoc_files"]:
            del self.data["pixodoc_files"][rel_path]
            self._save()

    # --- doc files (Docling) ----------------------------------------------

    def is_doc_file_done(self, rel_path: str) -> bool:
        return rel_path in self.data["doc_files"]

    def mark_doc_file_done(self, rel_path: str) -> None:
        self.data["doc_files"][rel_path] = True
        self._save()

    def clear_doc_file_done(self, rel_path: str) -> None:
        if rel_path in self.data["doc_files"]:
            del self.data["doc_files"][rel_path]
            self._save()

    # --- URLs (website loader) --------------------------------------------

    def is_url_done(self, url: str) -> bool:
        return url in self.data["urls"]

    def mark_url_done(self, url: str) -> None:
        self.data["urls"][url] = True
        self._save()

    # --- file fingerprints -------------------------------------------------

    def get_file_fingerprint(self, rel_path: str) -> str | None:
        return self.data["file_fingerprints"].get(rel_path)

    def set_file_fingerprint(self, rel_path: str, fingerprint: str) -> None:
        self.data["file_fingerprints"][rel_path] = fingerprint
        self._save()

    # --- local->remote source mapping -------------------------------------

    def get_remote_source(self, rel_path: str) -> str | None:
        return self.data["remote_sources"].get(rel_path)

    def set_remote_source(self, rel_path: str, remote_path: str) -> None:
        current = self.data["remote_sources"].get(rel_path)
        if current == remote_path:
            return
        self.data["remote_sources"][rel_path] = remote_path
        self._save()
