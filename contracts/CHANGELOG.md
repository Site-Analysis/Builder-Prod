# Contracts Changelog

Monotonic version across all services. Each entry: version, date, service, summary.

---

## 1.20.0 — 2026-10-04 — cadastral (auth hardening)

Numbered 1.20.0 because `feat/planning-2031-phase0` already uses 1.8.0–1.19.0 (planning).

**Security scheme (documented, now enforced):** every route except `/health` needs
`Authorization: Bearer <Keycloak access token>` (`components.securitySchemes.keycloakBearer`,
global `security`; `/health` has `security: []`). A token passes only if the RS256 signature
matches the realm JWKS, `iss` = KEYCLOAK_ISSUER, `exp` is in the future, `typ` = `Bearer` and
`azp` = KEYCLOAK_CLIENT_ID. Missing, expired, wrong-issuer, wrong-client and ID tokens get
**401** `{"detail": ...}` (before: issuer and client were not checked, so any token signed by
the realm, including another client's or an ID token, was accepted).

**Docs:** `/docs`, `/redoc` and `/openapi.json` are served only with `ENABLE_API_DOCS=1`
(before: always public). Off they return 404.

**Start-up (fail closed):** the service refuses to start if KEYCLOAK_URL, KEYCLOAK_REALM or
KEYCLOAK_CLIENT_ID is missing, or if DEV_BYPASS_AUTH is set with APP_ENV other than `local`.
New optional env: KEYCLOAK_ISSUER, KEYCLOAK_JWKS_URL (internal JWKS fetch).

**Frontend (no contract change):** confidential Keycloak client (KEYCLOAK_CLIENT_SECRET);
logout is `POST /api/auth/logout` → 303 to Keycloak end-session with `client_id`,
`post_logout_redirect_uri` (AUTH_URL) and `id_token_hint`; a 401 from a data call starts
sign-in (no session) or shows "access denied" (session present), never sign-out; web server
refuses to start on missing auth env or a bypass outside APP_ENV=local.

---

## 1.7.0 — 2026-09-15 — cadastral

**New endpoint `GET /rtc`:** Live RCCMS (Records of Rights) + mutations proxy for a survey parcel.
Calls eChhawadi `GetActiveRCCMS` (types P + D) and `GetActiveCasesofMutationStatus` for the
village, then filters by `survey_no` base. Village-level cache TTL 300s. Returns
`{owners: [{survey_no, owner_name, case_status, ack_no}], mutations: [{mr_number, transaction_type, survey_numbers, status, applicant}]}`.

**Frontend:**
- Parcel click card now shows live RCCMS ownership data
- Card shows "Loading ownership…" while fetching, then owner name(s) + case status
- Mutations section appears if any pending transactions found
- `loadedVillage` state in MapView tracks current village context for RTC calls

---

## 1.6.0 — 2026-09-10 — cadastral

**New endpoint `GET /village-search?q=<prefix>`:** In-memory prefix search across all villages
with cadastral parquet data. Returns up to 20 results (village_name, dist, taluk, hobli, vlg,
dist_name, taluk_name). Index built at startup in ~5-10s. Replaces Nominatim in frontend
location search — results are guaranteed accurate (only villages with real parquet data).

**Frontend:**
- Location search replaced: Nominatim removed, uses `/village-search` instead
- Results show green "cadastral" badge + district · taluk subtitle
- Click result → map flies to village boundary + dropdowns pre-fill; parcels load on explicit Load click only
- Removed "no data" card from map UI

---

## 1.5.0 — 2026-09-10 — cadastral

**`GET /nearby` response extended:** Feature properties now include `dist`, `taluk`, `hobli`,
`vlg` (e-Chawadi string codes) alongside existing `lgd_code`, `village_name`, `has_data`.
These codes are `null` when the LGD village has no e-Chawadi mapping. Frontend uses these
to auto-populate the hierarchy dropdowns and load parcels after a coordinate search.

**New frontend features:**
- Nearby toggle: LGD village boundaries rendered as green/red polygons (has_data flag)
- Load on explicit click only — no auto-load on dropdown selection or location result click

---

## 1.2.0 — 2026-09-06 — cadastral

`GET /boundaries` now returns ALL LGD villages in the hobli (from echawadi_village_list.json),
not only those with cadastral parquet data. Each feature gains a `has_data: boolean` property.
Villages without parquet data are returned with `geometry: null` and `has_data: false` — frontend
uses this to render a red "No data" chip list alongside the green polygon overlays.

---

## 1.1.0 — 2026-09-06 — cadastral

Added village boundary overlay endpoints.

New endpoints:
- `GET /boundary?dist=&taluk=&hobli=&vlg=` — single village boundary polygon (union of parcels)
- `GET /boundaries?dist=&taluk=&hobli=` — all village boundary polygons in a hobli

---

## 1.0.0 — 2026-09-04 — cadastral

Initial cadastral service contract.

Endpoints: `/health`, `/search`, `/districts`, `/taluks`, `/hoblis`, `/villages`, `/data`.

Feature flag: `feature.cadastral.land-records`.
