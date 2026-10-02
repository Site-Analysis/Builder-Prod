# Anekal LPA Master Plan 2031: extraction QA

Plan `BMRDA-ANK-MP2031`, status **final**, GO UDD 151 BMR 2013, 03-09-2014 (verified on the
sheet stamps). Source: `BMRDA-ANK-MP2031-MP` ("Anekal MP.pdf", 22 raster sheets at 110 ppi;
URL unverified, from the "DTCP Docs" Drive folder). Built by
`infra/scripts/planning/extract_anekal.py` + `raster_plan.py` (2 Oct 2026). Output:
`<data-root>/planning/zones/BMRDA-ANK-MP2031.parquet` (1,187,469 polygons), `_lpa.parquet`,
`_qa.json` (round 1 kept as `_qa_v1.json`).

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

| Class | Plan table (ha) | Round 1 (ha) | Round 2 (ha) | Round 2 diff | ±10 %? |
|---|---:|---:|---:|---:|---|
| Residential | 11,230.69 | 9,588.75 | 10,940.52 | -2.6 % | yes |
| Commercial | 768.32 | 590.58 | 673.61 | -12.3 % | no (-95 ha) |
| Industrial | 5,099.95 | 4,558.33 | 4,991.39 | -2.1 % | yes |
| Public & semi-public | 840.27 | 754.36 | 793.13 | -5.6 % | yes |
| Park & open space | 2,003.78 | 1,684.85 | 1,780.59 | -11.1 % | no (-223 ha) |
| Public utility | 31.16 | 2,196.74 | not extracted | | (hatched) |
| Agriculture | 10,891.44 | 10,612.11 | 11,205.88 | +2.9 % | yes |
| Water bodies | 2,147.11 | 2,402.15 | 2,462.14 | +14.7 % | no (+315 ha) |
| **Forest** | 2,126.92 | 2,888.14 | **4,876.18** | **+129 %** | **no** |
| Hillocks / quarries | 293.29 | 3,361.73 | not extracted | | (hatched) |
| Transportation | 3,943.22 | 2,603.70 | 3,242.17 | -17.8 % | cartographic |
| Not coloured on the plan | — | 1,915.97 | 2,191.93 | | |

**Forest fails the bar and is flagged for the SME.** 4,742 ha of it lies inside BMRDA's Anekal
extent (so it is not land outside the LPA). Most likely the hillock / quarry hatch areas (on
the hills, next to forest) are now filled from their forest neighbours: round 1 had 2,888 ha
forest + 3,362 ha hillocks. Commercial, park and water are 11-15 % off (95-315 ha), within
what the scanned palette (110 ppi JPEG) and the halo filling move between neighbouring
classes. Treat Anekal forest areas as unconfirmed until checked against the sheets.

LPA: the title map's coloured extent (closed 60 m, holes filled) is **43,157.9 ha**; the plan
table says 40,230 ha; BMRDA's LPA map before STRR gives 40,464 ha (title minus BMRDA: 2,718 ha,
mostly agriculture and not-coloured land at the edge). Area check: zones 43,157.5 ha vs LPA
43,157.9 ha (-0.001 %, pass).

## 3. Georeferencing and OSM check

Georeference: primeocr's margin-label affine (3.3 m/px on the 1:10,000 sheets). OSM check:
Overpass was unavailable on 1-2 Oct (504 / 429 on every mirror), so junctions were checked
against the cached OSM extracts of 30 Sep (motorway to tertiary only, 11,841 junctions;
open-decisions #15). That network is sparse on a 1:10,000 sheet, so most sheets have fewer
than 3 matches. Where sheet junctions do have a major-road junction within 60 m, the median
offset is (-1, -7) m (Map No. 41), which supports the margin-label georeference.

- **Floor** = median RMSE of the 4 well-matched sheets (≥ 3 matches): **6.50 m**.
- **No sheet above 10 m.**

| Sheet | Layer | Scale | m/px | Matched / sheet junctions | OSM RMSE (m) | Georef used (m) | Uncertainty (m) | Unknown px % | Flags |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Map No. 41 (Sarjapura SP-1) | detail | 1:10,000 | 3.30 | 0/615 | — | 6.5 floor | 7.3 | 10.7 | few ground checks |
| Map No. 42 (Sarjapura SP-2) | detail | 1:10,000 | 3.30 | 0/388 | — | 6.5 floor | 7.3 | 6.1 | few ground checks |
| Map No. 43 (Sarjapura SP-3) | detail | 1:10,000 | 3.29 | 14/2185 | 6.6 | 6.6 own | 7.3 | 7.1 | |
| Map No. 45 (Attibele AT-1) | detail | 1:10,000 | 3.32 | 0/983 | — | 6.5 floor | 7.3 | 9.8 | few ground checks |
| Map No. 46 (Attibele AT-2) | detail | 1:10,000 | 3.30 | 2/1044 | 6.6 | 6.5 floor | 7.3 | 14.5 | few ground checks |
| Map No. 47 (Attibele AT-3) | detail | 1:10,000 | 3.33 | 1/1148 | 7.9 | 6.5 floor | 7.3 | 12.8 | few ground checks |
| Map No. 49 (Jigani JI-1) | detail | 1:10,000 | 3.33 | 0/566 | — | 6.5 floor | 7.3 | 10.6 | few ground checks |
| Map No. 50 (Jigani JI-2) | detail | 1:10,000 | 3.31 | 0/416 | — | 6.5 floor | 7.3 | 11.4 | few ground checks |
| Map No. 51 (Jigani JI-3) | detail | 1:10,000 | 3.31 | 0/1345 | — | 6.5 floor | 7.3 | 9.5 | few ground checks |
| Map No. 52 (Jigani JI-4) | detail | 1:10,000 | 3.35 | 2/4821 | 7.0 | 6.5 floor | 7.3 | 3.7 | few ground checks |
| Map No. 54 (Anekal AN-1) | detail | 1:10,000 | 3.29 | 6/6754 | 6.3 | 6.3 own | 7.1 | 8.9 | |
| Map No. 55 (Anekal AN-2) | detail | 1:10,000 | 3.28 | 9/4799 | 6.4 | 6.4 own | 7.2 | 10.3 | |
| Map No. 56 (Anekal AN-3) | detail | 1:10,000 | 3.30 | 0/1007 | — | 6.5 floor | 7.3 | 10.5 | few ground checks |
| Map No. 57 (Anekal AN-4) | detail | 1:10,000 | 3.31 | 1/767 | 3.8 | 6.5 floor | 7.3 | 8.2 | few ground checks |
| Map No. 58 (Anekal AN-5) | detail | 1:10,000 | 3.31 | 1/2595 | 3.6 | 6.5 floor | 7.3 | 2.9 | few ground checks |
| Map No. 59 (Anekal AN-6) | detail | 1:10,000 | 3.31 | 5/3751 | 6.6 | 6.6 own | 7.4 | 5.9 | |
| Map No. 39 (title map) | lpa_map | 1:45,000 | 14.86 | 2/6847 | 2.8 | 6.5 floor | 16.2 | 9.6 | few ground checks |

## 4. Coverage by layer

| Layer | Area (ha) | Share of LPA |
|---|---:|---:|
| Detail sheets (1:10,000) | 39,130.9 | 90.7 % |
| Title map (1:45,000, `lpa_map`, coarse) | 1,834.7 | 4.3 % |
| Not coloured on the plan | 2,191.9 | 5.1 % |
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
- Map No. 60 picked up in round 1 and removed; hatched classes dropped (round 2).
