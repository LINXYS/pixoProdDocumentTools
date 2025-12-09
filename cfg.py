import logging
import os
import sys
import yaml
from dotenv import load_dotenv

# Centralized supported options and defaults (single source of truth)
SUPPORTED_EMBEDDING_PROVIDERS = ["openai", "azure_openai", "ollama", "google"]
SUPPORTED_LLM_PROVIDERS = ["openai", "anthropic", "google", "azure_openai", "groq", "ollama"]
SUPPORTED_EMBEDDING_MODELS = {
    "openai": ["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"]
    # Other providers can be added here as needed
}
OPENAI_DEFAULT_VECTOR_SIZES = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}

# Single default config (used by both cfg.py and setup.py)
DEFAULT_CONFIG = {
    "chunk_size": 1000,
    "chunk_overlap": 200,
    "use_chunking": True,
    "embedding_provider": "openai",
    "embedding_model": "text-embedding-3-large",
    "vector_size": None,  # None => auto-resolve for OpenAI in load_config
    "llm_provider": "openai",
    "collection_name": "documents",
}

# Defaults for ingestion-specific settings
DEFAULT_INGESTION_CONFIG = {
    "urls": [],
    "sitemap_url": None,
    "css_selector": None,
    "site_root": None,
    "remote_source": None,
}

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
            embedding_model: str = "text-embedding-3-large",
            vector_size: int | None = None,
            llm_provider: str = "openai",
            collection_name: str = "documents",
            record_manager_db_url: str = None,
            # Ingestion-specific settings
            urls: list | None = None,
            sitemap_url: str | None = None,
            css_selector: str | None = None,
            site_root: str | None = None,
            limit: int | None = None,
            max_workers: int | None = None,
            min_words: int | None = None,
            remote_source: dict | None = None,
    ):
        self.database_url = database_url
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.use_chunking = use_chunking
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.vector_size = vector_size
        self.llm_provider = llm_provider
        self.collection_name = collection_name
        # Use the provided record_manager_db_url or default to the main database URL.
        self.record_manager_db_url = record_manager_db_url or database_url

        # Ingestion-specific
        self.urls = urls or []
        self.sitemap_url = sitemap_url
        self.css_selector = css_selector
        self.site_root = site_root
        self.limit = limit
        self.max_workers = max_workers
        self.min_words = min_words
        # Remote source configuration (FTP/FTPS/SFTP)
        self.remote_source = remote_source

    def __repr__(self):
        return (
            f"IngestionConfig(database_url={self.database_url}, chunk_size={self.chunk_size}, "
            f"chunk_overlap={self.chunk_overlap}, use_chunking={self.use_chunking}, "
            f"embedding_provider={self.embedding_provider}, embedding_model={self.embedding_model}, "
            f"vector_size={self.vector_size}, "
            f"collection_name={self.collection_name}, "
            f"urls={len(self.urls)} URLs, "
            f"sitemap_url={self.sitemap_url}, "
            f"css_selector={self.css_selector}, "
            f"remote_source={'yes' if self.remote_source else 'no'}, "
            f"record_manager_db_url={self.record_manager_db_url})"
        )

def load_config(config_file: str = "config.yaml") -> IngestionConfig:
    """
    Load configuration from a YAML file located in the current working directory,
    and load necessary environment variables (DATABASE_URL and RECORD_MANAGER_DATABASE_URL).
    """
    load_dotenv()

    # Determine the directory to search for the config file.
    # We intentionally use the current working directory so that each project
    # subfolder (where main.py is executed from the BAT script) can have its
    # own independent config.yaml.
    main_dir = os.getcwd()
    config_file_path = os.path.join(main_dir, config_file)

    # Check if the config file exists; if not, create it with default values.
    if not os.path.exists(config_file_path):
        # Start from centralized DEFAULT_CONFIG
        default_config = dict(DEFAULT_CONFIG)
        with open(config_file_path, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f)
        logging.info(f"Default configuration file '{config_file_path}' created.")

    try:
        with open(config_file_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Error loading configuration file {config_file_path}: {e}")
        raise

    if config_data is None:
        config_data = {}

    # Get chunking and embedding settings from the YAML file.
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config_data or {})

    chunk_size = cfg.get("chunk_size", DEFAULT_CONFIG["chunk_size"])
    chunk_overlap = cfg.get("chunk_overlap", DEFAULT_CONFIG["chunk_overlap"])
    use_chunking = cfg.get("use_chunking", DEFAULT_CONFIG["use_chunking"])
    embedding_provider = cfg.get("embedding_provider", DEFAULT_CONFIG["embedding_provider"])
    # Read embedding model and vector size (support a couple synonyms)
    embedding_model = cfg.get("embedding_model", DEFAULT_CONFIG["embedding_model"])
    vector_size = cfg.get("vector_size", cfg.get("embedding_dimensions", DEFAULT_CONFIG["vector_size"]))
    # Resolve OpenAI default vector sizes if not provided
    if embedding_provider and embedding_provider.lower() == "openai":
        if vector_size in (None, "", 0):
            vector_size = OPENAI_DEFAULT_VECTOR_SIZES.get(embedding_model, 1536)
    llm_provider = cfg.get("llm_provider", DEFAULT_CONFIG["llm_provider"])
    # Try to load the collection name from "collection_name" key; if not found, check "project_id".
    collection_name = cfg.get("collection_name", cfg.get("project_id", DEFAULT_CONFIG["collection_name"]))

    # Ingestion-specific configuration (website, remote source, etc.)
    ingestion_cfg = config_data.get("ingestion") or {}
    # Start from ingestion defaults
    ing = dict(DEFAULT_INGESTION_CONFIG)
    ing.update(ingestion_cfg or {})

    urls = ing.get("urls") or []
    # Allow URLs to be provided as a single string (newline/comma separated)
    if isinstance(urls, str):
        raw = urls
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            for part in line.split(","):
                u = part.strip()
                if u:
                    items.append(u)
        # dedupe while preserving order
        seen = set()
        ordered = []
        for u in items:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        urls = ordered

    sitemap_url = ing.get("sitemap_url") or None
    css_selector = ing.get("css_selector") or None
    site_root = ing.get("site_root") or None
    limit = ing.get("limit")
    max_workers = ing.get("max_workers")
    min_words = ing.get("min_words")
    remote_source = ing.get("remote_source") or None

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
        embedding_model=embedding_model,
        vector_size=vector_size,
        llm_provider=llm_provider,
        collection_name=collection_name,
        record_manager_db_url=record_manager_db_url,
        # Ingestion-specific
        urls=urls,
        sitemap_url=sitemap_url,
        css_selector=css_selector,
        site_root=site_root,
        limit=limit,
        max_workers=max_workers,
        min_words=min_words,
        remote_source=remote_source,
    )
    # Warn if an explicit vector_size disagrees with known defaults (OpenAI only)
    if embedding_provider.lower() == "openai":
        expected = OPENAI_DEFAULT_VECTOR_SIZES.get(embedding_model)
        if expected and config.vector_size and config.vector_size != expected:
            logging.warning(f"Configured vector_size={config.vector_size} differs from OpenAI default for {embedding_model} ({expected}). Proceeding with configured value.")
    logging.info(f"Configuration loaded: {config}")
    return config
