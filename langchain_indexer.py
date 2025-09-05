import logging
from typing import List, Optional

from sqlalchemy import create_engine
from langchain.indexes import SQLRecordManager
from langchain_core.documents import Document
from langchain_core.indexing import index
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings

from langchain_postgres import PGEngine, PGVectorStore

from cfg import IngestionConfig


class LangChainIndexer:
    """
    Uses langchain-postgres v2 (PGEngine + PGVectorStore).

    - Creates a Postgres engine and vector store table
    - Instantiates a PGVectorStore (sync)
    - Uses SQLRecordManager + LangChain indexing API for de-dupe/cleanup

    NOTE: The record manager namespace still includes the collection name so
    each collection gets its own record manager.
    """

    def __init__(self, config: IngestionConfig):
        self.config = config
        self.table_name = config.collection_name

        # 1) Embeddings
        provider = config.embedding_provider.lower()
        if provider == "openai":
            model = getattr(config, "embedding_model", "text-embedding-3-large")
            self.embedding = OpenAIEmbeddings(model=model)
        elif provider == "huggingface":
            model_name = getattr(config, "embedding_model", None)
            self.embedding = (
                HuggingFaceEmbeddings(model_name=model_name)
                if model_name
                else HuggingFaceEmbeddings()
            )
        else:
            raise ValueError(f"Unsupported embedding provider: {config.embedding_provider}")

        # 2) Build a PGEngine (sync driver so we can stay fully synchronous)
        pg_url = self._ensure_psycopg_scheme(config.database_url)
        self.pg_engine = PGEngine.from_connection_string(url=pg_url)

        # 3) Create the vectorstore table (idempotent)
        vector_size = getattr(config, "vector_size", None)
        if not vector_size:
            # Fallback only if not provided in config
            vector_size = self._infer_vector_size(self.embedding)
        else:
            # Soft validation for OpenAI known sizes
            try:
                model = getattr(self.embedding, "model", "") or getattr(self.embedding, "model_name", "")
                known = {"text-embedding-3-large": 3072, "text-embedding-3-small": 1536, "text-embedding-ada-002": 1536}
                if model in known and known[model] != vector_size:
                    logging.warning(f"Configured vector_size={vector_size} differs from OpenAI default for {model} ({known[model]}). Using configured value.")
            except Exception:
                pass
        self.pg_engine.init_vectorstore_table(
            table_name=self.table_name,
            vector_size=vector_size,
            # If you need SQL-filterable metadata columns later, declare them here:
            # metadata_columns=[Column("source", "TEXT"), Column("topic", "TEXT")]
        )

        # 4) Create the vectorstore (sync)
        self.vectorstore = PGVectorStore.create_sync(
            engine=self.pg_engine,
            table_name=self.table_name,
            embedding_service=self.embedding,
            # If you declared typed metadata columns above and want to filter on them:
            # metadata_columns=["source", "topic"],
        )

        # 5) Record manager (separate DB or same Postgres — your choice)
        rm_url = self._ensure_psycopg_scheme(config.record_manager_db_url)
        rm_engine = create_engine(rm_url)
        namespace = f"pgvector/{self.table_name}"
        self.record_manager = SQLRecordManager(namespace, engine=rm_engine)
        self.record_manager.create_schema()

    def index_documents(self, docs: List[Document], cleanup_mode: Optional[str] = "incremental") -> dict:
        """
        Index documents using LangChain's indexing API.
        cleanup_mode: None | "incremental" | "full" | "scoped_full"
        `source_id_key` stays "source" (must be present in each Document.metadata).
        """
        logging.info("Starting indexing via LangChain indexing API...")
        result = index(
            docs,
            self.record_manager,
            self.vectorstore,
            cleanup=cleanup_mode,
            source_id_key="source",
        )
        logging.info("Indexing result: %s", result)
        return result

    @staticmethod
    def _ensure_psycopg_scheme(url: str) -> str:
        """Force psycopg driver for sync PGEngine/PGVectorStore APIs."""
        if url.startswith("postgresql+"):
            return url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    @staticmethod
    def _infer_vector_size(embedding) -> int:
        """Best effort: use known OpenAI dims, else probe one embedding."""
        try:
            model = getattr(embedding, "model", "") or getattr(embedding, "model_name", "")
            if "text-embedding-3-large" in model:
                return 3072
            if "text-embedding-3-small" in model:
                return 1536
            if "text-embedding-ada-002" in model:
                return 1536
        except Exception:
            pass
        # Fallback probe (runs one embedding request)
        vec = embedding.embed_query("dimension probe")
        return len(vec)
