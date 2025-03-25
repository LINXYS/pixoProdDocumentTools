import logging
import os

from pathlib import Path

from dotenv import load_dotenv

from cfg import load_config
from data_ingestion import DataIngestionApp
from database.setup_db import initialize_databases
import krb_xml_loader


def main():
    # Setup basic logging.
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Load configuration from the YAML config file and environment variable.
    try:
        current_dir = Path.cwd()
        config_path = current_dir / "config.yaml"
        config = load_config(config_path)
    except Exception as e:
        logging.error(f"Could not load configuration: {e}")
        return

    # Initialize databases if they don't exist
    initialize_databases(config)

    # Create the main application instance.
    app = DataIngestionApp(config=config, chunking_enabled=True)

    files_dir = current_dir / "files"

    load_dotenv()

    krb_url = os.getenv("KRB_URL")

    if not krb_url:
        logging.error("KRB_URL environment variable is not set to the utl of the xml files.")
        return

    loader = krb_xml_loader.KRBXmLLoader(url=krb_url)

    app.register_loader(loader)

    app.ingest_data()


if __name__ == "__main__":
    main()
