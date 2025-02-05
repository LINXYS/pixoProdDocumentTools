import glob
import logging
import os
from os.path import join

from langchain_community.document_loaders import DirectoryLoader, UnstructuredFileLoader
from langchain_unstructured import UnstructuredLoader
from unstructured.cleaners.core import clean_extra_whitespace, bytes_string_to_string

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
    files = glob.glob(join("test", "**/*"), recursive=True)
    files = [f for f in files if os.path.isfile(f)]

    loader_cls = UnstructuredLoader(
        file_path=files,
        chunking_strategy="basic",
        max_characters=config.chunk_size*4,
        overlap=config.chunk_overlap*4,
        include_orig_elements=False
    )
    # loader = DirectoryLoader(
    #     "test",
    #     glob="**",
    #     show_progress=True,
    #     use_multithreading=True,
    #     max_concurrency=4,
    #     loader_cls=loader_cls
    # )

    app.register_loader(loader_cls)

    # Run the ingestion process with incremental cleanup mode.
    app.ingest_data()


if __name__ == "__main__":
    main()
