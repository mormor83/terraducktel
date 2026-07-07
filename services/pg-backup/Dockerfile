FROM postgres:16-alpine

# pg_dump version must match the source server to avoid version warnings.
# `postgres:16-alpine` ships pg_dump 16. We add tini for proper signal
# forwarding so SIGTERM stops the backup loop cleanly.
RUN apk add --no-cache tini ca-certificates

# Entrypoint starts as root just long enough to chown the bind-mounted backup
# directory (Docker creates volume mounts as root by default), then `exec
# gosu postgres` drops to the unprivileged postgres user (UID 70) for the
# actual loop.
COPY --chmod=0755 backup-loop.sh /usr/local/bin/backup-loop.sh
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
