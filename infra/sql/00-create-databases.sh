#!/bin/bash
# Runs once on first Postgres start, as the superuser. Creates the Langfuse database, every Saathi role and the
# Saathi database (LLD grants part, section 1). No migration creates a role: the migrator is not a superuser.
# The schema is applied later by `saathi-migrate` as saathi_migrator (every file in ./schema, by the compose `migrate` service before edge and worker start).
# tests/conftest.py runs the block between the BEGIN and END markers below, with fixed test words for the secrets.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE DATABASE langfuse;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
-- BEGIN roles
CREATE ROLE saathi_migrator LOGIN PASSWORD '${SAATHI_DB_PASSWORD_MIGRATOR}' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_edge     LOGIN PASSWORD '${SAATHI_DB_PASSWORD_EDGE}'     NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_core     LOGIN PASSWORD '${SAATHI_DB_PASSWORD_CORE}'     NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_model    LOGIN PASSWORD '${SAATHI_DB_PASSWORD_MODEL}'    NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_owner    LOGIN PASSWORD '${SAATHI_DB_PASSWORD_OWNER}'    NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_readonly LOGIN PASSWORD '${SAATHI_DB_PASSWORD_READONLY}' NOSUPERUSER NOCREATEROLE BYPASSRLS;
CREATE ROLE saathi_scan  NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS;   -- owns the scan functions
CREATE ROLE saathi_audit NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS;   -- owns audit_append (insert only)
GRANT saathi_scan, saathi_audit TO saathi_migrator WITH INHERIT FALSE, SET TRUE;   -- lets 004 hand over function ownership
CREATE DATABASE saathi OWNER saathi_migrator;
REVOKE ALL ON DATABASE saathi FROM PUBLIC;
GRANT CONNECT ON DATABASE saathi TO saathi_edge, saathi_core, saathi_model, saathi_owner, saathi_readonly;
-- END roles
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d saathi -c "CREATE EXTENSION IF NOT EXISTS vector;"
