import glob
import logging
import os
from os.path import join
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

    def getUnstructuredLoader(self, directory_path: str):
        """
        Create a new unstructured loader.
        """

        files = glob.glob(join(directory_path, "**/*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]

        return UnstructuredLoader(
            file_path=files,
            chunking_strategy="basic",
            max_characters=self.config.chunk_size*4,
            overlap=self.config.chunk_overlap*4,
            include_orig_elements=False,
        )
