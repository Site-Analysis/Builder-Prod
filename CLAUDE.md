# CLAUDE.md — Builder-Prod

Guidance for Claude Code in this repo.

## Repo Purpose

Standalone production repo for the Qnit Builders module. Features land one at a time via PRs. Each merged feature is a complete clone-and-run package (env examples, docker-compose, setup SQL, README steps).

## Layout

```
apps/web/                  Next.js 16 + React 19 frontend (port 3000)
services/cadastral/        FastAPI cadastral service (port 8011)
contracts/                 OpenAPI YAML — one per service + CHANGELOG.md
infra/supabase/            Supabase SQL setup scripts
tests/                     Smoke tests (one file per service, run per-process)
docker-compose.yml         Local dev: cadastral backend only; web runs outside
.env.example               Root env (docker-compose vars)
apps/web/.env.example      Frontend env
```

## Current Features

| Feature | Branch | Status | Phases |
|---------|--------|--------|--------|
| Karnataka Cadastral Explorer | `Cadestral` | Complete | 1A (scaffold), 1B (backend), 1D (search + click) |

Phase 1C (CockroachDB) was cancelled — filesystem + SQLite approach used instead.

---

## Cadastral Feature — Full Context

### Architecture

No external database for parcel data. Pure filesystem:

| Data source | Path (inside container) | Env var |
|-------------|------------------------|---------|
| Parquet tree | `/data/cadastral_lake_v2/dist_*/taluk_*/hobli_*/vlg_*.parquet` | `CADASTRAL_DATA_DIR` |
| Name lookup | `/data/echawadi_village_list.json` (one level above `CADASTRAL_DATA_DIR`) | derived |
| Survey index | `/survey_index/survey_index.db` | `SURVEY_INDEX_DB` |

`survey_index.db` — built automatically in a background daemon thread on first service start. Takes ~15 min for ~35K parquets. Subsequent starts: skips if already populated. Must be on a **persistent, writable mount** (not a temp dir) or it rebuilds every restart.

`karnataka_lands_full.db` — NOT used in Phase 1A–1D. Needed later for RCCMS/mutations overlays.

### Parquet quirk (critical)

Every parquet stores `Polygon(Northing, Easting)` instead of `Polygon(Easting, Northing)` — upstream scraper bug. `load_village()` in `cadastral_service.py` fixes this with `affine_transform([0,1,1,0,0,0])` before reprojecting to WGS84. Never read parquets directly without this fix.

**Datum**: Source data is in Kalianpur 1975 datum (Everest ellipsoid + 3-param Bursa-Wolf shift: towgs84=295,736,257). Set `CADASTRAL_DATUM=kalianpur` (confirmed via visual alignment test — parcels match satellite boundaries). Default in docker-compose and `.env.example`. Do NOT use `wgs84` (shifts parcels ~60 m S / ~108 m E).

### Request flow

1. `/districts` → scans `dist_*/` dirs → names from echawadi JSON
2. `/taluks?dist=1` → scans `dist_1/taluk_*/`
3. `/hoblis?dist=1&taluk=9` → scans `dist_1/taluk_9/hobli_*/`
4. `/villages?dist=1&taluk=9&hobli=3` → scans `dist_1/taluk_9/hobli_3/`
5. `/data?dist=1&taluk=9&hobli=3&vlg=46` → reads `vlg_46.parquet` → X/Y fix → reproject → GeoJSON
6. `/search?q=123` → queries `survey_index.db` LIKE `123%` → returns `(survey_no, village_name, dist, taluk, hobli, vlg)` list

### Auth

- **Frontend**: `next-auth` v5 with Keycloak OIDC provider (authorization code + PKCE). Session exposes `session.accessToken` (Keycloak JWT).
- **Backend**: FastAPI `verify_token` fetches JWKS from Keycloak, validates RS256 JWT.
- **Local dev**: set `DEV_BYPASS_AUTH=1` on the backend to skip JWT validation entirely.
- Keycloak: `https://auth.builder.qnit.site`, realm `sat`, client `sat-web` (public, no secret, PKCE).

### Project storage (Supabase)

Supabase is used **only** for `builder_projects` table — NOT for auth, NOT for parcel data.
- Table setup SQL: `infra/supabase/builder_projects.sql`
- Access via service role key only (API routes in `apps/web/app/api/projects/`)
- New Supabase project per deployment (separate from SAT Supabase).

---

## Setup Steps (new machine)

When helping a user set up the cadastral feature from scratch:

### 1. Data files

User must copy two things to their machine:
- `cadastral_lake_v2/` directory (the parquet tree, ~1.7 GB)
- `echawadi_village_list.json` (must be in the **parent** of `cadastral_lake_v2/`, not inside it)

Example layout on Windows:
```
C:\Users\tanny\Downloads\
  echawadi_village_list.json
  cadastral_lake_v2\
    dist_1\
      taluk_9\
        hobli_3\
          vlg_46.parquet
```

### 2. Supabase

1. Create new Supabase project at supabase.com
2. SQL editor → run `infra/supabase/builder_projects.sql`
3. Get: Project URL, anon/publishable key, service role key

### 3. Root `.env` (for docker-compose)

```
CADASTRAL_DATA_ROOT=C:\Users\tanny\Downloads   # parent dir containing cadastral_lake_v2 + echawadi JSON
SURVEY_INDEX_PATH=C:\Users\tanny\Downloads\survey_index
KEYCLOAK_URL=https://auth.builder.qnit.site
KEYCLOAK_REALM=sat
```

### 4. `apps/web/.env.local`

```
KEYCLOAK_URL=https://auth.builder.qnit.site
KEYCLOAK_REALM=sat
KEYCLOAK_CLIENT_ID=sat-web
AUTH_SECRET=<32 random chars — run: openssl rand -hex 32>
NEXTAUTH_URL=http://localhost:3000

NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

NEXT_PUBLIC_CADASTRAL_API_URL=http://localhost:8011
NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER=1
```

### 5. Start

```bash
# Install JS deps
npm install

# Backend (Docker)
docker-compose up --build

# Frontend (separate terminal)
npm run dev
```

Verify: `curl http://localhost:8011/health` → `{"status":"ok","service":"cadastral"}`

### Local dev without Docker (faster)

```powershell
# One-time venv setup
cd services/cadastral
python3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Run backend
$env:FLAGS = "feature.cadastral.land-records"
$env:CADASTRAL_DATA_DIR = "C:\Users\tanny\Downloads\cadastral_lake_v2"
$env:SURVEY_INDEX_DB = "C:\Users\tanny\Downloads\survey_index\survey_index.db"
$env:KEYCLOAK_URL = "https://auth.builder.qnit.site"
$env:KEYCLOAK_REALM = "sat"
$env:CORS_ORIGINS = '["http://localhost:3000"]'
$env:DEV_BYPASS_AUTH = "1"
.venv\Scripts\uvicorn app.main:app --port 8011 --reload
```

---

## Dev Workflow

### Frontend
```bash
cd apps/web
npm run dev       # http://localhost:3000
```

TypeScript check (CI uses this, no eslint):
```bash
../../node_modules/.bin/tsc --noEmit
```

### Backend (per service)
```bash
cd services/<service>
python3.12 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port <port>
```

### Smoke tests
```bash
# From repo root — run each file in its own process (app package collision)
pytest tests/cadastral_smoke.py
```

### Lint
```bash
ruff check services/
ruff format --check services/
```

---

## Rules (Non-Negotiable)

1. **Contract-first.** Update `contracts/<service>.yaml` + `contracts/CHANGELOG.md` before writing service code.
2. **Feature flag.** Every new behavior behind `FLAGS=feature.<service>.<name>` env var, checked inline per service (no shared package — outside Docker build context).
3. **One feature per PR** targeting the feature branch.
4. **Clone-and-run package on final push.** Every feature's last PR must include: env examples, docker-compose updates, setup SQL if needed, README updated.
5. **No secrets in committed files.** `.env`, `.env.local`, `*.local.*` are gitignored.
6. **Smoke test per service.** Each service ships a `tests/<service>_smoke.py`. Run per-file — never all together (app package name collision).

## Gotchas

- **`app` name collision in tests.** Every service's package is `app`. Each smoke file must `sys.path.insert` its own service dir + `sys.modules.pop("app")` before importing.
- **`survey_index.db` on temp dir.** If `SURVEY_INDEX_DB` points to a temp dir, the index rebuilds every restart (~15 min). Use a persistent bind mount.
- **CORS_ORIGINS must be valid JSON.** `CORS_ORIGINS=["http://localhost:3000"]` — pydantic-settings parses it as a JSON list. Comma-separated strings will fail.
- **echawadi JSON location.** Must be one directory **above** `CADASTRAL_DATA_DIR`. The service derives its path as `os.path.dirname(DATA_DIR) + "/echawadi_village_list.json"`. If it's missing, district/taluk/hobli/village names show as numeric codes (still works, just ugly).
- **DEV_BYPASS_AUTH in production.** Never set `DEV_BYPASS_AUTH=1` in production. It disables all JWT validation.
- **Next.js 16 breaking changes.** No `next lint` subcommand — treat `lint` as a directory arg. CI uses `tsc --noEmit` only (no eslint).
