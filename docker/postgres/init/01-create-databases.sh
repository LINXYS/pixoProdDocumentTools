#!/usr/bin/env bash
set -euo pipefail

VECTOR_DB="${VECTOR_DB:-vectorstore}"
RECORD_MANAGER_DB="${RECORD_MANAGER_DB:-record_manager}"

validate_db_name() {
  case "$1" in
    *[!a-zA-Z0-9_-]* | "")
      echo "Invalid database name: $1" >&2
      exit 1
      ;;
  esac
}

create_database() {
  local db_name="$1"
  validate_db_name "$db_name"

  if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${db_name}'" | grep -q 1; then
    echo "Database already exists: ${db_name}"
  else
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "CREATE DATABASE \"${db_name}\" TEMPLATE template0 ENCODING 'UTF8';"
  fi

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "ALTER DATABASE \"${db_name}\" SET client_encoding TO 'UTF8';"
}

create_database "$VECTOR_DB"
create_database "$RECORD_MANAGER_DB"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$VECTOR_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;"
