import logging
import os
import sys
import yaml
from dotenv import load_dotenv

class IngestionConfig:
    """
    Configuration for data ingestion.

    - The vectorstore database URL is loaded from the environment (DATABASE_URL).
    - The record manager's DB URL is taken from the YAML config if provided; otherwise, it defaults to DATABASE_URL.
    - Other parameters (chunking, embedding provider, collection name) are loaded from YAML.
    """

    def __init__(
            self,
            database_url: str,
            chunk_size: int = 1000,
            chunk_overlap: int = 200,
            use_chunking: bool = True,
            embedding_provider: str = "openai",
            llm_provider: str = "openai",
            collection_name: str = "documents",
            record_manager_db_url: str = None,
    ):
        self.database_url = database_url
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.use_chunking = use_chunking
        self.embedding_provider = embedding_provider
        self.llm_provider = llm_provider
        self.collection_name = collection_name
        # Use the provided record_manager_db_url or default to the main database URL.
        self.record_manager_db_url = record_manager_db_url or database_url

    def __repr__(self):
        return (
            f"IngestionConfig(database_url={self.database_url}, chunk_size={self.chunk_size}, "
            f"chunk_overlap={self.chunk_overlap}, use_chunking={self.use_chunking}, "
            f"embedding_provider={self.embedding_provider}, collection_name={self.collection_name}, "
            f"record_manager_db_url={self.record_manager_db_url})"
        )

def load_config(config_file: str = "config.yaml") -> IngestionConfig:
    """
    Load configuration from a YAML file located in the same directory as the main script,
    and load necessary environment variables (DATABASE_URL and RECORD_MANAGER_DATABASE_URL).
    """
    load_dotenv()

    # Determine the directory of the main script.
    try:
        # Try to get the __file__ attribute of the main module.
        main_script_path = sys.modules["__main__"].__file__
    except (KeyError, AttributeError):
        # Fallback to this file's directory if __main__ is not available.
        main_script_path = __file__

    main_dir = os.path.dirname(os.path.abspath(main_script_path))
    config_file_path = os.path.join(main_dir, config_file)

    # Check if the config file exists; if not, create it with default values.
    if not os.path.exists(config_file_path):
        default_config = {
            "chunk_size": 1000,
            "chunk_overlap": 200,
            "use_chunking": True,
            "embedding_provider": "openai",
            "llm_provider": "openai",
            # Allow both "collection_name" and "project_id" as keys for compatibility.
            "collection_name": "documents",
        }
        with open(config_file_path, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f)
        logging.info(f"Default configuration file '{config_file_path}' created.")

    try:
        with open(config_file_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Error loading configuration file {config_file_path}: {e}")
        raise

    # Get chunking and embedding settings from the YAML file.
    chunk_size = config_data.get("chunk_size", 1000)
    chunk_overlap = config_data.get("chunk_overlap", 200)
    use_chunking = config_data.get("use_chunking", True)
    embedding_provider = config_data.get("embedding_provider", "openai")
    llm_provider = config_data.get("llm_provider", "openai")
    # Try to load the collection name from "collection_name" key; if not found, check "project_id".
    collection_name = config_data.get("collection_name", config_data.get("project_id", "documents"))

    print("Loaded collection_name:", collection_name)

    # Load the vectorstore database URL from the environment.
    db_url = os.getenv("DATABASE_URL")
    record_manager_db_url = os.getenv("RECORD_MANAGER_DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set. "
                         "Please set it in your environment or in a .env file.")

    if not record_manager_db_url:
        raise ValueError("RECORD_MANAGER_DATABASE_URL is not set. "
                         "Please set it in your environment or in a .env file.")

    config = IngestionConfig(
        database_url=db_url,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        use_chunking=use_chunking,
        embedding_provider=embedding_provider,
        llm_provider=llm_provider,
        collection_name=collection_name,
        record_manager_db_url=record_manager_db_url,
    )
    logging.info(f"Configuration loaded: {config}")
    return config
