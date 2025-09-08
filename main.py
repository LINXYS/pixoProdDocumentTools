import logging

from pathlib import Path
from cfg import load_config
from data_ingestion import DataIngestionApp


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

    # Create the main application instance.
    app = DataIngestionApp(config=config, chunking_enabled=True)

    files_dir = current_dir / "files"
    loader = app.get_docling_loader(directory_path=files_dir)

    app.register_loader(loader)

    app.ingest_data()


if __name__ == "__main__":
    main()
