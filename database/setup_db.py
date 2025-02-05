import logging
import psycopg2
from cfg import IngestionConfig

def initialize_databases(config: IngestionConfig):
    """
    Initialize the main database and record manager database if they don't exist,
    ensuring that they are created with UTF8 encoding.
    """
    databases = [
        (config.database_url, "Main database"),
        (config.record_manager_db_url, "Record manager database")
    ]

    for db_url, db_label in databases:
        try:
            # Extract the database name from the URL
            db_name_from_url = db_url.split('/')[-1]

            # Connect to the default 'postgres' database to execute database-level commands.
            # We replace the final part of the URL with 'postgres'
            base_url = db_url.rsplit('/', 1)[0]
            conn = psycopg2.connect(f"{base_url}/postgres")
            conn.autocommit = True
            cursor = conn.cursor()

            # Check if the database exists
            cursor.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (db_name_from_url,)
            )
            exists = cursor.fetchone()

            if not exists:
                # Create the database with UTF-8 encoding using template0.
                cursor.execute(
                    f"CREATE DATABASE {db_name_from_url} WITH ENCODING 'UTF8' TEMPLATE template0"
                )
                logging.info(f"{db_label} '{db_name_from_url}' created successfully with UTF-8 encoding.")
            else:
                logging.info(f"{db_label} '{db_name_from_url}' already exists.")

            cursor.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error creating {db_label}: {e}")
