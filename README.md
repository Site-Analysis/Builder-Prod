# Builder-Prod

Production repo for the Qnit Builders module. Features land one at a time via PRs; each feature is a complete clone-and-run package.

## Features

| Feature | Branch | Status |
|---------|--------|--------|
| Karnataka Cadastral Explorer | `Cadestral` | Complete (Phase 1A–1D) |

---

## Karnataka Cadastral Explorer — Setup

### What you need

| Item | Where to get it |
|------|----------------|
| Cadastral parquet data (`cadastral_lake_v2/` + `echawadi_village_list.json`) | Copy from Qnit internal drive |
| Keycloak account | `https://auth.builder.qnit.site` — realm `sat` |
| Supabase project | Create new at supabase.com |

### 1. Clone and install

```bash
git clone https://github.com/Site-Analysis/Builder-Prod
cd Builder-Prod
npm install
```

### 2. Supabase — create the projects table

Open your Supabase project → SQL editor → run:

```sql
-- contents of infra/supabase/builder_projects.sql
```

Or paste the file directly: `infra/supabase/builder_projects.sql`

### 3. Configure env vars

**Root `.env`** (for docker-compose):

```bash
cp .env.example .env
```

Fill in:
```
CADASTRAL_DATA_ROOT=/path/to/folder/containing/cadastral_lake_v2
SURVEY_INDEX_PATH=/path/to/writable/dir          # survey_index.db built here on first start
KEYCLOAK_URL=https://auth.builder.qnit.site
KEYCLOAK_REALM=sat
```

**Frontend `apps/web/.env.local`**:

```bash
cp apps/web/.env.example apps/web/.env.local
```

Fill in:
```
KEYCLOAK_URL=https://auth.builder.qnit.site
KEYCLOAK_REALM=sat
KEYCLOAK_CLIENT_ID=sat-web
AUTH_SECRET=<openssl rand -hex 32>
NEXTAUTH_URL=http://localhost:3000

NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

NEXT_PUBLIC_CADASTRAL_API_URL=http://localhost:8011
NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER=1
```

### 4. Start the backend

```bash
docker-compose up --build
```

Cadastral service starts at `http://localhost:8011`.  
`survey_index.db` builds in the background on first start (~15 min). Dropdowns and parcel load work immediately; search works once the index finishes.

### 5. Start the frontend

```bash
npm run dev          # http://localhost:3000
```

Log in with your Keycloak account → dashboard → open a project → map loads with cadastral toolbar.

### Local dev without Docker (faster iteration)

```powershell
# Terminal 1 — backend
cd services/cadastral
$env:FLAGS = "feature.cadastral.land-records"
$env:CADASTRAL_DATA_DIR = "C:\path\to\cadastral_lake_v2"
$env:SURVEY_INDEX_DB = "C:\path\to\survey_index.db"
$env:KEYCLOAK_URL = "https://auth.builder.qnit.site"
$env:KEYCLOAK_REALM = "sat"
$env:CORS_ORIGINS = '["http://localhost:3000"]'
$env:DEV_BYPASS_AUTH = "1"   # skip JWT validation for local testing
.venv\Scripts\uvicorn app.main:app --port 8011 --reload

# Terminal 2 — frontend
npm run dev
```

### Verify

```bash
curl http://localhost:8011/health
# → {"status":"ok","service":"cadastral"}

curl http://localhost:8011/districts
# → 403 without DEV_BYPASS_AUTH, or list of Karnataka districts with it
```

---

## 2031 planning layers — Setup

The map shows the 2031 master-plan zones of BDA (RMP 2031, draft), Hoskote and Anekal:
- a side panel with plan switches, opacity, legend and sources;
- an area / sub-area picker in the toolbar;
- a zone legend card;
- the plans that touch the parcel, on the parcel card.

**No plan data is stored in the repo or on disk.**

| Part | Where it comes from |
|---|---|
| Map zones | Pre-drawn raster tiles (PMTiles) in the public Supabase Storage bucket `planning-tiles`, read by the browser. Built once by `infra/scripts/planning/build_tiles.py` |
| Parcel answers (`/zones/at`, `/authority`) | The planning service (port 8012). It downloads the plan sheets listed in `infra/planning/layer_index.json` on demand, extracts them in a memory-capped worker, keeps the result in RAM, and deletes the download. See `infra/planning/LAYER_INDEX.md` |

### 1. Python environments

```bash
# planning service
cd services/planning && python3.12 -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows (macOS/Linux: .venv/bin/pip)

# sheet worker + build scripts
cd ../../infra/scripts/planning && python3.12 -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

### 2. Env vars (`apps/web/.env.local`)

```
NEXT_PUBLIC_PLANNING_API_URL=http://localhost:8012
NEXT_PUBLIC_ENABLE_PLANNING_LAYERS=1
NEXT_PUBLIC_PLANNING_PREBUILT_TILES=1
```

`NEXT_PUBLIC_SUPABASE_URL` must point to the Supabase project that holds the `planning-tiles` bucket. The tiles are
public, so the browser needs no key.

### 3. Run the planning service

```powershell
cd services/planning
$env:FLAGS = "feature.planning.layers feature.planning.coverage-layer feature.planning.plan.BDA-RMP2031 feature.planning.plan.BMRDA-HSK-MP2031 feature.planning.plan.BMRDA-ANK-MP2031"
$env:PLANNING_REGISTER_DIR = "..\..\infra\planning"
$env:PLANNING_WORKER_PYTHON = "..\..\infra\scripts\planning\.venv\Scripts\python.exe"
$env:CADASTRAL_URL = "http://127.0.0.1:8011"
$env:CORS_ORIGINS = '["http://localhost:3000"]'
$env:DEV_BYPASS_AUTH = "1"   # local only
.venv\Scripts\uvicorn app.main:app --port 8012
```

On Windows, `infra\scripts\dev\builders_all.ps1` starts the cadastral service, the planning service and the web app
with these settings. `-Stop` stops them and deletes the temporary logs.

With Docker, `docker-compose up --build` runs the planning service with the worker mounted from `infra/scripts/planning`.

- Use `127.0.0.1` for the services: `localhost` adds a 2 s IPv6 delay on some Windows machines.
- The web session is bound to `localhost:3000`.

### 4. Check

```bash
curl http://127.0.0.1:8012/health          # {"status":"ok","service":"planning"}
services/planning/.venv/Scripts/python -m pytest tests/planning_smoke.py
```

Open `http://localhost:3000/dashboard` and open a project:
- toolbar: **2031 plan area**, then **Sub-area**; the map flies there and draws the zones;
- or the right panel **2031 plan layers**: switching a plan on zooms to it.

### Re-drawing the map tiles (after a plan is re-indexed)

```bash
infra/scripts/planning/.venv/Scripts/python infra/scripts/planning/build_tiles.py \
  --plans BDA-RMP2031,BMRDA-HSK-MP2031,BMRDA-ANK-MP2031 \
  --raw-plans BDA-RMP2031,BMRDA-HSK-MP2031,BMRDA-ANK-MP2031
```

- It reads `SUPABASE_SERVICE_ROLE_KEY` from `apps/web/.env.local` (never printed or committed).
- It uploads one file per plan plus `manifest.json`; the map picks the new tiles up within a minute.
- It needs the planning service running (for `/plans`).
- Downloads go to the temp folder and are deleted afterwards.

---

## Development rules

- One feature per PR, targeting the feature branch (e.g. `Cadestral`)
- Each merged feature must be a complete clone-and-run package (env examples, docker-compose, setup SQL)
- No direct push to `Cadestral` or `main`
- CI: tsc + smoke tests must pass before merge
