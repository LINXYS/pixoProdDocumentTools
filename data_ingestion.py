import logging
import sys, asyncio

import website_loader

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import json
from os.path import join
from pathlib import Path
from typing import List, Optional
from collections import defaultdict, deque

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_docling import DoclingLoader
from langchain_docling.loader import ExportType
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tqdm import tqdm

from cfg import IngestionConfig
from langchain_indexer import LangChainIndexer
import glob
import os
import shutil
import warnings
from ingestion_state import IngestionState

DOCLING_SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".md",
    ".adoc",
    ".asciidoc",
    ".txt",
}

# Local imports to avoid hard dependency if not used elsewhere
try:
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
    import tiktoken

    openai_tokenizer_ok = True
except Exception:
    openai_tokenizer_ok = False

huggingface_tokenizer_ok = False
if not openai_tokenizer_ok:
    try:
        from transformers import AutoTokenizer
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        huggingface_tokenizer_ok = True
    except Exception:
        huggingface_tokenizer_ok = False


class DataIngestionApp:
    """
    Ties together document loaders, optional chunking, and indexing via LangChain.
    """

    def __init__(
        self,
        config: IngestionConfig,
        chunking_enabled: bool = True,
        project_dir: Optional[Path] = None,
        ingestion_state: Optional[IngestionState] = None,
    ):
        self.config = config
        self.indexer = LangChainIndexer(config)
        self.loaders: List[BaseLoader] = []
        self.disable_bar = True
        self.project_dir = Path(project_dir or Path.cwd())
        state_path = self.project_dir / "ingestion_state.json"
        self.state = ingestion_state or IngestionState(state_path)
        logging.info(
            f"DataIngestionApp initialized with docling_batch_size="
            f"{getattr(self.config, 'docling_batch_size', None)!r}"
        )
        if not chunking_enabled:
            self.config.chunk_size = 10000000000000

    def register_loader(self, loader: BaseLoader):
        """
        Register a new document loader.
        """
        self.loaders.append(loader)
        logging.info(f"Registered loader: {loader.__class__.__name__}")

    def get_token_splitter(self) -> RecursiveCharacterTextSplitter:
        """
        Token-aware splitter (tiktoken). Falls back to char-based if tiktoken
        isn't available. Chunk size & overlap are interpreted as TOKENS when
        token-aware, or as characters on fallback.
        """
        try:
            return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                # Prefer Markdown-friendly boundaries before falling back
                separators=["\n## ", "\n### ", "\n\n", "\n", " "],
            )
        except Exception:
            return RecursiveCharacterTextSplitter(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                separators=["\n## ", "\n### ", "\n\n", "\n", " "],
            )

    # --- NEW: hybrid Markdown→token splitter ---
    def ingest_data(self, cleanup_mode: str = "incremental"):
        """
        Loads documents from all registered loaders, applies chunking if enabled,
        and indexes them using LangChain's indexing API.

        Policy:
          - For DoclingLoader: rely on Docling's own splitter (HybridChunker) by
            configuring the loader with ExportType.DOC_CHUNKS (see get_docling_loader).
            Do NOT re-split here.
          - For all other loaders: use the normal token-aware splitter.
          - For .pixodoc files: use the normal token-aware splitter.
        """

        # ---------- .pixodoc files first ----------
        files_dir = Path.cwd() / "files"
        pixodoc_files = glob.glob(join(files_dir, "**/*.pixodoc"), recursive=True)

        if self.disable_bar:
            print(f"Processing {len(pixodoc_files)} .pixodoc files...")

        for file in tqdm(
                pixodoc_files,
                desc="Processing .pixodoc files",
                unit="file",
                disable=self.disable_bar,
                file=sys.stdout,
        ):
            try:
                rel_path = ""
                try:
                    rel_path = str(Path(file).relative_to(files_dir))
                except Exception:
                    rel_path = str(Path(file).name)

                if self.state.is_pixodoc_done(rel_path):
                    logging.info(f"Skipping .pixodoc (already processed): {rel_path}")
                    continue

                with open(file, "r", encoding="utf-8") as f:
                    doc_data = json.load(f)
            except UnicodeDecodeError:
                logging.warning(
                    f"Failed to decode file {file} using UTF-8. Trying with 'latin-1' encoding."
                )
                try:
                    with open(file, "r", encoding="latin-1") as f:
                        doc_data = json.load(f)
                except json.JSONDecodeError:
                    logging.error(f"Failed to parse JSON in file {file}")
                    continue
                except Exception as e:
                    logging.error(f"Failed to process file {file}: {str(e)}")
                    continue
            except json.JSONDecodeError:
                logging.error(f"Failed to parse JSON in file {file}")
                continue
            except Exception as e:
                logging.error(f"Failed to process file {file}: {str(e)}")
                continue

            doc = Document(
                page_content=doc_data.get("page_content", ""),
                metadata=doc_data.get("metadata", {}) or {},
            )

            # Per-file chunk + index immediately
            docs_to_index: List[Document]
            if self.config.use_chunking:
                splitter = self.get_token_splitter()
                parts = splitter.split_text(doc.page_content or "")
                docs_to_index = [Document(page_content=p, metadata=doc.metadata) for p in parts]
                logging.info(
                    f"Split .pixodoc {os.path.basename(file)} into {len(parts)} chunk(s) "
                    f"(target={self.config.chunk_size}, overlap={self.config.chunk_overlap})."
                )
            else:
                docs_to_index = [doc]

            if docs_to_index:
                try:
                    self.indexer.index_documents(docs_to_index, cleanup_mode="incremental")
                    self.state.mark_pixodoc_done(rel_path)
                    logging.info(f"Indexed .pixodoc file and marked done: {rel_path}")
                except Exception as e:
                    logging.error(f"Indexing failed for .pixodoc {rel_path}: {e}")

        logging.info(f"Loaded {len(pixodoc_files)} .pixodoc files.")
        print()
        logging.info("Starting ingestion... Processing Docling file batches.")

        # Canonical root for all doc files (used for state keys)
        files_dir = Path.cwd() / "files"

        # ---------- Process Docling file batches until all files are done ----------
        batch_round = 0
        while True:
            docling_loaders = self.get_docling_loaders(directory_path=str(files_dir))
            if not docling_loaders:
                logging.info("Docling: no more file batches to process.")
                break

            batch_round += 1
            round_indexed_sources = 0
            logging.info(
                f"Docling: starting round {batch_round} with "
                f"{len(docling_loaders)} loader batch(es)."
            )

            for i, loader in enumerate(docling_loaders, start=1):
                try:
                    # Expect loader to be configured with ExportType.DOC_CHUNKS and a Docling chunker.
                    batch_files = self._get_loader_batch_files(loader)
                    num_files = None
                    if batch_files:
                        num_files = len(batch_files)

                    logging.info(
                        f"Docling batch {i}/{len(docling_loaders)} in round {batch_round} "
                        f"starting: {num_files if num_files is not None else 'unknown'} file(s) "
                        f"(configured batch_size={getattr(self.config, 'docling_batch_size', None)!r})."
                    )

                    docs = loader.load()
                    logging.info(
                        f"Docling batch {i}/{len(docling_loaders)} in round {batch_round} "
                        f"finished load() with {len(docs) if docs else 0} document(s)."
                    )
                    if not docs:
                        logging.info(f"{loader.__class__.__name__} returned no documents.")
                        continue

                    # Group Docling docs by original source path and index per source
                    grouped = defaultdict(list)
                    unknown_idx = 1
                    for d in docs:
                        src_path = self._extract_source_path(d.metadata or {})
                        if src_path is None:
                            key = f"__unknown__/doc_{unknown_idx:05d}"
                            unknown_idx += 1
                        else:
                            key = str(src_path)
                        grouped[key].append(d)

                    for key, parts in grouped.items():
                        # Determine relative tracking key (must match get_docling_loaders)
                        if key.startswith("__unknown__/"):
                            rel_key = key
                        else:
                            src_path = Path(key)
                            rel_key = self._normalize_doc_rel_path(src_path, files_dir)

                        if self.state.is_doc_file_done(rel_key):
                            logging.info(f"Skipping Docling source (already processed): {rel_key}")
                            continue

                        try:
                            self._apply_remote_source_metadata(parts, rel_key)
                            self.indexer.index_documents(parts, cleanup_mode="incremental")
                            self.state.mark_doc_file_done(rel_key)
                            round_indexed_sources += 1
                            logging.info(
                                f"Indexed Docling source {rel_key} with {len(parts)} chunk(s) "
                                f"and marked done."
                            )
                        except Exception as e:
                            logging.error(f"Indexing failed for Docling source {rel_key}: {e}")
                except Exception as e:
                    if batch_files:
                        logging.error(
                            f"Docling loader batch {i} in round {batch_round} failed for file(s): "
                            f"{', '.join(batch_files)}. Error: {e}"
                        )
                    else:
                        logging.error(f"Docling loader batch {i} in round {batch_round} failed: {e}")

            if round_indexed_sources == 0 and docling_loaders:
                logging.error(
                    "Docling made no indexing progress in round %s. "
                    "Stopping Docling rounds to avoid infinite retries. "
                    "Inspect preceding batch errors for failing file paths.",
                    batch_round,
                )
                break

        logging.info("Docling file ingestion complete. Proceeding with other loaders (if any).")

        # ---------- Process non-Docling registered loaders (e.g., website) ----------
        logging.info("Starting ingestion... Processing non-Docling loaders.")
        for loader in tqdm(
                self.loaders,
                desc="Processing loaders",
                unit="loader",
                disable=self.disable_bar,
                file=sys.stdout,
        ):
            try:
                if getattr(loader, "__class__", None).__name__ == "WebsiteLoader":
                    # Website loader: index per URL and track progress
                    raw_docs = loader.load()
                    if not raw_docs:
                        logging.info(f"{loader.__class__.__name__} returned no documents.")
                        continue

                    # Group by URL from metadata
                    url_groups = defaultdict(list)
                    for d in raw_docs:
                        meta = d.metadata or {}
                        url = meta.get("source") or meta.get("url") or "__unknown__"
                        url_groups[str(url)].append(d)

                    splitter = self.get_token_splitter() if self.config.use_chunking else None
                    max_retries = max(0, int(getattr(self.config, "website_index_max_retries", 3) or 0))
                    retry_counts: dict[str, int] = defaultdict(int)
                    pending_urls = deque(url_groups.keys())

                    while pending_urls:
                        url = pending_urls.popleft()
                        docs_for_url = url_groups[url]
                        if url != "__unknown__" and self.state.is_url_done(url):
                            logging.info(f"Skipping URL (already processed): {url}")
                            continue

                        docs_to_index: List[Document]
                        if splitter:
                            docs_to_index = []
                            for d in docs_for_url:
                                parts = splitter.split_text(d.page_content or "")
                                docs_to_index.extend(
                                    [Document(page_content=p, metadata=d.metadata) for p in parts]
                                )
                            logging.info(
                                f"{loader.__class__.__name__}: URL {url} split into "
                                f"{len(docs_to_index)} chunk(s) (target={self.config.chunk_size}, "
                                f"overlap={self.config.chunk_overlap})."
                            )
                        else:
                            docs_to_index = docs_for_url

                        if not docs_to_index:
                            continue

                        try:
                            self.indexer.index_documents(docs_to_index, cleanup_mode="incremental")
                            if url != "__unknown__":
                                self.state.mark_url_done(url)
                            logging.info(
                                f"Indexed website URL {url} with {len(docs_to_index)} document(s)/chunk(s)."
                            )
                        except Exception as e:
                            retry_counts[url] += 1
                            attempt = retry_counts[url]
                            if attempt <= max_retries:
                                logging.error(
                                    f"Indexing failed for website URL {url} (attempt {attempt}/"
                                    f"{max_retries + 1}): {e}. Re-queueing for retry."
                                )
                                pending_urls.append(url)
                            else:
                                logging.error(
                                    f"Indexing failed for website URL {url} after "
                                    f"{max_retries + 1} attempts: {e}"
                                )

                else:
                    # Other loaders: process in one shot (no resume tracking)
                    raw_docs = loader.load()
                    if not raw_docs:
                        logging.info(f"{loader.__class__.__name__} returned no documents.")
                        continue

                    if self.config.use_chunking:
                        splitter = self.get_token_splitter()
                        chunked_docs: List[Document] = []
                        for d in raw_docs:
                            parts = splitter.split_text(d.page_content or "")
                            chunked_docs.extend(
                                [Document(page_content=p, metadata=d.metadata) for p in parts]
                            )
                        logging.info(
                            f"{loader.__class__.__name__}: split {len(raw_docs)} doc(s) "
                            f"into {len(chunked_docs)} chunk(s) (target={self.config.chunk_size}, "
                            f"overlap={self.config.chunk_overlap})."
                        )
                        docs_to_index = chunked_docs
                    else:
                        docs_to_index = raw_docs
                        logging.info(
                            f"{loader.__class__.__name__}: added {len(raw_docs)} document(s) (no chunking)."
                        )

                    if docs_to_index:
                        self.indexer.index_documents(docs_to_index, cleanup_mode="incremental")
            except Exception as e:
                logging.error(f"Loader {loader.__class__.__name__} failed: {e}")

        # Indexing is now done incrementally per file/URL.
        print()
        logging.info("Ingestion complete.")

    def _build_docling_converter(self):
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        requested = (os.getenv("PIXO_ACCELERATOR") or "cpu").strip().lower()
        if requested == "gpu":
            requested = "cuda"
        if requested not in {"cpu", "auto", "cuda", "mps"}:
            logging.warning("Unknown PIXO_ACCELERATOR=%r; using CPU.", requested)
            requested = "cpu"

        device = AcceleratorDevice.CPU
        torch_info = {}
        cuda_available = False
        mps_available = False
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
            mps_available = bool(
                getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
            )
            torch_info = {
                "torch_version": getattr(torch, "__version__", "?"),
                "torch_cuda": getattr(torch.version, "cuda", None),
                "cuda_available": cuda_available,
                "cuda_count": torch.cuda.device_count() if cuda_available else 0,
                "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
                "mps_available": mps_available,
                "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
            }

        except Exception as e:
            logging.warning(f"PyTorch check failed; using CPU. Error: {e}")

        if requested == "cpu":
            device = AcceleratorDevice.CPU
            logging.info("Docling accelerator forced to CPU by PIXO_ACCELERATOR=cpu.")
        elif requested == "cuda":
            if cuda_available:
                try:
                    torch.cuda.set_device(0)
                except Exception as e:
                    logging.warning(f"Failed to set CUDA device 0 explicitly: {e}")
                device = AcceleratorDevice.CUDA
            else:
                device = AcceleratorDevice.CPU
                logging.warning("PIXO_ACCELERATOR=cuda requested, but CUDA is not available. Falling back to CPU.")
        elif requested == "mps":
            if mps_available:
                device = AcceleratorDevice.MPS
            else:
                device = AcceleratorDevice.CPU
                logging.warning("PIXO_ACCELERATOR=mps requested, but MPS is not available. Falling back to CPU.")
        else:
            if cuda_available:
                try:
                    torch.cuda.set_device(0)
                except Exception as e:
                    logging.warning(f"Failed to set CUDA device 0 explicitly: {e}")
                device = AcceleratorDevice.CUDA
            elif mps_available:
                device = AcceleratorDevice.MPS
            else:
                device = AcceleratorDevice.CPU
                logging.warning(
                    "Docling is using CPU because no supported accelerator is visible. "
                    "For Docker GPU mode, start with docker-compose.gpu.yml and a CUDA-enabled torch build."
                )

        logging.info(f"Docling/torch info: {torch_info}")
        logging.info(f"Docling accelerator set to: {getattr(device, 'name', device)}")

        accel = AcceleratorOptions(num_threads=min(os.cpu_count() or 1, 8), device=device)
        pdf_opts = PdfPipelineOptions()
        pdf_opts.accelerator_options = accel

        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
        )

    def get_docling_loaders(self, directory_path: str) -> list[DoclingLoader]:
        """
        Return one or more Docling-based loaders over files in `directory_path`.
        Files are optionally batched according to config.docling_batch_size.

        Progress tracking:
          - We skip files already marked as done in IngestionState.doc_files
            (relative to the `files/` directory).
          - Each loader processes a batch of files; ingestion still indexes
            and marks completion per original source file.
        """

        # Gather files
        files = glob.glob(join(directory_path, "**/*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]

        # Remove .pixodoc files (handled elsewhere)
        files = [f for f in files if os.path.splitext(f)[1].lower() != ".pixodoc"]

        supported_files = []
        skipped_unsupported = []
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in DOCLING_SUPPORTED_EXTENSIONS:
                supported_files.append(f)
            else:
                skipped_unsupported.append(f)
        files = supported_files
        if skipped_unsupported:
            preview = ", ".join(Path(p).name for p in skipped_unsupported[:10])
            logging.warning(
                "Docling: skipping %s unsupported file(s) by extension. "
                "Supported extensions: %s. Examples: %s",
                len(skipped_unsupported),
                ", ".join(sorted(DOCLING_SUPPORTED_EXTENSIONS)),
                preview,
            )

        files_root = Path(directory_path)

        # Skip files that are already fully processed according to ingestion_state
        remaining_files = []
        for f in files:
            p = Path(f)
            rel = self._normalize_doc_rel_path(p, files_root)
            if self.state.is_doc_file_done(rel):
                logging.info(f"Docling: skipping already-processed file: {rel}")
                continue
            remaining_files.append(f)

        files = remaining_files

        if not files:
            logging.info("Docling: no new files to process (all already ingested).")
            return []

        logging.info(f"Docling: raw configured batch size={getattr(self.config, 'docling_batch_size', None)!r}")

        # Build Docling chunker with a tokenizer
        chunker = None
        if openai_tokenizer_ok:
            try:
                model_name = getattr(self.config, "tokenizer_model", None) or \
                             getattr(self.config, "embedding_model", None) or \
                             "text-embedding-3-small"
                try:
                    enc = tiktoken.encoding_for_model(model_name)
                except Exception:
                    # Sensible default for most modern OpenAI models
                    enc = tiktoken.get_encoding("cl100k_base")
                oa_tok = OpenAITokenizer(tokenizer=enc, max_tokens=self.config.chunk_size)
                from docling.chunking import HybridChunker as _HC  # same import, just to be safe in nested scopes
                chunker = _HC(tokenizer=oa_tok, merge_peers=True)
            except Exception:
                chunker = None

        if chunker is None and huggingface_tokenizer_ok:
            try:
                hf_model = getattr(self.config, "hf_tokenizer_model", None) or \
                           getattr(self.config, "embedding_model", None) or \
                           "sentence-transformers/all-MiniLM-L6-v2"
                hf_auto = AutoTokenizer.from_pretrained(hf_model)
                hf_tok = HuggingFaceTokenizer(tokenizer=hf_auto, max_tokens=self.config.chunk_size)
                chunker = HybridChunker(tokenizer=hf_tok, merge_peers=True)
            except Exception:
                chunker = None

        converter = self._build_docling_converter()

        # If no tokenizer backend is available, fall back to MARKDOWN export
        if chunker is None:
            warnings.warn(
                "Could not initialize a Docling tokenizer for HybridChunker. "
                "Falling back to MARKDOWN export; normal splitter will be used later."
            )

        # Determine batch size from config; None or <=0 => single batch
        batch_size = getattr(self.config, "docling_batch_size", None)
        try:
            batch_size = int(batch_size) if batch_size is not None else None
        except Exception:
            batch_size = None

        if not batch_size or batch_size <= 0:
            # For remote-sync workflows, default to per-file batches so embeddings
            # are indexed incrementally instead of only after a huge load() call.
            if getattr(self.config, "remote_source", None):
                batch_size = 1
                logging.info(
                    "Docling: docling_batch_size is unset; defaulting to 1 for remote source ingestion "
                    "to index embeddings incrementally."
                )
            else:
                batch_size = len(files) if files else 0
                if batch_size > 1:
                    logging.warning(
                        "Docling: docling_batch_size is unset; processing %s files in one batch. "
                        "Indexing starts only after conversion of that full batch. "
                        "Set ingestion.docling_batch_size (e.g. 1-5) for earlier DB writes.",
                        batch_size,
                    )

        loaders: list[DoclingLoader] = []
        if not files:
            return loaders

        total_files = len(files)
        logging.info(
            f"Docling: preparing loaders for {total_files} file(s) "
            f"with batch_size={batch_size}."
        )

        for batch_idx in range(0, total_files, batch_size):
            batch_files = files[batch_idx: batch_idx + batch_size]
            if not batch_files:
                continue

            batch_number = len(loaders) + 1
            logging.info(
                f"Docling: creating batch {batch_number} with "
                f"{len(batch_files)} file(s) "
                f"({batch_idx + 1}-{batch_idx + len(batch_files)} of {total_files})."
            )

            if chunker is None:
                # MARKDOWN export; downstream splitter will handle chunking
                loader = DoclingLoader(
                    file_path=batch_files,
                    export_type=ExportType.MARKDOWN,
                    converter=converter,
                )
            else:
                # Preferred path: Docling performs chunking and returns pre-chunked Documents
                loader = DoclingLoader(
                    file_path=batch_files,
                    export_type=ExportType.DOC_CHUNKS,
                    chunker=chunker,
                    converter=converter,
                )

            loaders.append(loader)

        logging.info(f"Docling: created {len(loaders)} loader batch(es).")
        return loaders

    # Backwards-compat wrapper (kept in case of external callers)
    def get_docling_loader(self, directory_path: str) -> Optional[DoclingLoader]:
        loaders = self.get_docling_loaders(directory_path)
        return loaders[0] if loaders else None

    def _normalize_doc_rel_path(self, src_path: Path, files_root: Path) -> str:
        """
        Compute a canonical, case-insensitive relative key for a document file.
        This MUST be used consistently everywhere we talk to IngestionState
        for doc_files so that:
          - get_docling_loaders() skipping logic
          - ingest_data() mark_doc_file_done()
        agree on the exact same key for the same physical file.
        """
        try:
            # Use resolved paths to avoid minor differences like "files/./sub/../sub/file.pdf"
            files_root_resolved = files_root.resolve()
            src_resolved = src_path.resolve()
            rel = src_resolved.relative_to(files_root_resolved)
        except Exception:
            # If the file is not under files_root (or resolution fails), fall back
            # to the filename only. This is not perfect but is at least consistent
            # across both caller sites.
            rel = src_path.name

        # Normalize to POSIX separators + lowercase for cross-platform stability
        return rel.as_posix().lower()

    def _extract_source_path(self, metadata: dict) -> Optional[Path]:
        """
        Try to recover the original file path from common metadata keys.
        Returns a Path or None if unavailable.
        """
        for k in ("source", "file_path", "path", "document_path", "filename", "file", "input_path", "document_source"):
            v = metadata.get(k)
            if isinstance(v, str) and v.strip():
                try:
                    return Path(v)
                except Exception:
                    continue
        return None

    def _get_loader_batch_files(self, loader: DoclingLoader) -> List[str]:
        """
        Best-effort extraction of current batch file paths from a Docling loader.
        """
        candidates = []
        for attr in ("file_path", "file_paths", "_file_path", "_file_paths"):
            try:
                value = getattr(loader, attr, None)
            except Exception:
                value = None
            if value is None:
                continue
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, (list, tuple, set)):
                candidates.extend(str(v) for v in value if v)

        seen = set()
        ordered = []
        for c in candidates:
            s = str(c).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            ordered.append(s)
        return ordered

    def _apply_remote_source_metadata(self, docs: List[Document], rel_key: str) -> None:
        """
        Ensure Docling chunks use the remote source path (if available) instead of
        a local filesystem path so source tracking/record-manager IDs are stable.
        """
        if rel_key.startswith("__unknown__/"):
            return

        try:
            remote_source = self.state.get_remote_source(rel_key)
        except Exception as e:
            logging.exception(
                "Failed to read remote source mapping for rel_key=%s. "
                "Keeping original source metadata. Error: %s",
                rel_key,
                e,
            )
            return

        if not remote_source:
            if getattr(self.config, "remote_source", None):
                logging.warning(
                    "Remote source configured but no mapping found for rel_key=%s. "
                    "Keeping original local source metadata.",
                    rel_key,
                )
            return

        updated = 0
        for i, d in enumerate(docs, start=1):
            try:
                if not isinstance(d.metadata, dict):
                    d.metadata = {}
                d.metadata["source"] = remote_source
                updated += 1
            except Exception as e:
                logging.exception(
                    "Failed applying remote source metadata for rel_key=%s on chunk=%s/%s. "
                    "Remote source=%s. Error: %s",
                    rel_key,
                    i,
                    len(docs),
                    remote_source,
                    e,
                )

        if updated != len(docs):
            logging.warning(
                "Applied remote source metadata partially for rel_key=%s: "
                "updated=%s, total=%s.",
                rel_key,
                updated,
                len(docs),
            )

    def _persist_docling_outputs(
            self,
            docs: List[Document],
            files_root: Path,
            processed_root: Optional[Path] = None,
    ) -> None:
        """
        Save Docling-processed docs to 'processed_files/' while preserving the
        directory structure relative to 'files/'. Chunks from the same source
        are concatenated into a single .md file.
        """
        processed_root = processed_root or (Path.cwd() / "processed_files")
        processed_root.mkdir(parents=True, exist_ok=True)

        # Group chunks by their original source file (best-effort)
        grouped = defaultdict(list)
        unknown_idx = 1
        for d in docs:
            src = self._extract_source_path(d.metadata or {})
            if src is None:
                key = f"__unknown__/doc_{unknown_idx:05d}"
                unknown_idx += 1
            else:
                key = str(src)
            grouped[key].append(d)

        for key, parts in grouped.items():
            # Determine relative output path
            if key.startswith("__unknown__/"):
                rel = Path("_unknown") / (key.split("/", 1)[1] + ".md")
            else:
                src_path = Path(key)
                try:
                    rel = src_path.relative_to(files_root)
                except Exception:
                    # If the source isn't under files_root, put it under 'misc'
                    rel = Path("misc") / src_path.name
                rel = rel.with_suffix(".md")

            out_path = processed_root / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Concatenate chunk texts with a simple separator
            content = "\n\n---\n\n".join((p.page_content or "") for p in parts)
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(content if content.endswith("\n") else content + "\n")
                logging.info(f"Wrote processed file: {out_path}")
            except Exception as e:
                logging.error(f"Failed to write {out_path}: {e}")

    # --- Website ingestion registration from config ---
    def get_website_loader(self) -> Optional[BaseLoader]:
        """
        Read website ingestion settings from the loaded config and return a loader that
        yields the resulting documents when loaded.

        The config.ingestion section may include:
          - urls: list[str]           # explicit page URLs
          - sitemap_url: str          # a sitemap XML URL
          - site_root: str            # a site root (robots.txt discovery)
          - limit: int (optional)
          - max_workers: int (optional, default 8)
          - min_words: int (optional, default 40)
          - css_selector: str (optional)
        """
        urls: List[str] = getattr(self.config, "urls", []) or []
        sitemap_url: Optional[str] = getattr(self.config, "sitemap_url", None)
        site_root: Optional[str] = getattr(self.config, "site_root", None)

        # Optional website loader tuning parameters
        limit: Optional[int] = getattr(self.config, "limit", None)
        max_workers: int = int(getattr(self.config, "max_workers", 8) or 8)
        min_words: int = int(getattr(self.config, "min_words", 40) or 40)

        css_selector: Optional[str] = getattr(self.config, "css_selector", None)
        if css_selector:
            css_selector = css_selector.strip() or None
        if css_selector and " " in css_selector:
            css_selector = css_selector.replace(" ", ".")

        if not urls and not sitemap_url and not site_root:
            logging.info("No website ingestion settings found in config; skipping website source registration.")
            return None

        loader = website_loader.WebsiteLoader(
            urls=urls,
            sitemap_url=sitemap_url,
            site_root=site_root,
            limit=limit,
            max_workers=max_workers,
            min_words=min_words,
            css_selector=css_selector,
        )
        logging.info(
            f"Website loader configured with {len(urls)} URL(s), "
            f"sitemap_url={sitemap_url}, site_root={site_root}, "
            f"css_selector={css_selector}"
        )
        return loader
