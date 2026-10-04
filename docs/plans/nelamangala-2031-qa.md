# Nelamangala Master Plan 2031: extraction QA

Plan `BMRDA-NLM-MP2031`, status **final**, GO UDD 150 BMR 2013, 01-06-2015 (provisional of
16-09-2013 registered as superseded). LPA later extended by 37 villages of Madhure hobli,
Doddaballapura taluk (UDD 141 BMR 2015, 08-12-2015), agricultural zone until the plan is
revised. Sources: `BMRDA-NLM-MP2031-MAP002`–`MAP023` (21 PDF sheets + 2 JPGs) in
`infra/planning/plan_docs.csv`.

**Result: zones not loaded.** Every village of the LPA still gets an answer:
`plan_registered_not_loaded` (final plan registered, with its sheets), or `lpa_no_zone_map`
for the Madhure villages. See `docs/plans/open-decisions.md` #4 and #5.

Self-checked. SME pending.

## 1. What the plan publishes

| Sheet | Drawing | Scale | Content | Usable as zones? |
|---|---|---|---|---|
| MAP002 | Drg 03 | 1:70,000 | LPA map, coloured by taluk (Nelamangala / Bangalore North / Magadi), 735 km², 345 villages | No: not land use |
| MAP003 | Drg 08 | 1:70,000 | TGR reservoir catchment zones | No: an overlay, not land use |
| MAP004 | Drg 31 | 1:21,000 | Proposed land use, Nelamangala conurbation, with its land-use table | Yes, if georeferenced |
| MAP005–MAP015 | Drg 32–42 | 1:5,000 | Nelamangala grids A1–D2 (MAP012 = grid B3, only a 1,024 px JPG: low-res) | Yes, if georeferenced |
| MAP017–MAP022 | Drg 44–49 | 1:5,000 | Sompura grids | Yes, if georeferenced |
| MAP023 | Drg 50 | 1:5,000 | Thyamagondlu | Yes, if georeferenced |

The rest of the LPA (outside the three towns) and the Madhure villages have no published
zone map.

Plan's own land-use table (Drg 31, Nelamangala conurbation, ha): residential 2,924.67,
commercial 271.66, industrial 820.90, public & semi-public 235.66, park & open space
394.44, public utility 49.26, transportation 1,186.55; total 5,883.14; water bodies 235.61;
grand total 6,118.75. Not compared: no zones loaded.

Legend colours read from MAP005 (for when the sheets are loaded): residential `#fdf769`,
commercial `#27a2ec`, industrial `#a217a6`, public & semi-public `#f10401`, park `#a3e458`,
public utility `#fd8300`, transportation `#a3a29b`, water `#9bf0f9`.

## 2. Why the zones are not loaded: no coordinates

The brief says to georeference each grid from its grid ticks. The published sheets have none:
no coordinate labels in the margins, no graticule (the magenta lines are the edges of the
neighbouring grid sheets), no text layer (96 dpi images). The only other route is matching
the sheet to OSM roads.

That method (`georef_osm.py`) was tested first on Hoskote sheets, whose printed UTM grid
gives the true answer (`validate_georef_osm.py`, results in
`<data-root>/planning/georef_osm_validation.json`). Each sheet is placed by correlating its
roads with OSM, then refined on road junctions. It is accepted only if the held-out junctions
match within 10 m and beat a null baseline (the same sheet shifted 1.5–3.5 km) by 3x:

| Hoskote map | Variant | Accepted by its own checks | Matches / null | True max error |
|---|---|---|---:|---:|
| 56 | affine | no | 7 / 7.4 | 592 m |
| 56 | translation | no | 13 / 7.2 | 213 m |
| 49 | affine | **yes** | 27 / 7.2 | **105 m** |
| 49 | translation | no | 20 / 7.1 | 227 m |
| 28 | affine | no | 0 / 0.6 | 9.1 km |
| 28 | translation | no | 3 / 1.9 | 8.0 km |
| 23 | affine | no | 2 / 1.0 | 13.8 km |
| 23 | translation | **yes** | 3 / 0.9 | **14.8 km** |
| 62 | affine | no | 4 / 4.2 | 12.8 km |
| 62 | translation | no | 8 / 5.9 | 12.8 km |
| 70 | affine | no | 2 / 1.5 | 8.7 km |
| 70 | translation | no | 0 / 0.9 | 11.1 km |

No sheet lands within 100 m, and the checks accepted two wrong placements. The plan's
transport layer and the OSM road network differ too much, and in a dense network
same-degree junctions match by chance. Loading Nelamangala zones this way would place them
hundreds of metres to kilometres off while appearing to pass QA, so they are not loaded.

What would unblock it: ground control for each grid (coordinates of 3 or more identifiable
points per sheet, e.g. from the Nelamangala Planning Authority, a field visit with GNSS, or the
cadastral survey stones). With those, `raster_plan.py` (the pipeline used for Anekal) loads
the sheets as they are.

## 3. LPA extent and authority rows

- LPA: BMRDA's LPA map (`BMRDA-LPA-MAP`): Nelamangala 687 km² today, 836 km² before the STRR
  LPA (2021) took its band. The plan's own map gives 735 km².
- Villages (`infra/planning/authority_villages.csv`, `build_authority_all.py`): an entry for
  Nelamangala wherever ≥ 5 % of the village is inside the current extent.
  - Madhure hobli (Doddaballapura taluk) villages: `lpa_no_zone_map`, note "added to the LPA
    in 2015 ... the plan has no zone map for it".
  - All others: `plan_registered_not_loaded`, note "final plan registered; its zone sheets
    print no coordinates and could not be georeferenced reliably, so its zones are not loaded".
  - Villages also in the STRR band list STRR too (`lpa_no_zone_map`, no STRR master plan).

## 4. Low-res grid B3

Grid B3 (Drg 39) is published only as `MAP012.jpg`, 1,024 × 705 px: about 4.5 m per pixel
at 1:5,000, against about 1.3 m for the other grids. Flag `low-res source (1,024 px JPG)`
applies if the grids are loaded later.

## 5. Second route (round of 2 Oct 2026): LPA map prior + OSM refine near it

`infra/scripts/planning/georef_nlm.py`; outputs in `<data-root>/planning/zones/nlm_sheets/`
(`map002_georef.json`, `grids_georef.json`). **Result: still not loaded**; every village keeps its
value (`plan_registered_not_loaded`, Madhure `lpa_no_zone_map`).

### B1. LPA map (MAP002, Drg 03, 1:70,000, 18.25 m/px fitted)

| Check | Bar | Result |
|---|---|---|
| Outline IoU vs the LPA the plan was made for (BMRDA pre-STRR less the 37 Madhure villages, 735 km² on the sheet) | ≥ 0.97 | **0.981** (outline median 43 m, p90 253 m) pass (vs BMRDA current extent 0.700, pre-STRR with Madhure 0.876) |
| Junction refine on major roads (OSM motorway-primary) | ≥ 10 held-out, RMSE ≤ 30 m | **fail**: 1 pair. The sheet's red road mask also takes the red "existing developed area" boundary and settlement outlines; 482 sheet junctions vs 166 OSM major junctions |
| Classes | ±10 % of a table | **not separable**: MAP002 is coloured by taluk (Nelamangala / Bangalore North / Magadi), no land use. Sub-step stopped |

Uncertainty would be the outline fit (median 43 m), floor 18 m. Used only as the prior for B2.

### B2. Grid sheets (1:5,000)

Prior for each sheet from the plan's own maps: its land-use colours matched (FFT correlation over
scale) to Drg 31 (MAP004, 1:21,000 conurbation land use, same legend; peak ratio 1.34-2.05, checked
by overlay on grid A1), MAP004's tanks matched to MAP002 (peak ratio 3.86), MAP002 placed by B1.
Then OSM roads (all classes, fetched for the Nelamangala box) within ±300 m: road correlation,
junction affine on half the matches, RMSE on the other half, and the null baseline (the same
match at 8 placements shifted 1.5-3.5 km; validated on Hoskote, where wrong placements scored as
well as right ones on held-out RMSE alone).

| Sheet | Grid | Prior peak | Fine peak | Shift from prior (m) | Matches | Held-out | Held-out RMSE (m) | Null mean | Brief bars (≤ 10 m, ≤ 300 m, ≥ 6) | Null ≥ 3x | Status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| MAP005 | A1 | 1.41 | 1.06 | 314 | 15 | 7 | 6.1 | 15.6 | no (shift) | no | rejected |
| MAP006 | A2 | 1.47 | 1.04 | 317 | 24 | 12 | 6.2 | 19.0 | no (shift) | no | rejected |
| MAP007 | A3 | 1.38 | 1.05 | 244 | 14 | 7 | 6.7 | 16.5 | yes | no | rejected |
| MAP008 | B1 | 1.96 | 1.30 | 173 | 51 | 25 | 7.5 | 38.0 | yes | no | rejected |
| MAP009 | B2 | 2.05 | 1.03 | 378 | 90 | 45 | 6.9 | 53.8 | no (shift) | no | rejected |
| MAP010 | C1 | 1.64 | 1.06 | 334 | 30 | 15 | 6.8 | 26.4 | no (shift) | no | rejected |
| MAP011 | C2 | 1.56 | 1.01 | 372 | 15 | 7 | 6.5 | 13.4 | no (shift) | no | rejected |
| MAP012 | B3 (1,024 px JPG, low-res) | 2.05 | 1.01 | 232 | 3 | 1 | 9.6 | 1.4 | few checks | no | rejected |
| MAP013 | C3 | 1.34 | 1.00 | 135 | 36 | 18 | 6.2 | 15.6 | yes | no | rejected |
| MAP014 | D1 | 1.42 | 1.02 | 133 | 7 | 3 | 8.4 | 4.2 | yes | no | rejected |
| MAP015 | D2 | 1.37 | 1.10 | 375 | 1 | 0 | — | 2.0 | no | no | rejected |
| MAP017-022, MAP023 | Sompura S1-S6, Thyamagondlu | 1.04-1.37 (tanks on MAP002 only) | | | | | | | not run (Overpass 504 / 429 for hours; priors too weak to bound ±300 m) | | not refined |

Why rejected: the fine road correlation has no distinct peak (ratio 1.00-1.30), and the
junction matches at the refined position are about what the same sheet gets 1.5-3.5 km away
(matches / null 0.9-1.4x, bar 3x). In a dense road network same-degree junctions within 10 m match
by chance, so the held-out RMSE of 6-8 m does not confirm the position. Four sheets (A3, B1, C3,
D1) pass the brief's three bars but fail the null check; loading them could put zones 100+ m off
while looking checked. Default applied: rejected (open-decisions #31).

### B3. Rows

No sheet accepted: no zones written; Nelamangala villages unchanged (327 `plan_registered_not_loaded`,
40 Madhure `lpa_no_zone_map` in Nelamangala taluk). The plan flag `feature.planning.plan.BMRDA-NLM-MP2031`
is in `run_services.ps1`; `/plans` reports it `loaded: false`, so the web shows no switch for it.

## 6. Round of 3 Oct 2026: on-demand index (layer_index.json)

`build_layer_index.py --plans BMRDA-NLM-MP2031` re-ran B1 and B2 from the source URLs, in temp
(peak 193 MB, 0 bytes left; 65 min, mostly Overpass retries). It kept the calibration in the index and
deleted the downloads. The MAP002 major-road refine was skipped (#63), so the grid priors use the outline fit:
- IoU 0.982 vs pre-STRR less Madhure (42 Madhure village outlines from the cadastral service);
- 18.25 m/px; uncertainty 43.5 m.

Town roads came from OSM in 0.04 deg tiles. Refine bars: held-out RMSE <= 10 m, shift from the prior <= 300 m,
>= 6 matches, matches >= 3x the null baseline.

| Grid | Doc | Shift (m) | Matches | Held-out RMSE (m) | Null mean | Brief bars | Null 3x | Row | Placement |
|---|---|---:|---:|---:|---:|---|---|---|---|
| A1 | MAP005 | 311 | 22 | 6.3 | 17.6 | no | no | rejected | — |
| A2 | MAP006 | 320 | 24 | 6.9 | 19.9 | no | no | rejected | — |
| A3 | MAP007 | 247 | 15 | 6.2 | 16.6 | yes | no | indexed | unconfirmed (Tanmay) |
| B1 | MAP008 | 169 | 51 | 6.4 | 38.9 | yes | no | indexed | unconfirmed (Tanmay) |
| B2 | MAP009 | 376 | 94 | 6.7 | 54.8 | no | no | rejected | — |
| C1 | MAP010 | 333 | 25 | 7.3 | 26.0 | no | no | rejected | — |
| C2 | MAP011 | 364 | 11 | 7.4 | 13.6 | no | no | rejected | — |
| B3 | MAP012 (1,024 px JPG) | 232 | 2 | 4.4 | 1.5 | no | no | rejected | — |
| C3 | MAP013 | 406 | 21 | 6.3 | 15.4 | no (shift) | no | rejected (#66) | — |
| D1 | MAP014 | 135 | 8 | 8.0 | 4.5 | yes | no | indexed | unconfirmed (Tanmay) |
| D2 | MAP015 | 372 | 1 | — | 1.9 | no | no | rejected | — |
| S1 | MAP017 | 235 | 11 | 6.3 | 6.0 | yes | no | rejected (#48) | — |
| S2 | MAP018 | 293 | 19 | 5.6 | 5.0 | yes | yes | **indexed** | **confirmed** |
| S3 | MAP019 | 377 | 3 | 9.4 | 3.9 | no | no | rejected | — |
| S4 | MAP020 | 295 | 5 | 9.1 | 2.1 | no | no | rejected | — |
| S5 | MAP021 | 270 | 8 | 5.6 | 7.9 | yes | no | rejected (#48) | — |
| S6 | MAP022 | 187 | 5 | 7.0 | 7.1 | no | no | rejected | — |
| T1 | MAP023 (Thyamagondlu) | 290 | 22 | 6.0 | 4.8 | yes | yes | **indexed** | **confirmed** |

Result: 5 of 18 sheets indexed.
- **Confirmed (2):** S2 and T1, uncertainty = held-out RMSE (5.6 m, 6.0 m). Their priors came from the MAP002 tank match
  only (peaks 1.04 and 1.14), and the shifts are close to the 300 m cap. Field verification is advised before relying on them.
- **Unconfirmed (3):** A3, B1 and D1, indexed on Tanmay's decision with the safeguards:
  - SheetQA warning "Placement not confirmed by an independent check; zones may be 100 m or more off. Verify on site.";
  - position uncertainty 100 m;
  - dashed outline and "(placement unconfirmed)" on the map;
  - capped at LOW in a future /classify (#59).
- **C3:** no longer passes the shift bar (406 m), so it is rejected (#66).

Zones are clipped to BMRDA's pre-STRR Nelamangala extent + 100 m (`STRR-LPA-MAP#nlm`). Outside the sheets the plan has
no zone map (`lpa_no_zone_map`, #49). Extraction: `raster_affine_sheet` (legend from MAP005, colour distance 60).
Worker peaks were 331 MB; the A3 test gave 13,362 zones.
