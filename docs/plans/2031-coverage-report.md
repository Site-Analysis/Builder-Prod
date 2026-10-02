# 2031 planning layers: coverage and accuracy report (all plans)

Round of 1-2 Oct 2026 ("every village in Bengaluru Urban and Rural gets an answer"), updated by
the round of 2 Oct (Anekal round 4, Nelamangala second route, taluk rows), branch
`feat/planning-2031-phase0`, contract `planning` 1.17.0. Per-plan QA:
`hoskote-2031-qa.md`, `anekal-2031-qa.md`, `nelamangala-2031-qa.md`; seams: `2031-seams.md`;
defaults applied: `open-decisions.md`. The BDA RMP 2031 audit of 30 Sep - 1 Oct follows below
unchanged.

## Plans

| Plan | Status | Zones | Source layers | Class agreement with the plan's own table | Georef / OSM check |
|---|---|---|---|---|---|
| BDA RMP 2031 | **draft** (never approved) | loaded (331,194) | composite (PLUCOMP) | see the BDA audit below | ~11 m position uncertainty |
| Hoskote MP 2031 | **final**, subject to W.P. 4188/2016 | loaded (1,029,504) | detail 79.8 %, hobli 1.8 %, not coloured 18.3 % | all classes ±10 % except unclassified (+40 %, explained) and transport (cartographic) | grid labels; floor 6.85 m; no sheet > 10 m |
| Anekal MP 2031 | **final** | loaded (729,417, round 4) | detail 89.9 %, lpa_map 5.7 %, not coloured 4.4 % | on the plan's own extent every class ±10 % except water +20 % (measured cause, SME pending); hatched PU / hillocks not extracted (sheet warning on every hit); transport cartographic | margin labels (primeocr); floor 7.02 m; 13/16 sheets ≥ 3 checks; no sheet > 10 m |
| Nelamangala MP 2031 | **final** | **not loaded** | — | — | LPA map outline fit IoU 0.981; 11 grids refined against OSM near that prior: all rejected (matches no better than the null baseline) |
| BIAAPA MP 2021 | final (2021 horizon) | registered, not loaded | — | — | — |
| BMICAPA ODP 2004 | unconfirmed (no GO found) | registered, not loaded | — | — | — |
| STRR LPA | no master plan | — | — | — | — |

## G1: per taluk (all 2,287 villages)

`audit_service.py --steps all` (every parcel in each loaded plan's LPA through the /zones/at
functions) and `--steps http500`; `planning/audit/g1_taluks.csv`.

| Taluk | Villages | plan_loaded | registered_not_loaded | lpa_no_zone_map | no_master_plan_found | blank | Parcels inside loaded plans | sum ≥ 99 % | Uncoloured % | /zones/at errors | HTTP errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bangalore North (20/1) | 188 | 143 | 43 | 0 | 2 | 0 | 19,114 | 19,114 (100.000 %) | 15.49 | 0 | 0 |
| Bangalore South (20/2) | 224 | 161 | 15 | 0 | 48 | 0 | 9,217 | 9,216 (99.989 %) | 18.42 | 0 | 0 |
| Anekal (20/3) | 304 | 304 | 0 | 0 | 0 | 0 | 77,797 | 77,794 (99.996 %) | 6.08 | 0 | 0 |
| Bangalore East (20/4) | 186 | 186 | 0 | 0 | 0 | 0 | 14,595 | 14,595 (100.000 %) | 15.44 | 0 | 0 |
| Yelahanka (Bangalore North Additional) (20/5) | 196 | 108 | 87 | 0 | 1 | 0 | 24,817 | 24,815 (99.992 %) | 16.1 | 0 | 0 |
| Nelamangala (21/1) | 367 | 0 | 327 | 40 | 0 | 0 | 0 | 0 (—) | — | 0 | 0 |
| Doddaballapura (21/2) | 302 | 0 | 237 | 65 | 0 | 0 | 0 | 0 (—) | — | 0 | 0 |
| Devanahalli (21/3) | 226 | 0 | 190 | 35 | 1 | 0 | 6 | 6 (100.000 %) | 96.52 | 0 | 0 |
| Hoskote (21/4) | 294 | 252 | 0 | 42 | 0 | 0 | 63,834 | 63,834 (100.000 %) | 16.2 | 0 | 0 |

Changes this round (open-decisions #27-#29): Bangalore North 36 old-city Kasaba villages BDA by
the RMP 2031 village list; Bangalore South 47 Magadi / Kanakapura villages
`no_master_plan_found` (their plans are not registered); Devanahalli's 2 and 15 STRR-band
villages in Hoskote taluk keep their own authority's value where the loaded plan leaves their
sliver uncoloured. Anekal parcels re-audited on the round-4 zones: 231 villages, 0 errors,
0 parcels below 99 %.

Pass bar: **0 villages without a value** (met); **0 errors** (met: 0 function errors over
289,101 parcels run (BDA 118,588, Hoskote 85,499, Anekal 85,014), 0 HTTP errors); **≥ 99 % sum inside loaded plans**: met except 6 BDA
parcels (98.72-98.99 %, all fully inside the LPA): 20/2/17/34 29/*/*, 20/3/6/49 164/*/1,
20/3/8/22 120/*/3, 20/3/9/21 7/*/*, 20/5/5/21 158/*/*, 20/5/8/20 27/*/*. Reason: cut slivers
under half a source pixel are dropped at zone seams (about 1 % of these parcels' area).

Uncoloured %: share of the zone area on parcels inside a loaded plan that is "Not coloured on
the plan" or road space (BDA). Devanahalli's 6 parcels sit on the Hoskote edge (not coloured).

## G2: HTTP pass

500 parcels across 18 strata (plan × plan_coverage), 961 calls (2 Oct, round-4 zones): **all
HTTP 200, all JSON valid**. `/zones/at` p50 2.6 s, p95 2.8 s; `/authority` p50 2.1 s, p95 2.1 s.

## G3: acceptance pages

`<data-root>/planning/acceptance/`: BDA (5, `acceptance_pack.py`), `BMRDA-HSK-MP2031/` and
`BMRDA-ANK-MP2031/` (5 each, `acceptance_lpa.py`: deep in a zone, across two zones, water edge,
partial village, just outside). Source sheet crop next to our zones, with the live /zones/at and
/authority answers; raw JSON next to each PNG. The Anekal pages found the round 2 road-as-forest
error (fixed in round 3). Anekal round 4 (2 Oct; round 3 kept as `BMRDA-ANK-MP2031_round3/`):
deep (industrial 100 %, matches the sheet), across two zones (industrial 62 % / road band 38 %,
matches), water edge (industrial 77 % / stream 19 %, matches), just outside (no Anekal hit, BDA
only, correct); partial village: the parcel lies on the title map's margin grid label "796000",
extracted as transport inside the title-map extent (open-decisions #32). Hoskote: base-map building outlines show as small forest / transport
specks in settlements (a tighter forest colour cut real forest by 13 %, so not applied).

## G4: service memory, load time, and what a deployment needs

Measured 2 Oct on this machine (planning service, plans BDA, Hoskote, Anekal loaded; lazy
simplification): first request per plan 48 s (BDA), 60 s (Hoskote), 47 s (Anekal), about 2.6
minutes for all three; then /zones/at p50 2.6 s. Peak during the 500-parcel HTTP pass: **working
set 10.2 GB, private 11.7 GB**.

Build-time only (never on a server): source PDFs `raw/` 885 MB, OSM caches 94 MB, per-sheet
checkpoints and earlier rounds (`*_sheets*`) about 1.2 GB, audit / acceptance outputs 20 MB.

What the service reads today: zone parquets 136 MB (BDA) + 69 MB (Hoskote) + 46 MB (Anekal) +
21 MB (BDA overlays) + LPA / village files ~10 MB, about 280 MB on disk, which expand to ~11 GB in
memory (GeoDataFrames, spatial indexes, simplified copies). That does not fit a small server.

The build output can go to the map and the API without the server holding the polygons:
1. Map display: pre-build vector tiles (one PMTiles file per plan, zoom 10-17, at build time)
   served as static files (S3 / CDN or the web app's static folder); the browser draws them and
   the server holds nothing for /zones.
2. Parcel answers: /zones/at is already computed for every parcel in every loaded LPA by the audit
   (289k parcels); store those answers (zones, overlap %, edge distance, flags, sheets_qa) in one
   SQLite / parquet keyed by (dist, taluk, hobli, vlg, survey), tens of MB, read per request.
   /authority already comes from `authority_villages.csv` (2,287 rows).
3. Points between parcels (/authority by lat/lng) need only the LPA polygons (~2 MB).
This is a contract-compatible change (same responses), but a design change: it needs Tanmay's OK
(open-decisions #33); nothing was changed this round.

---

# BDA RMP 2031 (Draft): coverage and accuracy audit

Round 1: 30 Sep 2026 (audit only). Round 2: 1 Oct 2026 (fixes decided by Tanmay on 30 Sep,
then the audit re-run). Branch `feat/planning-2031-phase0`. RMP 2031 is a draft plan, never
approved.

CSVs: `<data-root>/planning/audit/` (round 1 copies in `audit_before_fix/`; data-root here
`C:\Users\tanny\Downloads\planning`). Scripts: `infra/scripts/planning/audit_rmp2031.py`
(A1-A5, C9, C10, summary), `audit_service.py` (B6, B7, planning service venv),
`crosscheck_pdr.py --all --extents` (C8, PD extents).

## Step 1.8 acceptance

**Self-checked, SME pending.** The acceptance pack (5 parcels: deep in a zone, straddling
two zones, stream/NGT edge, partial-coverage village, just outside BDA; one page each with
the source crop, our zones, the parcel outline and the `/zones/at` + `/authority` output) is in
`<data-root>/planning/acceptance/`, built with `acceptance_pick.py` / `acceptance_pack.py`.
It was checked against the source map by us; the SME review has not happened. The SME
questions run on defaults, see `rmp2031-sme-defaults.md`. The pages were rendered on 30 Sep,
before the round-2 fixes (road space, NGT closing); re-run `acceptance_pack.py` before sending
them.

## Before / after

| Check | Round 1 (before) | Round 2 (after) |
|---|---:|---:|
| LPA area with no zone polygon (A2) | 3,004 ha (2.49 %), 221,595 pieces | **2.17 ha** (0.002 %), 12,231 pieces; 2.14 ha of it is the raster step along the LPA edge, 165 m2 interior |
| Gap pieces >= 100 m2 | 65,682 | **0** |
| Parcels fully inside the LPA whose `/zones/at` hits sum < 95 % (B6) | 15,627 | **0** |
| ... sum < 99 % | not measured | **6** (all cross the LPA boundary, see B6) |
| ... sum > 101 % | 0 | 0 |
| `/zones/at` errors over 92,623 parcels | 0 | 0 |
| `/zones/at` time per parcel (audit run, 6-10 workers) | ~0.16 s | ~0.10 s (1,610 s for all BDA parcels on 6 workers) |
| `/authority` errors (2,287 villages + 7,650 points) | 0 | 0 |
| `/authority` point mismatches | 5 | **1** (Lingadeeranahalli, noted, no change) |
| Random-sample mismatches (C10) | 5 of 124 | **0** of 118 |
| PDR figure cross-check (C8) | 36 pass, 4 borderline, 2 mis-registered | 36 pass, 4 borderline, **2 "cross-check unavailable"** (PD 31, 37) |
| NGT buffer within +-5 pp of PDR (A1) | 10 / 41 PDs | **27 / 39** |
| NGT overlay area | 16,134 ha | 11,613 ha |
| PDs where every PDR category passes (combined road row) | 3 (1, 6, 20) | 8 (1, 4, 5, 6, 8, 15, 20, 39) |
| Villages over 30 % uncoloured or inferred (A4) | 186 of 493 | 168 of 493 |
| Georef: PDs with fewer than 3 check points | 11 | **0 of the 11 with an extent** (independent check points, C9) |
| Georef: PDs over 20 m RMSE | PD 5 (20.4 m, n=4) | **PD 26** (21.8 m, n=8, independent points); PD 5 is 2.4 m on independent points |

## What changed (round 2)

Commit `03a2bce` (contract 1.14.0, additive).

1. **M1 road gaps.** Thin white pieces (under 2 source pixels, ~10 m) are no longer dropped:
   they are the zone "Road space (not coloured on the plan)", `class_norm` `road_space`,
   status draft, `cartographic: true` (a drawing class, not comparable with plan area
   tables). Thin pieces of a coloured zone keep their own class (not given to neighbours).
   A second cause turned up and was fixed: every one-pixel piece (36,177 of them) was lost to
   a float cut-off (`area < px_area`); the minimum is now half a pixel.
   Road space: 3,138 ha in the LPA (median 2.8 % of a PD, max 6.3 %); zones now cover
   120,674.5 of the LPA's 120,676.7 ha.
   The service passes `cartographic` through `/zones` and `/zones/at`; web legend and card
   show it as a road corridor, not a zone decision.
   Side effect fixed in the same commit: road space added ~230,000 small polygons and made the
   edge-distance union in `/zones/at` slow (4 s per parcel). The union now takes only the
   zone pieces touching the parcel, clipped just beyond it: identical results on 469 of 469
   checked hits, 0.07 s per parcel.
2. **W1 PD 31 / PD 37.** Wider scale search (1.6-26 m/px): best agreement still 0.4 % and
   3.1 %, so both are marked "cross-check unavailable" (floor 60 %) and have no PD extent.
   PD-level A1/C9/C10 numbers are not given for them.
3. **W4 NGT.** On PLUCOMP the NGT buffer is a solid #38a800 band along streams and lakes, not a
   line hatch. The old closing (10 px) bridged across the stream and the parks between two
   bands. Now 2 px (bridges road lines drawn across the band). Per PD below.
4. **M3 / W7 check points.** OSM residential/unclassified/living-street roads fetched per PD
   (Overpass; two dense PDs in 2x2 tiles) and used only as check points; the georeference
   fit never saw them. A pair is kept when the sheet junction is isolated (100 m), the pairing
   is mutual, within 40 m, and unambiguous (second-nearest OSM junction at least 2.5x and
   30 m further). No refit.
5. **W6.** A village the schedule lists as full is full only from 90 % map share; 75-90 % is
   partial. Changed: Bhutanahalli (89.5 %) and Chokkanahalli (86.0 %) to partial.
6. **+-5** is percentage points of the PD's area. The combined road-space row stays (now
   including Road space).
7. **M2.** The 52 BDA villages without parcel geometry are in
   `infra/planning/cadastral_data_gaps.csv` as a cadastral data gap. No fix this phase.
8. **W2 (borderline PDs 8, 15, 17, 41) and W3 (uncoloured, PD 30):** unchanged, waiting on the
   SME.

## Summary by district and taluk (after)

| District | Taluk | Villages | In BDA | BDA, no parcels | Searchable | Outside BDA (no plan loaded yet) | `/zones/at` parcels | Errors | Sum < 99 % | Villages > 30 % uncoloured/inferred |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bengaluru Urban | Bangalore North | 188 | 107 | 20 | 81.3 % | 81 | 20,417 | 0 | 0 | 26 |
| Bengaluru Urban | Bangalore South | 224 | 141 | 10 | 92.9 % | 83 | 11,195 | 0 | 1 | 60 |
| Bengaluru Urban | Anekal | 304 | 54 | 0 | 100 % | 250 | 19,398 | 0 | 3 | 17 |
| Bengaluru Urban | Bangalore East | 186 | 143 | 20 | 86.0 % | 43 | 13,451 | 0 | 0 | 39 |
| Bengaluru Urban | Yalahanka (incl. North Additional) | 196 | 100 | 2 | 98.0 % | 96 | 28,162 | 0 | 2 | 26 |
| Bengaluru Rural | Nelamangala / Doddaballapura / Devanahalli / Hoskote | 1,189 | 0 | – | – | 1,189 | – | 0 | – | – |
| **Total** | | **2,287** | **545** | **52** | **90.5 %** | **1,742** | **92,623** | **0** | **6** | **168** |

Accuracy flags: PD 26 lower map accuracy (21.8 m); PD 31 and 37 no PDR cross-check; PD 8,
15, 17, 41 borderline PDR cross-check. Per-PD PDR table results in `pd_vs_pdr.csv`.

## What's still open

| # | Open | Status / proposal |
|---|---|---|
| O1 | **PD 26 has lower map accuracy**: 21.8 m RMSE on 8 independent check points (median 9.1 m, max 36.1 m), the only PD over 20 m. | Flagged, no refit (decision). Edge answers there carry more uncertainty than the ~11 m used elsewhere. |
| O2 | **PD 31, 37 cross-check unavailable** (PDR figures do not register onto PLUCOMP). | No PD extent for them; their zones exist and are used. |
| O3 | **NGT still above the PDR in 12 PDs**, mostly the outer agricultural ones (21, 28-30, 34, 38, 40-42: PDR 0-3 %, drawn band 8-14 %). | Definitional: the PDR seems not to count NGT over agriculture. SME. |
| O4 | **Uncoloured share** (median PD 14.7 %, PD 30 34.5 %) and 168 villages over 30 %. | Waiting on the SME (W3). |
| O5 | **Borderline PDR cross-check** PD 8, 15, 17, 41. | Waiting on the SME (W2). |
| O6 | **52 BDA villages without parcel geometry.** | Cadastral data gap, `cadastral_data_gaps.csv`. |
| O7 | **Lingadeeranahalli** (map share 95.6 %, full): the one grid point that answers "outside the LPA" while its village is full, 15.6 m outside the edge. | Left as is (decision): within the ~11 m map uncertainty plus the parcel edge. |
| O8 | Water, Agriculture and road space still do not reconcile with the PDR tables in many PDs (A1). | Revisit with the SME answers on O3/O4. |

---

## A. Coverage

### A1. Planning districts (`pd_landuse.csv`, `pd_vs_pdr.csv`, `ngt_before_after.csv`)

PD extents are the coloured extent of each PDR figure registered onto PLUCOMP (PD
boundaries are not published as vectors); PD 31 and 37 have none (O2). Every other PD has
zones; area with no polygon is now at most 0.01 % of a PD (was up to 7.1 %). Uncoloured
median 14.7 % (PD 30 34.5 %); inferred under symbols median 14.9 % (was 16.2 %).

PDR tables: read for all 42 PDs (PD number from the chapter number; PD 11's caption misprints
"PD 10", PD 33's has none). PD 27's table is blank in the source (only Agriculture and the
total, copied from PD 26).

Categories passing +-5 pp, before -> after (after has 39 PDs with an extent and a usable table):

| Category | Before | After |
|---|---|---|
| Commercial | 41/41 | 39/39 |
| Industrial | 39/39 | 37/37 |
| Public Utility | 41/41 | 39/39 |
| Public & Semi Public | 40/41 | 39/39 |
| Parks / open spaces | 40/41 | 38/39 |
| Forest | 37/39 | 35/37 |
| Streams | 35/41 | 33/39 |
| Residential | 34/41 | 32/39 |
| Water Bodies | 24/41 | 24/39 |
| Agriculture | 17/31 | 17/29 |
| **NGT Buffer** | **10/41** | **27/39** |
| Unclassified + Transport (combined, incl. Road space and no-polygon) | 10/41 | 12/39 |

NGT buffer, % of PD area (PDR / before / after; fail = outside +-5 pp):
1: 5.9 / 9.5 / 8.1 · 2: 6.4 / 14.8 / 11.4 · 3: 6.2 / 17.9 / 13.7 fail · 4: 5.0 / 11.4 / 9.5 ·
5: 9.8 / 17.6 / 14.7 · 6: 5.8 / 8.3 / 6.9 · 7: 7.4 / 13.2 / 9.8 · 8: 10.5 / 17.4 / 13.1 ·
9: 10.2 / 14.5 / 10.6 · 10: 13.6 / 17.8 / 12.6 · 11: 8.5 / 15.7 / 10.5 · 12: 10.8 / 15.4 / 10.1 ·
13: 12.9 / 17.3 / 13.0 · 14: 8.7 / 14.1 / 11.9 · 15: 10.3 / 18.0 / 15.0 · 16: 10.2 / 15.5 / 12.2 ·
17: 11.5 / 21.8 / 16.2 · 18: 9.3 / 18.6 / 14.8 fail · 19: 10.4 / 20.8 / 16.0 fail ·
20: 9.3 / 13.0 / 9.3 · 21: 1.3 / 12.3 / 8.4 fail · 22: 9.0 / 16.9 / 11.5 · 23: 12.0 / 15.8 / 10.8 ·
24: 10.3 / 17.4 / 11.2 · 25: 13.9 / 14.2 / 9.9 · 26: 5.1 / 13.7 / 8.9 · 28: 3.1 / 13.8 / 9.4 fail ·
29: 1.8 / 15.1 / 10.3 fail · 30: 0.6 / 16.5 / 10.8 fail · 32: 11.6 / 16.6 / 11.7 ·
33: 10.9 / 19.2 / 15.2 · 34: 7.2 / 18.8 / 13.0 fail · 35: 6.1 / 11.9 / 8.2 · 36: 10.7 / 19.8 / 12.3 ·
38: 6.9 / 19.8 / 12.9 fail · 39: 10.7 / 16.2 / 11.2 · 40: 0.6 / 19.5 / 13.1 fail ·
41: 0.0 / 21.2 / 13.8 fail · 42: 0.7 / 14.3 / 9.8 fail · (31, 37: no extent; 27: blank table).

Closing radius was chosen from the band's geometry, then checked against the tables (raster
measure, 39 PDs within +-5 pp): r=10 7, r=6 13, r=4 20, r=2 23, band pixels only 28. Even the
raw band exceeds the PDR totals, so the remaining gap is how the PDR counts NGT (O3).

### A2. Gaps (`gaps.csv`, `gaps_summary.json`)

2.17 ha in 12,231 pieces, none >= 100 m2. 2.14 ha lie on the LPA edge (the raster's pixel
steps against the smooth boundary); 165 m2 are interior. Round 1: 3,004 ha of road corridors.

### A3. Admin hierarchy (`admin_hobli.csv`, `bda_villages_no_parcels.csv`)

Unchanged: 2,287 villages, 545 in BDA (now 488 full, 57 partial after W6), all in Bengaluru
Urban. 52 BDA villages without parcel geometry (38 no file, 14 placeholder), listed in
`infra/planning/cadastral_data_gaps.csv`. Hierarchy errors: 0.

### A4. Villages (`village_quality.csv`)

493 BDA villages with parcels measured. Over 30 % uncoloured or inferred: 168 (was 186).
Road space is reported in its own column and not counted as uncoloured.

### A5. Documents (`documents.csv`)

Unchanged. PD 19: PDR figure and table used, extent registered (pass), zones present; only
its ELU sheet is missing from the source.

## B. Works everywhere

### B6. `/zones/at` over every BDA parcel (`zones_at_*.csv`)

92,623 survey numbers, **0 errors**. Empty: 5,420 (5,214 wholly outside the LPA in partial
villages; the rest touch zones only under 1 %). Partly outside the LPA (< 99 % inside):
2,054, not held to the sum. Fully inside the LPA and summing under 99 %: **6**, none under
98.7 %. All 6 cross the LPA boundary by a sliver (99.0-99.96 % inside), and the missing part
is the part outside the plan edge:

| Village key | Survey | Sum % | Area m2 | Share inside LPA | Reason |
|---|---|---:|---:|---:|---|
| 20/2/17/34 | 29/\*/\* | 98.99 | 15,021 | 99.00 % | crosses the LPA edge (152 m2 outside) |
| 20/3/6/49 | 164/\*/1 | 98.75 | 18,117 | 99.01 % | crosses the LPA edge (227 m2 outside) |
| 20/3/8/22 | 120/\*/3 | 98.92 | 1,852 | 99.96 % | crosses the LPA edge (20 m2: edge raster step) |
| 20/3/9/21 | 7/\*/\* | 98.72 | 18,303 | 99.49 % | crosses the LPA edge (235 m2 outside) |
| 20/5/5/21 | 158/\*/\* | 98.95 | 85,398 | 99.06 % | crosses the LPA edge (899 m2 outside) |
| 20/5/8/20 | 27/\*/\* | 98.96 | 18,742 | 99.06 % | crosses the LPA edge (195 m2 outside) |

No parcel away from the plan edge sums under 99 %.

### B7. `/authority` (`authority_*_check.csv`)

2,287 village keys: 0 errors, 0 mismatches with the table. 7,650-point grid: 0 errors,
1 mismatch: Lingadeeranahalli (20/2/20/38, map share 95.6 %, full) at 12.86804, 77.51198,
15.6 m outside the LPA edge. Left as is (O7).

## C. Accuracy

### C8. PDR figure cross-check (`pdr_crosscheck_all.csv`)

Pass = agreement excluding PDR greys >= 80 % and >= 5 points above the majority-class
baseline. 36 pass (80.6-95.2 %); borderline PD 8, 15, 17, 41 (W2, SME); PD 31 and 37
cross-check unavailable after the wider search (0.4 % and 3.1 %; floor 60 %).

### C9. Georeference per PD (`georef_pd.csv`, `georef_pd_independent.csv`)

Round 1 method (major/secondary junctions, not independent of the fit): 212 points, RMSE
11.5 m. Round 2 adds **independent** check points (OSM minor roads, never used in the fit) for
the 11 thin PDs and PD 5:

| PD | OSM minor ways | Independent points | RMSE m | Median m | Max m | Flag |
|---|---:|---:|---:|---:|---:|---|
| 5 | 8,045 | 4 | 2.4 | 1.9 | 3.9 | |
| 10 | 9,969 | 9 | 17.3 | 9.0 | 30.1 | |
| 12 | 7,392 | 10 | 17.2 | 8.2 | 34.3 | |
| 22 | 6,159 | 11 | 19.8 | 13.7 | 34.4 | |
| 25 | 2,329 | 11 | 11.8 | 8.2 | 22.5 | |
| **26** | 2,137 | 8 | **21.8** | 9.1 | 36.1 | **lower map accuracy (O1)** |
| 28 | 4,734 | 4 | 8.8 | 8.8 | 12.1 | |
| 36 | 3,277 | 16 | 14.7 | 9.9 | 37.8 | |
| 37 | – | – | – | – | – | no PD extent |
| 39 | 2,079 | 9 | 17.8 | 14.0 | 32.3 | |
| 41 | 5,492 | 14 | 16.4 | 6.9 | 35.0 | |
| 42 | 3,471 | 14 | 11.1 | 4.2 | 28.7 | |

PD 5's round-1 20.4 m came from 4 points on major roads; independent points give 2.4 m.
Medians sit at 2-14 m; RMSE is pulled up by a few 30-38 m points (likely residual junction
mismatches in dense street grids), so treat per-PD RMSE on 4-16 points as indicative.

### C10. Random parcels (`sample_parcels.csv`)

3 per PD, one per village, seeded: 118 parcels (PD 31, 37 no extent; PD 1 only 1). 78 match,
38 sit where the source shows a map symbol and ours is inferred, 2 are road space over white
source pixels (match). **0 mismatches** (round 1: 5, all road gaps).

## Method notes

- "+-5" = percentage points of the PD's area.
- PD extents come from registered PDR figures; A1/C9/C10 per-PD numbers are only as good as
  that registration.
- B6 runs the route's functions directly (no HTTP); B7 calls the router function with a store
  built from the same files.
- Edge distance (`/zones/at`): distance from the parcel boundary to the zone's boundary (0 when
  they cross); a parcel inside a zone measures to the nearest other zone or the plan's outer
  boundary, capped at 200 m. Compared with the round-1 method on 402 hits: 387 identical,
  near-edge flag the same on 399; the differences are the 200 m cap and parcels next to the
  few remaining gaps.
