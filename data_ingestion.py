import logging
import sys, asyncio

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import json
from os.path import join
from pathlib import Path
from typing import List, Optional
from collections import defaultdict

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_docling import DoclingLoader
from langchain_docling.loader import ExportType
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from tqdm import tqdm

from cfg import IngestionConfig
from langchain_indexer import LangChainIndexer
import glob
import os
import shutil
import warnings

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

    def __init__(self, config: IngestionConfig, chunking_enabled: bool = True):
        self.config = config
        self.indexer = LangChainIndexer(config)
        self.loaders: List[BaseLoader] = []
        self.disable_bar = True
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
        all_documents: List[Document] = []

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

            if self.config.use_chunking:
                splitter = self.get_token_splitter()
                parts = splitter.split_text(doc.page_content or "")
                all_documents.extend([Document(page_content=p, metadata=doc.metadata) for p in parts])
                logging.info(
                    f"Split .pixodoc {os.path.basename(file)} into {len(parts)} chunk(s) "
                    f"(target={self.config.chunk_size}, overlap={self.config.chunk_overlap})."
                )
            else:
                all_documents.append(doc)

        logging.info(f"Loaded {len(pixodoc_files)} .pixodoc files.")
        print()
        logging.info("Starting ingestion... Processing loaders.")

        # ---------- Process registered loaders ----------
        for loader in tqdm(
                self.loaders,
                desc="Processing loaders",
                unit="loader",
                disable=self.disable_bar,
                file=sys.stdout,
        ):
            try:
                if isinstance(loader, DoclingLoader):
                    # Expect loader to be configured with ExportType.DOC_CHUNKS and a Docling chunker.
                    docs = loader.load()
                    if not docs:
                        logging.info(f"{loader.__class__.__name__} returned no documents.")
                        continue

                    # If user disabled chunking globally, we still accept Docling's output as-is.
                    # We do NOT re-split Docling chunks here.
                    # NEW: persist Docling-processed outputs to disk under processed_files/, preserving files/ structure
                    try:
                        self._persist_docling_outputs(docs, files_root=files_dir)
                    except Exception as e:
                        logging.error(f"Failed to write processed Docling outputs: {e}")

                    all_documents.extend(docs)
                    logging.info(
                        f"{loader.__class__.__name__} (Docling): received {len(docs)} pre-chunked document(s)."
                    )
                else:
                    # Normal loaders: load raw then apply our token-aware splitter (if enabled)
                    raw_docs = loader.load()
                    if not raw_docs:
                        logging.info(f"{loader.__class__.__name__} returned no documents.")
                        continue

                    if self.config.use_chunking:
                        splitter = self.get_token_splitter()
                        before = len(all_documents)
                        for d in raw_docs:
                            parts = splitter.split_text(d.page_content or "")
                            all_documents.extend(
                                [Document(page_content=p, metadata=d.metadata) for p in parts]
                            )
                        added = len(all_documents) - before
                        logging.info(
                            f"{loader.__class__.__name__}: split {len(raw_docs)} doc(s) "
                            f"into {added} chunk(s) (target={self.config.chunk_size}, "
                            f"overlap={self.config.chunk_overlap})."
                        )
                    else:
                        all_documents.extend(raw_docs)
                        logging.info(
                            f"{loader.__class__.__name__}: added {len(raw_docs)} document(s) (no chunking)."
                        )
            except Exception as e:
                logging.error(f"Loader {loader.__class__.__name__} failed: {e}")

        # ---------- Index ----------
        if all_documents:
            self.indexer.index_documents(all_documents, cleanup_mode=cleanup_mode)
        else:
            logging.info("No documents to index.")
        print()
        logging.info("Ingestion complete.")

    def _build_docling_converter(self):
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        device = AcceleratorDevice.AUTO
        torch_info = {}
        try:
            import torch, subprocess, shutil
            torch_info = {
                "torch_version": getattr(torch, "__version__", "?"),
                "torch_cuda": getattr(torch.version, "cuda", None),
                "cuda_available": torch.cuda.is_available(),
                "cuda_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
                "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            }
            if torch.cuda.is_available():
                device = AcceleratorDevice.CUDA
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = AcceleratorDevice.MPS
            else:
                device = AcceleratorDevice.CPU
        except Exception as e:
            logging.warning(f"PyTorch check failed; defaulting to AUTO. Error: {e}")
            device = AcceleratorDevice.AUTO

        logging.info(f"Docling/torch info: {torch_info}")
        logging.info(f"Docling accelerator set to: {getattr(device, 'name', device)}")

        accel = AcceleratorOptions(num_threads=os.cpu_count() or 8, device=device)
        pdf_opts = PdfPipelineOptions()
        pdf_opts.accelerator_options = accel

        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
        )

    def get_docling_loader(self, directory_path: str):
        """
        Return a Docling-based loader over all files in `directory_path`.
        Uses MARKDOWN export and lets the token-aware splitter handle chunking.
        """

        # Gather files
        files = glob.glob(join(directory_path, "**/*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]

        # Remove .pixodoc files (handled elsewhere)
        files = [f for f in files if os.path.splitext(f)[1].lower() != ".pixodoc"]

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
            return DoclingLoader(
                file_path=files,
                export_type=ExportType.MARKDOWN,
                converter=converter,
            )

        # Preferred path: Docling performs chunking and returns pre-chunked LangChain Documents
        return DoclingLoader(
            file_path=files,
            export_type=ExportType.DOC_CHUNKS,
            chunker=chunker,
            converter=converter,
        )

    # --- NEW: utilities to persist Docling-processed files ---
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
