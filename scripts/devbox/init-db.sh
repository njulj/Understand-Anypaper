#!/usr/bin/env bash
set -euo pipefail

PGPORT="${PGPORT:-5432}"
DB_NAME="understand_anypaper"
DB_USER="understand"
DB_PASSWORD="understand"

psql -h 127.0.0.1 -p "$PGPORT" -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER WITH LOGIN SUPERUSER PASSWORD '$DB_PASSWORD';
  ELSE
    ALTER ROLE $DB_USER WITH LOGIN SUPERUSER PASSWORD '$DB_PASSWORD';
    ALTER ROLE $DB_USER WITH SUPERUSER;
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$DB_NAME')\\gexec
SQL

PGPASSWORD="$DB_PASSWORD" psql \
  -h 127.0.0.1 \
  -p "$PGPORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -f apps/server/sql/schema.sql
