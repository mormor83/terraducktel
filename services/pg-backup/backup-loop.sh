#!/usr/bin/env bash
# Backup loop: dump → compress → retain 7 days. Runs every BACKUP_INTERVAL.
#
# Why pg_dump and not pg_basebackup? pg_dump is logical and version-portable;
# pg_basebackup is physical and requires the same Postgres major version on
# restore. For a one-tenant TDT install that's fine, but pg_dump's restore
# story is "createdb && psql < dump.sql.gz" which any operator can run cold.
# Swap to basebackup when WAL archiving + PITR are needed.
#
# Required env (no defaults — fail fast in compose if not wired):
#   PG_HOST PG_PORT PG_USER PG_DB PG_PASSWORD BACKUP_DIR
# Optional:
#   BACKUP_INTERVAL_SECONDS (default: 21600 = 6h)
#   BACKUP_RETENTION_DAYS   (default: 7)

set -euo pipefail

: "${PG_HOST:?PG_HOST required}"
: "${PG_PORT:?PG_PORT required}"
: "${PG_USER:?PG_USER required}"
: "${PG_DB:?PG_DB required}"
: "${PG_PASSWORD:?PG_PASSWORD required}"
: "${BACKUP_DIR:?BACKUP_DIR required}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-21600}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

export PGPASSWORD="$PG_PASSWORD"

mkdir -p "$BACKUP_DIR"

log() {
  printf '{"ts":"%s","level":"INFO","logger":"pg-backup","msg":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" "$1"
}

trap 'log "SIGTERM received; exiting"; exit 0' TERM INT

run_backup() {
  local ts file
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  file="${BACKUP_DIR}/tdt-${ts}.sql.gz"
  log "starting backup → ${file}"
  if pg_dump --host="$PG_HOST" --port="$PG_PORT" --username="$PG_USER" \
             --dbname="$PG_DB" --no-owner --no-privileges --format=plain \
             | gzip -9 > "${file}.tmp"; then
    mv "${file}.tmp" "$file"
    log "completed: $(stat -c %s "$file" 2>/dev/null || wc -c < "$file") bytes"
  else
    log "pg_dump FAILED — keeping previous backups; will retry next cycle"
    rm -f "${file}.tmp" || true
    return 1
  fi
  # Retention sweep — keep the most-recent N days of full dumps.
  find "$BACKUP_DIR" -maxdepth 1 -name 'tdt-*.sql.gz' \
       -type f -mtime "+${RETENTION_DAYS}" -delete -print 2>/dev/null | \
    while read -r path; do log "retention: removed ${path}"; done || true
}

log "starting; interval=${INTERVAL}s retention=${RETENTION_DAYS}d dir=${BACKUP_DIR}"

# Wait for postgres readiness before the first backup so a slow boot doesn't
# spam the error log.
for i in $(seq 1 30); do
  if pg_isready --host="$PG_HOST" --port="$PG_PORT" --username="$PG_USER" --quiet; then
    break
  fi
  sleep 2
done

while true; do
  run_backup || true
  sleep "$INTERVAL"
done
