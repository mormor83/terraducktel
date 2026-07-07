#!/usr/bin/env bash
# Privileged setup → drop privileges → run the backup loop.
set -euo pipefail

# Volume might be empty / owned by root on first mount. Make it writable by
# the unprivileged postgres user (UID 70 in the postgres:alpine image).
mkdir -p "${BACKUP_DIR:-/backups}"
chown -R postgres:postgres "${BACKUP_DIR:-/backups}"

# Drop to postgres. The postgres:alpine image ships gosu via su-exec.
exec su-exec postgres /usr/local/bin/backup-loop.sh
