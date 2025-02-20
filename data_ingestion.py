import glob
import logging
import os
import shutil
import warnings
import json
from os.path import join
from pathlib import Path
from typing import List

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_unstructured import UnstructuredLoader
from tqdm import tqdm

from cfg import IngestionConfig
from langchain_indexer import LangChainIndexer


class DataIngestionApp:
    """
    Ties together document loaders, optional chunking, and indexing via LangChain.
    """
    def __init__(self, config: IngestionConfig):
        self.config = config
        self.indexer = LangChainIndexer(config)
        self.loaders: List[BaseLoader] = []

    def register_loader(self, loader: BaseLoader):
        """
        Register a new document loader.
        """
        self.loaders.append(loader)
        logging.info(f"Registered loader: {loader.__class__.__name__}")

    def get_text_splitter(self) -> RecursiveCharacterTextSplitter:
        """
        Create a text splitter based on configured chunking parameters.
        """
        return RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap
        )

    def ingest_data(self, cleanup_mode: str = "incremental"):
        """
        Loads documents from all registered loaders, applies chunking if enabled,
        and indexes them using LangChain's indexing API.
        """
        all_documents: List[Document] = []

        # Load .pixodoc files first
        files_dir = Path.cwd() / "files"

        pixodoc_files = glob.glob(join(files_dir, "**/*.pixodoc"), recursive=True)
        for file in tqdm(pixodoc_files, desc="Processing .pixodoc files", unit="file"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    doc_data = json.load(f)
                    doc = Document(
                        page_content=doc_data['page_content'],
                        metadata=doc_data['metadata']
                    )
                    if len(doc.page_content) > self.config.chunk_size:
                        # Chunk the document if it's larger than the chunk size
                        text_splitter = self.get_text_splitter()
                        chunks = text_splitter.split_text(doc.page_content)
                        all_documents.extend([Document(page_content=chunk, metadata=doc.metadata) for chunk in chunks])
                        logging.info(f"Chunked document {file} into {len(chunks)} parts.")
                    else:
                        all_documents.append(doc)
            except UnicodeDecodeError:
                logging.warning(f"Failed to decode file {file} using UTF-8. Trying with 'latin-1' encoding.")
                try:
                    with open(file, 'r', encoding='latin-1') as f:
                        doc_data = json.load(f)
                        doc = Document(
                            page_content=doc_data['page_content'],
                            metadata=doc_data['metadata']
                        )
                        if len(doc.page_content) > self.config.chunk_size:
                            # Chunk the document if it's larger than the chunk size
                            text_splitter = self.get_text_splitter()
                            chunks = text_splitter.split_text(doc.page_content)
                            all_documents.extend([Document(page_content=chunk, metadata=doc.metadata) for chunk in chunks])
                            logging.info(f"Chunked document {file} into {len(chunks)} parts.")
                        else:
                            all_documents.append(doc)
                except json.JSONDecodeError:
                    logging.error(f"Failed to parse JSON in file {file}")
                except Exception as e:
                    logging.error(f"Failed to process file {file}: {str(e)}")
            except Exception as e:
                logging.error(f"Failed to process file {file}: {str(e)}")
        logging.info(f"Loaded {len(pixodoc_files)} .pixodoc files.")

        # Iterate over loaders with a progress bar.
        for loader in tqdm(self.loaders, desc="Processing loaders", unit="loader"):
            if self.config.use_chunking:
                docs = loader.load_and_split(text_splitter=self.get_text_splitter())
                logging.info(f"Loader {loader.__class__.__name__} provided {len(docs)} chunked documents.")
            else:
                docs = loader.load()
                logging.info(f"Loader {loader.__class__.__name__} provided {len(docs)} documents.")
            all_documents.extend(docs)
        if all_documents:
            self.indexer.index_documents(all_documents, cleanup_mode=cleanup_mode)
        else:
            logging.info("No documents to index.")

    def get_unstructured_loader(self, directory_path: str):
        """
        Create a new unstructured loader. If Tesseract is not installed,
        image files are skipped.
        """
        # Check if Tesseract is installed (i.e. available in PATH)
        if shutil.which("tesseract") is None:
            warnings.warn("Tesseract is not installed or not in PATH. All image files will be skipped.")
            tesseract_installed = False
        else:
            tesseract_installed = True

        # Get all files recursively in the directory
        files = glob.glob(join(directory_path, "**/*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]

        # If Tesseract is not installed, filter out image files by their extensions.
        if not tesseract_installed:
            image_extensions = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"}
            files = [
                f for f in files
                if os.path.splitext(f)[1].lower() not in image_extensions
            ]

        # remove .pixodoc files
        files = [
            f for f in files
            if os.path.splitext(f)[1].lower() != ".pixodoc"
        ]

        return UnstructuredLoader(
            file_path=files,
            chunking_strategy="basic",
            max_characters=self.config.chunk_size * 4,
            overlap=self.config.chunk_overlap * 4,
            include_orig_elements=False,
        )
