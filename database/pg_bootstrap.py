# pg_bootstrap.py
from __future__ import annotations

import os
from getpass import getpass
from typing import Tuple
from urllib.parse import quote_plus

import psycopg
from psycopg import sql

# LangChain indexing (SQL Record Manager)
# pip install langchain sqlalchemy
from langchain.indexes import SQLRecordManager


# --- Small URL helpers --------------------------------------------------------

def _sa_url(user: str, pwd: str, host: str, port: int, db: str) -> str:
    """SQLAlchemy-style URL for LangChain + psycopg3 driver."""
    return f"postgresql+psycopg://{user}:{quote_plus(pwd)}@{host}:{port}/{db}"

def _dsn(sa_url: str) -> str:
    """psycopg DSN from SQLAlchemy URL (drop '+psycopg')."""
    return sa_url.replace("postgresql+psycopg", "postgresql").replace("postgresql+psycopg2", "postgresql")


# --- Postgres operations -------------------------------------------------------

def _create_database(admin_dsn: str, db_name: str) -> None:
    """CREATE DATABASE if it doesn't exist."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


def _enable_pgvector(db_dsn: str) -> None:
    """CREATE EXTENSION vector in the target database."""
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")  # requires pgvector installed on server


def _init_record_manager_schema(db_url: str, namespace: str) -> None:
    """Create SQLRecordManager tables in the record-manager DB."""
    rm = SQLRecordManager(namespace, db_url=db_url)
    rm.create_schema()


# --- Public entrypoint ---------------------------------------------------------

def bootstrap_postgres_if_needed(
    collection_name: str,
    default_vec_db: str = "langchain",
    default_rm_db: str = "record_manager",
) -> Tuple[str, str]:
    """
    Ensures DATABASE_URL and RECORD_MANAGER_DATABASE_URL exist.
    If either is missing, asks whether to create DBs and does the setup.

    Returns:
        (database_url, record_manager_database_url)
    """
    db_url = os.getenv("DATABASE_URL")
    rm_url = os.getenv("RECORD_MANAGER_DATABASE_URL")

    # Nothing to do
    if db_url and rm_url:
        return db_url, rm_url

    print("DATABASE_URL and/or RECORD_MANAGER_DATABASE_URL not found in environment.")
    yn = input("Create the missing Postgres database(s) now? [y/N]: ").strip().lower()
    if yn not in ("y", "yes"):
        raise SystemExit("Exiting: required environment variables are missing and databases were not created.")

    # Gather admin connection details (to the 'postgres' maintenance DB)
    print("\nEnter Postgres admin connection (user must be able to CREATE DATABASE and CREATE EXTENSION):")
    host = input("Host [localhost]: ").strip() or "localhost"
    port = int(input("Port [5432]: ").strip() or "5432")
    user = input("Username [postgres]: ").strip() or "postgres"
    pwd = input("Password: ")
    admin_sa_url = _sa_url(user, pwd, host, port, "postgres")
    admin_dsn = _dsn(admin_sa_url)

    # Create the vector DB if needed
    if not db_url:
        vec_db_name = input(f"Vector DB name [{default_vec_db}]: ").strip() or default_vec_db
        print(f"\nCreating vector DB '{vec_db_name}' (if needed) and enabling pgvector…")
        _create_database(admin_dsn, vec_db_name)
        vec_sa_url = _sa_url(user, pwd, host, port, vec_db_name)
        try:
            _enable_pgvector(_dsn(vec_sa_url))
        except Exception as e:
            print(
                "\nWARNING: Could not enable the 'vector' extension. "
                "Install pgvector on your Postgres server and try again."
            )
            print(f"Error was: {e}")
        db_url = vec_sa_url

    # Create the record-manager DB if needed
    if not rm_url:
        rm_db_name = input(f"Record Manager DB name [{default_rm_db}]: ").strip() or default_rm_db
        print(f"\nCreating record manager DB '{rm_db_name}' (if needed) and initializing schema…")
        _create_database(admin_dsn, rm_db_name)
        rm_sa_url = _sa_url(user, pwd, host, port, rm_db_name)
        try:
            _init_record_manager_schema(rm_sa_url, namespace=f"postgres/{collection_name}")
        except Exception as e:
            print("\nERROR: Failed to create Record Manager schema.")
            print(f"Error was: {e}")
            raise
        rm_url = rm_sa_url

    # Make URLs available to the current process (persist to .env yourself if desired)
    os.environ["DATABASE_URL"] = db_url
    os.environ["RECORD_MANAGER_DATABASE_URL"] = rm_url

    print("\n✅ Done.")
    print("Set these in your .env for future runs:")
    print(f"  DATABASE_URL={db_url}")
    print(f"  RECORD_MANAGER_DATABASE_URL={rm_url}\n")

    return db_url, rm_url
