import logging
import json
from typing import List

from langchain.chains.query_constructor.schema import AttributeInfo
from langchain.indexes import SQLRecordManager
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import PGVector
from langchain_core.documents import Document
from langchain_core.indexing import index
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import create_engine

from llm_utils import query_llm
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

        # Generate AttributeInfo objects for metadata
        metadata_field_info = self.generate_metadata_field_info()
        self.save_metadata_field_info(metadata_field_info)

        return result

    def generate_metadata_field_info(self) -> List[AttributeInfo]:
        """
        Generate a list of AttributeInfo objects for each different metadata type
        by querying an LLM.
        """
        # Query the vectorstore to get a sample of documents
        sample_docs = self.vectorstore.similarity_search("", k=10)

        metadata_blacklist = {"category", "element_id"}

        # Extract all unique metadata keys
        metadata_keys = set()
        for doc in sample_docs:
            metadata_keys.update(doc.metadata.keys())

        # Remove blacklisted keys
        metadata_keys.difference_update(metadata_blacklist)

        # Query the LLM to generate AttributeInfo objects
        prompt = f"Given the following metadata keys: {', '.join(metadata_keys)}, generate a list of AttributeInfo objects. Each object should have a name, description, and type. The type should be one of: string, integer, float, boolean, or list[string]."
        llm_response = query_llm(prompt)

        # Parse the LLM response
        metadata_field_info = json.loads(llm_response)

        # Convert to AttributeInfo objects
        return [AttributeInfo(**field) for field in metadata_field_info['fields']]

    def save_metadata_field_info(self, metadata_field_info: List[AttributeInfo]):
        """
        Save the generated metadata field info to a JSON file.
        """
        data_to_save = [attr.model_dump() for attr in metadata_field_info]
        json_string = json.dumps(data_to_save, indent=2)

        with open(f"{self.config.collection_name}_metadata_field_info.json", "w") as f:
            f.write(json_string)

        logging.info(f"Metadata field info saved to {self.config.collection_name}_metadata_field_info.json")
