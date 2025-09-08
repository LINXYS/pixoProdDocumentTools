# pg_bootstrap.py
from __future__ import annotations

import os
from getpass import getpass
from typing import Tuple
from urllib.parse import quote_plus

import psycopg
from psycopg import sql

# LangChain indexing (SQL Record Manager)
from langchain.indexes import SQLRecordManager

# Always prefer UTF-8 on the client side for this process
os.environ.setdefault("PGCLIENTENCODING", "utf8")


# --- Small URL helpers --------------------------------------------------------

def _sa_url(user: str, pwd: str, host: str, port: int, db: str) -> str:
    """SQLAlchemy-style URL for LangChain + psycopg3 driver."""
    return f"postgresql+psycopg://{user}:{quote_plus(pwd)}@{host}:{port}/{db}"

def _dsn(sa_url: str) -> str:
    """psycopg DSN from SQLAlchemy URL (drop '+psycopg')."""
    return sa_url.replace("postgresql+psycopg", "postgresql").replace("postgresql+psycopg2", "postgresql")


# --- Encoding helpers ----------------------------------------------------------

def _get_db_server_encoding(admin_dsn: str, db_name: str) -> str | None:
    """Return server-side database encoding for db_name (e.g., 'UTF8')."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname=%s", (db_name,))
            row = cur.fetchone()
            return row[0] if row else None

def _ensure_db_client_default_utf8(admin_dsn: str, db_name: str) -> None:
    """
    Set default client_encoding for the database so all future sessions default to UTF8,
    even if the client doesn't explicitly request it.
    """
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("ALTER DATABASE {} SET client_encoding TO 'UTF8'").format(sql.Identifier(db_name)))


# --- Postgres operations -------------------------------------------------------

def _create_database_utf8(admin_dsn: str, db_name: str) -> None:
    """
    CREATE DATABASE in UTF-8 if it doesn't exist.
    Uses TEMPLATE template0 to guarantee clean UTF-8 regardless of template1 settings.
    """
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,))
            exists = cur.fetchone() is not None
            if not exists:
                # Note: On Postgres 15+/ICU you could also specify LOCALE_PROVIDER/ICU collations.
                cur.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'")
                    .format(sql.Identifier(db_name))
                )

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

    Always prefers UTF-8 databases and sets DB-level default client_encoding to UTF-8.

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
    pwd = getpass("Password: ")
    admin_sa_url = _sa_url(user, pwd, host, port, "postgres")
    admin_dsn = _dsn(admin_sa_url)

    # Create / verify the vector DB
    if not db_url:
        vec_db_name = input(f"Vector DB name [{default_vec_db}]: ").strip() or default_vec_db

        # If a DB exists but isn't UTF-8, offer to create a new *_utf8 DB.
        enc = _get_db_server_encoding(admin_dsn, vec_db_name)
        if enc and enc.upper() != "UTF8":
            print(f"\nWARNING: Database '{vec_db_name}' exists with encoding '{enc}', not 'UTF8'.")
            yn2 = input(f"Create a new UTF-8 database '{vec_db_name}_utf8' instead? [Y/n]: ").strip().lower() or "y"
            if yn2 in ("y", "yes"):
                vec_db_name = f"{vec_db_name}_utf8"
                enc = None  # we'll create it fresh

        print(f"\nEnsuring vector DB '{vec_db_name}' exists in UTF-8 and enabling pgvector…")
        _create_database_utf8(admin_dsn, vec_db_name)
        vec_sa_url = _sa_url(user, pwd, host, port, vec_db_name)
        try:
            _enable_pgvector(_dsn(vec_sa_url))
        except Exception as e:
            print("\nWARNING: Could not enable the 'vector' extension. Install pgvector on your Postgres server and try again.")
            print(f"Error was: {e}")

        # Make DB default to UTF-8 for all clients
        _ensure_db_client_default_utf8(admin_dsn, vec_db_name)

        db_url = vec_sa_url

    # Create / verify the record-manager DB
    if not rm_url:
        rm_db_name = input(f"Record Manager DB name [{default_rm_db}]: ").strip() or default_rm_db

        enc = _get_db_server_encoding(admin_dsn, rm_db_name)
        if enc and enc.upper() != "UTF8":
            print(f"\nWARNING: Database '{rm_db_name}' exists with encoding '{enc}', not 'UTF8'.")
            yn3 = input(f"Create a new UTF-8 database '{rm_db_name}_utf8' instead? [Y/n]: ").strip().lower() or "y"
            if yn3 in ("y", "yes"):
                rm_db_name = f"{rm_db_name}_utf8"
                enc = None

        print(f"\nEnsuring record manager DB '{rm_db_name}' exists in UTF-8 and initializing schema…")
        _create_database_utf8(admin_dsn, rm_db_name)
        rm_sa_url = _sa_url(user, pwd, host, port, rm_db_name)

        # Make DB default to UTF-8 for all clients
        _ensure_db_client_default_utf8(admin_dsn, rm_db_name)

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

    # Quick sanity output
    try:
        vec_dbname = db_url.rsplit("/", 1)[-1]
        rm_dbname = rm_url.rsplit("/", 1)[-1]
        venc = _get_db_server_encoding(admin_dsn, vec_dbname)
        renc = _get_db_server_encoding(admin_dsn, rm_dbname)
        print(f"Vector DB '{vec_dbname}' server encoding: {venc}")
        print(f"Record Manager DB '{rm_dbname}' server encoding: {renc}")
        print("Both databases also have default client_encoding set to UTF8.\n")
    except Exception:
        pass

    return db_url, rm_url
