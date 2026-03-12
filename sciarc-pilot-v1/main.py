import logging

from pathlib import Path
from cfg import load_config
from data_ingestion import DataIngestionApp
from remote_sync import sync_remote_to_local


def main():
    # Setup basic logging.
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Load configuration from the YAML config file and environment variable.
    current_dir = Path.cwd()
    try:
        # load_config will resolve config.yaml relative to the main script directory
        config = load_config()
    except Exception as e:
        logging.error(f"Could not load configuration: {e}")
        return

    # Handle "force fresh run" for ingestion progress tracking only.
    state_path = current_dir / "ingestion_state.json"
    if getattr(config, "force_fresh_run", False):
        if state_path.exists():
            try:
                state_path.unlink()
                logging.info("Force fresh run: existing ingestion_state.json removed.")
            except Exception as e:
                logging.warning(f"Failed to remove ingestion_state.json: {e}")

    # Ensure files directory exists (used for uploads and remote sync)
    files_dir = current_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # Optional: sync remote files (FTP/FTPS/SFTP) into files/
    remote_cfg = getattr(config, "remote_source", None)
    if remote_cfg:
        try:
            logging.info("Remote source configured; starting sync to local files/ directory.")
            sync_remote_to_local(remote_cfg, files_dir)
            logging.info("Remote sync completed.")
        except Exception as e:
            logging.error(f"Remote sync failed: {e}")
            return

    # Create the main application instance.
    app = DataIngestionApp(config=config, chunking_enabled=True, project_dir=current_dir)

    # NOTE:
    #  - Docling file ingestion is now driven internally by DataIngestionApp.ingest_data()
    #    using batched Docling loaders (see get_docling_loaders).
    #  - We no longer pre-register Docling loaders here; this allows multiple
    #    Docling batches to be discovered and processed until all files are done.

    # Register URL Loader
    loader_websites = app.get_website_loader()
    if loader_websites is not None:
        app.register_loader(loader_websites)

    app.ingest_data()


if __name__ == "__main__":
    main()
