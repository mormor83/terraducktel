# Developer onboarding

Target time: under 30 minutes on a machine with Docker and Git.

## Steps

1. **Clone** this repository and `cd` into the project root.
2. **Environment**: copy `.env.example` to `.env` and set secrets (Postgres password, JWT secret, encryption key).
3. **Start**: run `docker compose up -d --wait` (or `make up` if available).
4. **Database + dev users** (with the API container running):
   ```bash
   make seed-db
   # or: bash scripts/seed-dev-users.sh
   ```
   This runs `alembic upgrade head` and creates (if missing):

   | Email | Password | Role |
   |-------|----------|------|
   | admin@test.com | password123 | admin |
   | operator@test.com | password123 | operator |
   | viewer@test.com | password123 | viewer |

5. **API**: open `http://localhost:8001/docs` (or your mapped API port) and use **POST /api/v1/auth/token** with the email/password above.
6. **UI**: production bundle is proxied through nginx — `http://localhost:3001` maps `/api` → API. Run `make seed-db` first, then sign in with `admin@test.com` / `password123`. For local dev: `cd services/ui && npm run dev` (Vite proxies `/api` to the API port, default **8001**).

## Running against an external Postgres + S3 store

For a deployment where the database and the Terraform-state bucket live on
their own hosts (e.g. Postgres + Garage or MinIO), layer
`deploy/docker-compose.external-db.yml` over the base file and point `.env`
at them:

```bash
COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.external-db.yml
DATABASE_URL=postgresql+asyncpg://terraducktel:<pw>@<pg-host>:5432/terraducktel?ssl=require
POSTGRES_PASSWORD=<pw>          # pg-backup
PG_HOST=<pg-host>
S3_USE_LOCALSTACK=false
S3_ENDPOINT_URL=https://<s3-host>:3900
```

Use TLS for both. `?ssl=require` is asyncpg's spelling (alembic's migration
URL is translated to `sslmode=require` automatically); a plaintext `http://`
`S3_ENDPOINT_URL` to a non-local host works but logs a WARNING, since state
can contain secrets.
To refuse such an endpoint instead (state reads/writes return 503), a
superadmin can turn on **Require TLS** in **Settings → State store**
(config key `state_store.s3.require_tls`).

The S3 store's key pair is **not** an env var: once the API is up, sign in as
a superadmin and set it in **Settings → State store** (stored encrypted in
the `config` table; the page only ever shows a masked tail). Until it is set,
non-AWS workspaces use boto3's default credential chain for the fallback
bucket.

The override switches off the bundled `postgres`, `localstack`, `forgejo`
and `act_runner` services. Needs Compose ≥ 2.24.

## Scripts

- `scripts/onboard.sh` — bootstrap helper (adjust for your compose layout).
- `scripts/load-test.sh` — optional API load smoke (requires `curl`, `jq`, running API).
- `scripts/verify-onboarding-time.sh` — wraps `onboard.sh` and checks elapsed time.

## Tests

- API: `cd services/api && .venv/bin/python -m pytest tests/ -v`
- UI E2E: `cd services/ui && npm run test:e2e`
