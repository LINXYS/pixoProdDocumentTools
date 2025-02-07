import logging

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

    loader = app.getUnstructuredLoader(directory_path="files")

    app.register_loader(loader)

    app.ingest_data()


if __name__ == "__main__":
    main()
