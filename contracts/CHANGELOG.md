# Contracts Changelog

Monotonic version across all services. Each entry: version, date, service, summary.

---

## 1.21.0 — 2026-10-04 — planning

Additive.
- `AbuttingRoad.status` adds `existing_drawn` (an existing road the plan draws without a width
  label; `row_m` is its width as drawn), `width_source` adds `drawn_band`, new
  `drawn_band_m` (the existing road's grey band measured on the plan, to scale); status
  `plan_row_stated` for roads whose plan states a ROW without saying existing or proposed.
  Road layers: Anekal (Mobility Plan), Hoskote (Master Plan atlas), BDA (RMP 2031 composite).
- `ExistingWidthEstimate.method` adds `plan_drawn`: for roads to be widened, existing roads
  with a stated ROW and drawn-only roads, the existing width comes from the plan's grey band
  (MEDIUM) before the cadastral gap. Roads files are published gzip (`.geojson.gz`).

---

## 1.20.0 — 2026-10-04 — planning

Additive.
- `/zones/at`: new `abutting_roads` (AbuttingRoad[], null when `feature.planning.roads` is
  off) and optional query `road_width_m`. Each road: plan ROW and status (to be widened,
  proposed, existing road whose ROW the plan states, ring / radial), where the width comes
  from (label, drawn ROW edges, legend) and its confidence, distance and frontage, the parcel
  area inside the ROW corridor (`widening_area_sqm`), an existing-width estimate from the
  cadastral gap across the road (`existing_width`, MEDIUM / LOW), the width used (declared,
  else estimate), its Zonal Regulations band and the ZR rows for that band (Table 4 FAR by
  use, Table 6 group housing, Table 10A IT, with pages), and warnings (plan ROW wider than the
  existing width, proposed road, declared width far from the estimate).
- Roads come from pre-drawn plan road layers (first: Anekal Mobility Plan,
  `BMRDA-ANK-MP2031-MOB-10K`, `reference`), read by the service from the published manifest
  (`PLANNING_ROADS_SOURCE`) into memory.

---

## 1.19.0 — 2026-10-03 — planning

Additive.
- `/zones` and `/overlays`: bbox up to 0.25 degrees per side when `simplify_m` is 25 (map
  zoom 10-12), so plan layers show from zoom 10 (L2); 0.05 degrees otherwise, unchanged.
  Display only: `/zones` leaves out pieces under 100 m2 at `simplify_m` 8 and under 0.25 ha at
  25 (a Hoskote town box went from 37,865 features / 45 MB, 95 % specks, to the visible ones);
  `/zones/at` answers are unchanged.
- New `GET /coverage?bbox=` (CoverageFeatureCollection): villages of Bengaluru Urban and
  Rural coloured by the village table's `plan_coverage`, for the side panel's "Coverage status"
  layer. Outlines are the cadastral service's parcel unions, in the service's memory only;
  `state` is loading / ready / unavailable. Gated by `feature.planning.layers` and
  `feature.planning.coverage-layer`.
- Behaviour within 1.18 (no schema change): a lat/lng `/authority` answer's `village_summary`
  is null until the village outlines have loaded (they start on the first point query); zone
  `qa` (SheetQA) now always carries `placement_confirmed` and `position_uncertainty_m`; "Not
  coloured on the plan" hits report `source_layer` null for every plan (F6).
- Data: Magadi (`MAGADI-MP2031`, draft: no approval GO published) and Kanakapura
  (`KPA-MP2031`, final, UDD 153 BMR 2013 of 07-08-2015) are registered, not loaded (town maps
  without coordinates); their villages are `plan_registered_not_loaded`. STRR and
  Doddaballapura are `authority_no_master_plan`.

---

## 1.18.0 — 2026-10-02 — planning

Additive (new enum values, new optional fields); one value's meaning narrowed (below).
- `PlanCoverage`: new value `authority_no_master_plan` (authority known, the sources checked show
  no master plan, e.g. STRR). `lpa_no_zone_map` now means only "the plan exists but has no zone
  map here" (Madhure; a loaded plan leaving the place uncoloured, e.g. the STRR band in Hoskote).
  `no_master_plan_found` is never used for a plan that exists but is not registered: Magadi and
  Kanakapura are registered instead. Order: plan_loaded, plan_registered_not_loaded,
  lpa_no_zone_map, authority_no_master_plan, no_master_plan_found.
- F9 rule: a point outside every LPA but within 100 m of one is `plan_loaded` only when a loaded
  zone covers the point; otherwise `no_master_plan_found`, with the near-edge LPA in
  `authorities` as partial with the distance note.
- F1-F5: point and parcel answers come from geometry; village rows are summaries.
  `AuthorityResult` (lat/lng) and `ZonesAtResult` gain `village_summary` (VillageSummary) and
  `disagreement_note`, e.g. "Most of village X is in Y; this location is in Z".
- `build_id` (BuildId) on AuthorityResult, ZoneFeatureCollection, OverlayFeatureCollection and
  ZonesAtResult: the layer-index version behind the answer.
- `pending_sheets[]` (PendingSheet: sheet, plan_id, doc_id, state downloading / extracting /
  source_changed / failed, source, message, retry_after_s) on ZoneFeatureCollection,
  OverlayFeatureCollection and ZonesAtResult. Zones are extracted on demand from the layer index;
  a sheet whose download no longer matches the indexed sha256 is not served and shows
  "Source changed; needs re-indexing".
- `OverlayKind`: new kinds `proposed_road` (note always "Proposed road (plan), shown as a warning,
  not an input"), `water_body`, `metro_rail`, `drawn_buffer`. `OverlaysNearby.others[]` lists
  them for a parcel.
- `Plan.extent` (Bbox) and `Plan.sheets[]` (SheetState: state ready / not_loaded / downloading /
  extracting / source_changed / failed, placement_confirmed, extent, source_url) for the layer
  side panel and "Zoom to plan".
- `SheetQA.placement_confirmed` (default true) and `SheetQA.position_uncertainty_m`. Sheets
  indexed without an independent placement check (Nelamangala A3, B1, C3, D1) carry false, the
  warning "Placement not confirmed by an independent check; zones may be 100 m or more off.
  Verify on site." and uncertainty max(100 m, measured RMSE).
- `simplify_m` wording: geometry is simplified when a sheet is extracted, not at startup.

---

## 1.17.0 — 2026-10-02 — planning

Additive.
- `SheetQA.warnings` (string[]): sheet-level cautions for every zone from that sheet; empty when
  none (layers built before 1.17 return []). Anekal LPA Master Plan 2031: every sheet whose legend
  lists a hatched class carries "This sheet has hatched classes (public utility,
  hillocks/quarries; 324 ha across the LPA per the plan) that are not extracted. The zone shown
  here may be one of them." Hatched areas take the neighbouring zone in the extraction (not
  white), so "Not coloured on the plan" is unchanged.
- `/authority` near-edge note (approved 2 Oct with the 100 m rule) gives the distance to the
  LPA edge: "Outside the LPA by 42 m; boundary sources differ by about 15 m here."
- `plan_coverage` values (no new value; descriptions of `PlanCoverage` and `authorities`
  extended): an LPA with no register row at all (Magadi, Kanakapura, Ramanagara,
  Channapatna, GBBSC) is `no_master_plan_found` with the authority still listed and the
  sources checked; `lpa_no_zone_map` stays for LPAs registered as having no master plan (STRR,
  Doddaballapura) and for a loaded plan whose LPA takes in a sliver of a village (or a point)
  that the plan leaves uncoloured (< 5 % of the village coloured).
- Data (authority_villages.csv): Bengaluru North's old-city Kasaba villages are BDA by the RMP
  2031 village list (Annexure 1 sl 261, City Survey Sheets 1-97), note "Coverage by village
  list ..., no parcel geometry".

---

## 1.16.0 — 2026-10-01 — planning

Additive. Every village in Bengaluru Urban and Rural gets an answer.
- `/authority`: new `plan_coverage` (PlanCoverage: `plan_loaded`, `plan_registered_not_loaded`,
  `lpa_no_zone_map`, `no_master_plan_found`), `authorities` (AuthorityEntry per covering
  authority, largest share first; split villages list each authority, never merged) and
  `sources_checked` (SourceCheck: source, url, doc_id, checked_on, finding). The 1.15
  top-level fields stay and repeat the largest-share entry.
- PlanRef: `loaded` (zones loaded or registered only) and `doc_ids` (source documents).
- Plan: `loaded`.
- New SourceLayer enum (`detail`, `hobli`, `lpa_map`, `composite`) on ZoneProperties, SheetQA and
  ZoneHit, with `sheet` (sheet name). On a hit, the sheet holding its largest share.
- ZoneHit `mixed_source_layers` (boolean): the hit spans sheets from more than one source layer.
  `position_uncertainty_m` then uses the coarser layer, not the largest-share sheet.
- `/zones/at`: hits from overlapping plans are returned per plan with their own status and
  `status_condition`, never merged (documented; already the behaviour).
- `/authority` points within 100 m of an LPA they are not inside (boundary sources differ) return
  that LPA as `partial` with a note (no point falls between two LPAs).
- New plans in the registry (`/plans`, `loaded: false`): STRR-LPA, BMRDA-LPAS, BMRDA-RSP2031,
  BMICAPA-ODP2004, DPA-LPA; BMRDA-ANK-MP2031 zones loaded (Hoskote reloaded with forest).

---

## 1.15.0 — 2026-10-01 — planning

Additive.
- `status_condition` (string or null) next to every `status` / `status_label`: Plan, PlanDoc,
  PlanRef, ZoneProperties, ZoneHit, OverlayProperties, OverlayTouch, StreamNearby. Carries a
  condition on the status from the GO, e.g. Hoskote Master Plan 2031: final, "Subject to the
  High Court's final judgment in W.P. 4188/2016".
- `/authority` beyond the BDA area: Hoskote LPA (authority "BMRDA-HSK", operative plan
  BMRDA-HSK-MP2031, final with condition) and BIAAPA LPA (authority "BIAAPA", no plan loaded,
  note "No 2031 plan published; Master Plan 2021 exists (not loaded)").

---

## 1.14.0 — 2026-09-30 — planning

Additive (audit fixes M1, W6).
- New zone value: `zone_label_native` "Road space (not coloured on the plan)", `class_norm`
  `road_space`, status from the sheet (draft). Thin white strips between zones (under 2 source
  pixels) that were dropped before and left parcels without a zone; `/zones/at` hits now sum to
  ~100 % of a parcel inside the LPA. Thin pieces of a coloured zone are no longer dropped
  either; they keep their own class.
- `ZoneProperties.cartographic` and `ZoneHit.cartographic` (boolean): true for `road_space`,
  a drawing artefact class that is not comparable with plan area tables.
- `/authority` village coverage: the "schedule says full, map shows less" rule now makes a
  village full only from 90 % map share; 75-90 % is partial with a note.

---

## 1.13.1 — 2026-09-30 — planning

`/authority` coverage rules for villages refined (description only, no schema change):
- Schedule says full and map shows 75-98 %: `coverage` full, note "boundary drawing differs at
  the edge".
- Not in the schedule and map share under 5 %: `coverage` none (edge noise).
- Schedule names joined to ours through a reviewed alias table
  (`infra/planning/village_aliases.csv`); aliases failing the name, share or PD/taluk check
  are listed there but not applied.

---

## 1.13.0 — 2026-09-30 — planning

**`GET /authority` implemented (BDA area only, build step 1.7).** No longer returns 501.
- Villages: from the village-to-authority table (RMP 2031 LPA schedule compared with the LPA
  boundary on the plan sheet). BDA villages: authority `BDA`, `operative_plan` null (RMP 2015
  not loaded), `draft_plans` [BDA-RMP2031], note "Only a draft plan is loaded for this area".
- Outside BDA: `coverage` none, `authority` null, note "Outside BDA; this area's plan isn't
  loaded yet".
- lat/lng: point-in-LPA-polygon test.
- `AuthorityResult` gains `share_pct`, `pd`, `source`, `mismatch_note` (additive).

---

## 1.12.0 — 2026-09-30 — planning

Additive.
- `GET /zones/at`: new `trace_hits` list for hits under 1 % of the parcel area or under 20 m2.
  They no longer appear in `zones`, and they are ignored when measuring `edge_distance_m` /
  `near_edge` of the remaining hits.
- `GET /zones` and `GET /overlays`: optional `simplify_m` (2 | 8 | 25, default 8), geometry
  pre-simplified at startup with that tolerance; responses echo `simplify_m`. `/zones/at`
  keeps full geometry.

---

## 1.11.0 — 2026-09-30 — planning

**New endpoint `GET /overlays?plan_id&bbox&kind`:** map-symbol overlays (`ngt_buffer`,
`forest_symbol`, `stream_centreline`) as GeoJSON. Same flags and 0.05-degree bbox cap as
`/zones`. Every feature carries `status`, `status_label` and a `note` that it is a map
symbol, not a measured buffer.

**`GET /zones/at` extended (additive):**
- `ZoneHit.position_uncertainty_m` = sqrt(georef_rmse_m^2 + m_per_px^2) (about 11 m for BDA-RMP2031)
- `ZoneHit.near_edge` = edge_distance_m < position_uncertainty_m
- `ZoneHit.inferred`, `inferred_share_pct`, `inferred_notes`: zone inferred under the NGT hatch
  or a stream symbol
- `ZonesAtResult.overlays_nearby`: NGT areas and forest symbol areas the parcel touches, and the
  nearest stream centreline within 100 m with its distance

`ZoneProperties.inferred_note` added. `uncoloured` documented as a normal zone value (native
label "Not coloured on the plan"). `/authority` documents `501` until the authority table
is built (step 1.7).

---

## 1.10.0 — 2026-09-30 — planning

- `SheetQA.extraction` enum gains `raster_palette`: zones classified by exact colour from a
  lossless raster with a fixed palette (the BDA-RMP2031 Proposed Land Use composite).
- Schema examples updated to the registered doc_ids: `BDA-RMP2031-ELU-PD<n>` (Existing Land
  Use sheets) and `BDA-RMP2031-PLUCOMP` (proposed-zone source). No `PLU-PD` sheets exist.

Additive, no breaking change.

---

## 1.9.0 — 2026-09-30 — planning

**`SheetQA` extended:** two optional fields on every sheet's QA block (in `/zones` feature
`qa` and `/zones/at` `sheets_qa`):
- `m_per_px` (number or null): ground size of one source pixel in metres; null for vector sheets.
- `georef_method` (string or null): how the sheet was georeferenced (e.g. `affine_gcp`).

Additive, no breaking change. Needed for the raster BDA-RMP2031 Proposed Land Use composite.

---

## 1.8.0 — 2026-09-30 — planning

Initial planning service contract (`contracts/planning.yaml`, port 8012). 2031 plans only.

Endpoints: `/health`, `/plans`, `/docs/{doc_id}`, `/authority`, `/zones`, `/zones/at`.
No `/classify` yet — added only after the 2031 layers are signed off.

Every record carries its document `status` (final / draft / superseded / reference) and
`status_label`. Zones inherit status from their source sheet. BDA RMP 2031 is draft, never
operative. No endpoint returns an answer or confidence.

Feature flags: `feature.planning.layers` (all routes except `/health`),
`feature.planning.plan.<plan_id>` (per plan). Web flags `NEXT_PUBLIC_ENABLE_PLANNING_LAYERS` /
`NEXT_PUBLIC_ENABLE_PLANNING_PANEL` come with the Phase 1 web work.

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
reprojected to EPSG:4326. No path or schema changes. Coordinates shift versus 1.2.0 because the
Kalianpur correction was removed; size of the shift not measured.

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
