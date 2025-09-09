# langchain_indexer.py
import logging
from typing import List, Optional

from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError

try:
    import psycopg  # psycopg v3
except Exception:
    psycopg = None

from langchain.indexes import SQLRecordManager
from langchain_core.documents import Document
from langchain_core.indexing import index
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings

from langchain_postgres import PGEngine, PGVectorStore

from cfg import IngestionConfig


def _is_duplicate_table_error(err: ProgrammingError) -> bool:
    orig = getattr(err, "orig", None)
    if psycopg is not None:
        try:
            from psycopg.errors import DuplicateTable, DuplicateSchema, DuplicateObject  # type: ignore
        except Exception:
            DuplicateTable = DuplicateSchema = DuplicateObject = None  # type: ignore
        if (DuplicateTable and isinstance(orig, DuplicateTable)) or \
           (DuplicateSchema and isinstance(orig, DuplicateSchema)) or \
           (DuplicateObject and isinstance(orig, DuplicateObject)):
            return True

    msg = (str(err) or "").lower()
    return (
        "duplicatetable" in msg
        or "duplicate table" in msg
        or "already exists" in msg
        or "existiert bereits" in msg
    )


class LangChainIndexer:
    """
    Uses langchain-postgres v2 (PGEngine + PGVectorStore).
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
            vector_size = self._infer_vector_size(self.embedding)
        else:
            try:
                model = getattr(self.embedding, "model", "") or getattr(self.embedding, "model_name", "")
                known = {
                    "text-embedding-3-large": 3072,
                    "text-embedding-3-small": 1536,
                    "text-embedding-ada-002": 1536,
                }
                if model in known and known[model] != vector_size:
                    logging.warning(
                        f"Configured vector_size={vector_size} differs from OpenAI default for {model} ({known[model]}). Using configured value."
                    )
            except Exception:
                pass

        try:
            self.pg_engine.init_vectorstore_table(
                table_name=self.table_name,
                vector_size=vector_size,
            )
        except ProgrammingError as e:
            if _is_duplicate_table_error(e):
                logging.info('Table "%s" already exists; continuing.', self.table_name)
            else:
                raise

        # 4) Create the vectorstore (sync)
        self.vectorstore = PGVectorStore.create_sync(
            engine=self.pg_engine,
            table_name=self.table_name,
            embedding_service=self.embedding,
        )

        # 5) Record manager (make engine robust)
        rm_url = self._ensure_psycopg_scheme(config.record_manager_db_url)
        rm_engine = create_engine(
            rm_url,
            pool_pre_ping=True,         # transparently reconnect if dead
            pool_recycle=900,           # recycle every 15 min
            connect_args={
                "options": "-c client_encoding=utf8 -c statement_timeout=600000",
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 30,
                "keepalives_count": 5,
            },
        )
        self.rm_engine = rm_engine
        namespace = f"pgvector/{self.table_name}"
        self.record_manager = SQLRecordManager(namespace, engine=rm_engine)

        try:
            self.record_manager.create_schema()
        except ProgrammingError as e:
            if _is_duplicate_table_error(e):
                logging.info("Record manager schema already exists; continuing.")
            else:
                raise

    def index_documents(self, docs: List[Document], cleanup_mode: Optional[str] = "incremental") -> dict:
        """
        Index documents using LangChain's indexing API.
        """
        logging.info("Starting indexing via LangChain indexing API...")
        # drop any stale pooled conns before long indexing
        try:
            self.rm_engine.dispose()
        except Exception:
            pass
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
        if url.startswith("postgresql+"):
            return url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    @staticmethod
    def _infer_vector_size(embedding) -> int:
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
        vec = embedding.embed_query("dimension probe")
        return len(vec)
