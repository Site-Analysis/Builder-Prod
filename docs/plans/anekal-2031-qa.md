# Anekal LPA Master Plan 2031: extraction QA

Plan `BMRDA-ANK-MP2031`, status **final**, GO UDD 151 BMR 2013, 03-09-2014 (verified on the
sheet stamps). Source: `BMRDA-ANK-MP2031-MP` ("Anekal MP.pdf", 22 raster sheets at 110 ppi;
URL unverified, from the "DTCP Docs" Drive folder). Built by
`infra/scripts/planning/extract_anekal.py` + `raster_plan.py` (2 Oct 2026). Output:
`<data-root>/planning/zones/BMRDA-ANK-MP2031.parquet` (1,233,332 polygons, round 3), `_lpa.parquet`,
`_qa.json` (rounds 1 and 2 kept as `_qa_v1.json`, `_qa_v2.json`).

Self-checked. SME pending.

## 1. Method, and why the primeocr layer was not loaded as is

The primeocr merged layer (TanmayCJ/prime-v1ocr, `Anekal_MP_merged_landuse.geojson`, 9,453
polygons) **fails QA**: its classes sum to 71,840 ha against the plan's 40,230 ha, because the
1:45,000 title map and the planning-district maps overlap the 1:10,000 sheets. So the Hoskote
method is run on the raw sheets, taking from primeocr only what it did well:

- georeference: primeocr's per-page affine from the UTM labels in the margin (OCR), on the 18
  pages where it reports `ok` (the 4 planning-district maps failed);
- legend palette: each page's own legend colours (hex + extra shades);
- map frame: primeocr's `map_rect`.

Then, per sheet: nearest legend colour (white = not coloured, else unknown), isolated grey
halos dropped, unknown filled from neighbours, polygonised, transformed, clipped to the LPA;
priority merge (1:10,000 sheets, then the 1:45,000 title map), touching pieces of a class
dissolved; LPA area on no sheet = "Not coloured on the plan".

Layers: **detail** = 16 sheets at 1:10,000 (Sarjapura SP-1..3, Attibele AT-1..3, Jigani
JI-1..4, Anekal AN-1..6); **lpa_map** = Map No. 39, the 1:45,000 title map, for the rest.
Excluded: Map No. 60 "Circulation Pattern" (a roads plan, not land use; it had passed
primeocr's georeference) and the 4 planning-district maps (no georeference).

Not extracted (open-decisions #13): the two **hatched** legend classes, public utility and
hillocks / quarries. Their hatch-line colours match edges and linework on every sheet (round
1: 2,197 ha and 3,362 ha against 31 and 293 ha in the plan's table). 324 ha in the table,
0.8 % of the LPA; their pixels are filled from their neighbours.

## 2. Land use vs the plan's own table (Map No. 39)

Map No. 39 "Proposed Landuse Analysis - Anekal LPA": urbanisable + non-urbanisable rows added
per class, total 40,230.03 ha.

Round 3 fix (found on the acceptance pages): the map draws road bands **mid-grey**, but the
legend's transportation swatch is a light grey (#ddd8d5). Mid-grey went to the hillock hatch key
in round 1 (3,362 ha "hillocks") and, once that class was dropped, to forest in round 2 (forest
4,876 ha). Round 3 adds the road greys (#a0a0a0, #959899, #8c8c8c) as transport keys.

| Class | Plan table (ha) | Round 1 | Round 2 | **Round 3** | Round 3 diff | ±10 %? |
|---|---:|---:|---:|---:|---:|---|
| Residential | 11,230.69 | 9,588.75 | 10,940.52 | **10,715.70** | -4.6 % | yes |
| Commercial | 768.32 | 590.58 | 673.61 | **585.91** | -23.7 % | no (-182 ha) |
| Industrial | 5,099.95 | 4,558.33 | 4,991.39 | **4,708.79** | -7.7 % | yes |
| Public & semi-public | 840.27 | 754.36 | 793.13 | **777.13** | -7.5 % | yes |
| Park & open space | 2,003.78 | 1,684.85 | 1,780.59 | **1,766.16** | -11.9 % | no (-238 ha) |
| Public utility | 31.16 | 2,196.74 | not extracted | not extracted | | (hatched) |
| Agriculture | 10,891.44 | 10,612.11 | 11,205.88 | **10,621.65** | -2.5 % | yes |
| Water bodies | 2,147.11 | 2,402.15 | 2,462.14 | **2,397.66** | +11.7 % | no (+251 ha) |
| Forest | 2,126.92 | 2,888.14 | 4,876.18 | **2,823.07** | +32.7 % | no (+696 ha) |
| Hillocks / quarries | 293.29 | 3,361.73 | not extracted | not extracted | | (hatched) |
| Transportation | 3,943.22 | 2,603.70 | 3,242.17 | **6,893.12** | +74.8 % | cartographic |
| Not coloured on the plan | — | 1,915.97 | 2,191.93 | **1,868.30** | | |

Not within ±10 % (round 3), with reasons:
- **Transportation +75 %** (cartographic, not compared): the road-grey keys also take other grey
  linework (village / survey boundaries, text) that used to be filled from neighbours. This is
  where most of the residential, commercial and park shortfall went (thin grey lines through
  them). Acceptable for zoning answers: transport pieces on a parcel are small slivers.
- **Forest +33 % (+696 ha)**: the hillock / quarry hatch (293 ha in the table, not extracted)
  sits on the same hills and fills partly from forest; the forest swatch colour (#87b57f) is also
  close to some park greens. Flagged for the SME (open-decisions #19).
- **Commercial -24 %, park -12 %, water +12 %**: 180-250 ha each; scanned palette (110 ppi JPEG)
  and grey linework through small commercial plots.

LPA: the title map's coloured extent (closed 60 m, holes filled) is **43,157.9 ha**; the plan
table says 40,230 ha; BMRDA's LPA map before STRR gives 40,464 ha (title minus BMRDA: 2,718 ha,
mostly agriculture and not-coloured land at the edge). Area check: zones 43,157.5 ha vs LPA
43,157.9 ha (-0.001 %, pass). Polygons: 1,233,332.

## 3. Georeferencing and OSM check

Georeference: primeocr's margin-label affine (3.3 m/px on the 1:10,000 sheets). OSM check:
Overpass was unavailable on 1-2 Oct (504 / 429 on every mirror), so junctions were checked
against the cached OSM extracts of 30 Sep (motorway to tertiary only, 11,841 junctions;
open-decisions #15). With the road bands now transport, **13 of the 16 detail sheets have ≥ 3
matches** (round 2: 4), RMSE 5.9-8.2 m: an independent check that supports the margin-label
georeference.

- **Floor** = median RMSE of the 13 well-matched detail sheets: **7.02 m**.
- **No sheet above 10 m.**

| Sheet | Layer | Scale | m/px | Matched / sheet junctions | OSM RMSE (m) | Georef used (m) | Uncertainty (m) | Unknown px % | Flags |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Map No. 41 (Sarjapura SP-1) | detail | 1:10,000 | 3.30 | 3/2887 | 5.9 | 5.9 own | 6.8 | 8.7 | |
| Map No. 42 (Sarjapura SP-2) | detail | 1:10,000 | 3.30 | 2/1964 | 7.6 | 7.0 floor | 7.8 | 4.8 | few ground checks |
| Map No. 43 (Sarjapura SP-3) | detail | 1:10,000 | 3.29 | 11/2620 | 7.8 | 7.8 own | 8.5 | 6.6 | |
| Map No. 45 (Attibele AT-1) | detail | 1:10,000 | 3.32 | 7/4030 | 8.2 | 8.2 own | 8.9 | 7.9 | |
| Map No. 46 (Attibele AT-2) | detail | 1:10,000 | 3.30 | 5/5052 | 6.2 | 6.2 own | 7.0 | 12.0 | |
| Map No. 47 (Attibele AT-3) | detail | 1:10,000 | 3.33 | 12/4969 | 7.6 | 7.6 own | 8.3 | 10.4 | |
| Map No. 49 (Jigani JI-1) | detail | 1:10,000 | 3.33 | 8/3912 | 7.2 | 7.2 own | 8.0 | 8.7 | |
| Map No. 50 (Jigani JI-2) | detail | 1:10,000 | 3.31 | 12/3372 | 7.5 | 7.5 own | 8.2 | 9.5 | |
| Map No. 51 (Jigani JI-3) | detail | 1:10,000 | 3.31 | 3/4606 | 6.2 | 6.2 own | 7.0 | 7.2 | |
| Map No. 52 (Jigani JI-4) | detail | 1:10,000 | 3.35 | 1/5983 | 8.2 | 7.0 floor | 7.8 | 2.8 | few ground checks |
| Map No. 54 (Anekal AN-1) | detail | 1:10,000 | 3.29 | 11/7381 | 6.5 | 6.5 own | 7.3 | 8.1 | |
| Map No. 55 (Anekal AN-2) | detail | 1:10,000 | 3.28 | 8/4387 | 7.0 | 7.0 own | 7.7 | 9.2 | |
| Map No. 56 (Anekal AN-3) | detail | 1:10,000 | 3.30 | 14/4531 | 7.0 | 7.0 own | 7.7 | 8.4 | |
| Map No. 57 (Anekal AN-4) | detail | 1:10,000 | 3.31 | 4/3220 | 7.8 | 7.8 own | 8.5 | 6.0 | |
| Map No. 58 (Anekal AN-5) | detail | 1:10,000 | 3.31 | 1/2698 | 3.6 | 7.0 floor | 7.8 | 2.6 | few ground checks |
| Map No. 59 (Anekal AN-6) | detail | 1:10,000 | 3.31 | 7/3972 | 6.8 | 6.8 own | 7.6 | 5.4 | |
| Map No. 39 (title map) | lpa_map | 1:45,000 | 14.86 | 3/11219 | 5.6 | 7.0 floor | 16.4 | 7.9 | |

## 4. Coverage by layer

| Layer | Area (ha) | Share of LPA |
|---|---:|---:|
| Detail sheets (1:10,000) | 39,322.5 | 91.1 % |
| Title map (1:45,000, `lpa_map`, coarse) | 1,966.7 | 4.6 % |
| Not coloured on the plan | 1,868.3 | 4.3 % |
| **Total** | **43,157.5** | LPA 43,157.9 ha, diff -0.001 % (pass) |

## 5. Authority rows

`build_authority_all.py`: a village gets an Anekal entry where ≥ 5 % of it lies inside the
plan's LPA; 209 full, 18 partial.
- In Anekal taluk, **BDA keeps the villages it covers fully** (21 villages: no Anekal entry);
  villages partly in both list both, each with its own plan and status (4 villages).
- 57 Anekal villages are also in the STRR LPA (constituted 2021): both are listed, Anekal with
  the note "Area moved to the STRR LPA (GO NAI 89 BMR 2021) after this plan was made; whether
  the plan still applies here is not confirmed".
- Overlaps (docs/plans/2031-seams.md): Anekal ∩ STRR 14,224.6 ha, BDA ∩ Anekal 813.8 ha (both
  plans' hits returned, never merged), Anekal ∩ Hoskote 80.3 ha.

## 6. Run notes

- Two GEOS access violations in the merge (union of the footprints cut on the title map):
  that cut now runs on the footprints' 1 cm grid (< 1 cm change). Merge resumes per sheet.
- Map No. 60 picked up in round 1 and removed; hatched classes dropped (round 2); road greys
  added as transport keys (round 3).
- Footprint of a merged sheet: coverage union of the per-class unions (a snapped union of every
  piece crashed GEOS), with a snapped fallback on a topology error.
