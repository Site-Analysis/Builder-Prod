# Contracts Changelog

Monotonic version across all services. Each entry: version, date, service, summary.

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
