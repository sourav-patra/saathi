#!/bin/bash
# Runs once on first Postgres start: separate DBs for Langfuse and Saathi.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE DATABASE langfuse;
CREATE ROLE saathi LOGIN PASSWORD '${SAATHI_DB_PASSWORD}';
CREATE DATABASE saathi OWNER saathi;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d saathi -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -v ON_ERROR_STOP=1 --username saathi -d saathi -f /docker-entrypoint-initdb.d/schema/001_init.sql
