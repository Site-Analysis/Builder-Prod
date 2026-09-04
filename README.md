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

## Development rules

- One feature per PR, targeting the feature branch (e.g. `Cadestral`)
- Each merged feature must be a complete clone-and-run package (env examples, docker-compose, setup SQL)
- No direct push to `Cadestral` or `main`
- CI: tsc + smoke tests must pass before merge
