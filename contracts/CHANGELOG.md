# Contracts Changelog

Monotonic version across all services. Each entry: version, date, service, summary.

---

## 1.1.0 — 2026-09-04 — cadastral

Backend switched to CockroachDB asyncpg pool. All hierarchy and parcel endpoints
now served from DB (not filesystem parquets). Contract shape unchanged — same 7
endpoints, same request/response schemas. Service version bumped to 1.1.0.

---

## 1.0.0 — 2026-09-04 — cadastral

Initial cadastral service contract.

Endpoints: `/health`, `/search`, `/districts`, `/taluks`, `/hoblis`, `/villages`, `/data`.

Feature flag: `feature.cadastral.land-records`.
