import logging

from langchain_community.document_loaders import DirectoryLoader

from cfg import load_config
from data_ingestion import DataIngestionApp
from database.setup_db import initialize_databases


def main():
    # Setup basic logging.
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Load configuration from the YAML config file and environment variable.
    try:
        config = load_config("config.yaml")
    except Exception as e:
        logging.error(f"Could not load configuration: {e}")
        return

    # Initialize databases if they don't exist
    initialize_databases(config)

    # Create the main application instance.
    app = DataIngestionApp(config=config)

    # Register document loaders.
    # For example, register a FileLoader (update the file path as needed).
    loader = DirectoryLoader("test", glob="**/*.md")
    app.register_loader(loader)

    # Run the ingestion process with incremental cleanup mode.
    app.ingest_data()


if __name__ == "__main__":
    main()
