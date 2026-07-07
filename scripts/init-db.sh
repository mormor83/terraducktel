#!/bin/bash
# init-db.sh — Creates the forgejo database on first boot
# Runs as part of postgres container initialization (docker-entrypoint-initdb.d)
set -e

echo "==> init-db.sh: creating forgejo database"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE forgejo OWNER $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE forgejo TO $POSTGRES_USER;
EOSQL
echo "==> init-db.sh: forgejo database created"
