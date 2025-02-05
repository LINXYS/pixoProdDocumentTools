import logging
from typing import List

from langchain.indexes import SQLRecordManager
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import PGVector
from langchain_core.documents import Document
from langchain_core.indexing import index
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import create_engine

from cfg import IngestionConfig


class LangChainIndexer:
    """
    Sets up an embeddings provider, a PGVector vector store, and a SQLRecordManager,
    then indexes documents via LangChain's indexing API.

    Note: The SQLRecordManager is instantiated with a namespace that includes the collection name,
          so each collection gets its own record manager.
    """

    def __init__(self, config: IngestionConfig):
        self.config = config

        # Instantiate the embeddings provider.
        if config.embedding_provider.lower() == "openai":
            embedding = OpenAIEmbeddings()
        elif config.embedding_provider.lower() == "huggingface":
            embedding = HuggingFaceEmbeddings()
        else:
            raise ValueError(f"Unsupported embedding provider: {config.embedding_provider}")

        # Create a PGVector vectorstore using Postgres.
        self.vectorstore = PGVector(
            embedding_function=embedding,
            collection_name=config.collection_name,
            connection_string=config.database_url,
            engine_args={"client_encoding": "utf8"},
            use_jsonb=True
        )

        # Initialize a SQLRecordManager with a namespace that is unique to the collection.
        engine = create_engine(
            config.record_manager_db_url,
            connect_args={"client_encoding": "utf8"}
        )

        # Initialize a SQLRecordManager with a namespace that is unique to the collection,
        # and pass the created engine to it.
        namespace = f"pgvector/{config.collection_name}"
        self.record_manager = SQLRecordManager(namespace, engine=engine)
        self.record_manager.create_schema()  # Create the necessary schema in Postgres.

    def index_documents(self, docs: List[Document], cleanup_mode: str = "incremental") -> dict:
        """
        Index documents using LangChain's indexing API.
        Supported cleanup modes: None, "incremental", "full", or "scoped_full".
        The `source_id_key` is set to "source" (which must be provided in each Document's metadata).
        """
        logging.info("Starting indexing via LangChain indexing API...")
        result = index(
            docs,
            self.record_manager,
            self.vectorstore,
            cleanup=cleanup_mode,
            source_id_key="source"
        )
        logging.info(f"Indexing result: {result}")
        return result
