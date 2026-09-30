# Contracts Changelog

Monotonic version across all services. Each entry: version, date, service, summary.

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

## 1.4.0 — 2026-09-10 — cadastral

*Reconstructed from git history (f9710f3); no entry was written at the time.*

**`GET /data` geometry datum changed:** `CADASTRAL_DATUM` env var and the Kalianpur 1975 shift
(added in 9e138c2) removed. Parcels are now raw EPSG:32643 after the `[0,1,1,0,0,0]` X/Y swap,
reprojected to EPSG:4326. No path or schema changes; coordinates move by ~60–100 m versus 1.2.0.

---

## 1.3.0 — 2026-09-10 — cadastral

*Reconstructed from git history (f9710f3); no entry was written at the time.*

**New endpoint `GET /nearby`:** LGD villages whose centroid is within `radius_km` of a WGS84
point, as a GeoJSON FeatureCollection. Properties: `lgd_code`, `village_name`, `has_data`.
Extended in 1.5.0.

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

---

## Known contract drift

Not fixed yet; listed so it isn't lost.

- `contracts/cadastral.yaml` `info.version` is still `"1.0.0"`; the changelog is at 1.7.0.
- `contracts/cadastral.yaml` `/nearby` says geometry comes from `lgd_villages.parquet`; since
  802d36c the service reads an LGD SQLite index (`LGD_INDEX_DB`) first, parquet only as fallback.
