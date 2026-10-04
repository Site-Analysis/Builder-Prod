# Anekal LPA Master Plan 2031: extraction QA

Plan `BMRDA-ANK-MP2031`, status **final**, GO UDD 151 BMR 2013, 03-09-2014 (verified on the
sheet stamps). Source: `BMRDA-ANK-MP2031-MP` ("Anekal MP.pdf", 22 raster sheets at 110 ppi;
URL unverified, from the "DTCP Docs" Drive folder). Built by
`infra/scripts/planning/extract_anekal.py` + `raster_plan.py` (2 Oct 2026). Output:
`<data-root>/planning/zones/BMRDA-ANK-MP2031.parquet` (729,417 polygons, round 4), `_lpa.parquet`,
`_qa.json` (rounds 1-3 kept as `_qa_v1.json` .. `_qa_v3.json`; round 3 zones as `_v3.parquet`).

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
per class, total 40,230.03 ha. Round 4 compares on two extents (`compare_ank_table.py`):
the whole extracted LPA (title map, 43,158 ha) and **the plan's own extent** (BMRDA's pre-STRR
Anekal LPA, 40,464 ha, which the table's 40,230 ha refers to; see 2.3). The ±10 % bar is read
on the plan's extent.

| Class | Plan table (ha) | Round 1 | Round 2 | Round 3 | **Round 4** (title extent) | Round 4 diff | Round 3, plan extent | **Round 4, plan extent** | Round 4 diff | ±10 %? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Residential | 11,230.69 | 9,588.75 | 10,940.52 | 10,715.70 | **11,626.9** | +3.5 % | 10,689.0 (-4.8 %) | **11,596.3** | +3.3 % | yes |
| Commercial | 768.32 | 590.58 | 673.61 | 585.91 | **706.6** | -8.0 % | 585.1 (-23.8 %) | **705.8** | -8.1 % | yes |
| Industrial | 5,099.95 | 4,558.33 | 4,991.39 | 4,708.79 | **5,025.6** | -1.5 % | 4,689.3 (-8.1 %) | **5,004.4** | -1.9 % | yes |
| Public & semi-public | 840.27 | 754.36 | 793.13 | 777.13 | **813.3** | -3.2 % | 774.2 (-7.9 %) | **810.3** | -3.6 % | yes |
| Park & open space | 2,003.78 | 1,684.85 | 1,780.59 | 1,766.16 | **2,188.6** | +9.2 % | 1,761.2 (-12.1 %) | **2,181.7** | +8.9 % | yes |
| Public utility | 31.16 | 2,196.74 | not extracted | not extracted | not extracted | | | not extracted | | (hatched) |
| Agriculture | 10,891.44 | 10,612.11 | 11,205.88 | 10,621.65 | **12,301.1** | +12.9 % | 9,906.2 (-9.0 %) | **11,542.2** | +6.0 % | yes |
| Water bodies | 2,147.11 | 2,402.15 | 2,462.14 | 2,397.66 | **2,597.6** | +21.0 % | 2,378.5 (+10.8 %) | **2,576.1** | +20.0 % | **no** (2.2) |
| Forest | 2,126.92 | 2,888.14 | 4,876.18 | 2,823.07 | **2,334.2** | +9.7 % | 2,790.7 (+31.2 %) | **2,304.7** | +8.4 % | yes |
| Hillocks / quarries | 293.29 | 3,361.73 | not extracted | not extracted | not extracted | | | not extracted | | (hatched) |
| Transportation | 3,943.22 | 2,603.70 | 3,242.17 | 6,893.12 | **3,651.7** | -7.4 % | 5,932.3 | **2,755.3** | -30.1 % | cartographic |
| Not coloured on the plan | — | 1,915.97 | 2,191.93 | 1,868.30 | **1,912.1** | | 933.2 | **963.2** | | |

Public utility and hillocks / quarries are "not extracted (hatched)", not failures (2.4).

### 2.1 Round 4: what the random-pixel QA found, and the fixes

`qa_pixel_sample.py`: 200 random pixels per failing class (and transport) across the 16 detail
sheets, classified exactly as the pipeline does, shown as 33 x 33 px crops; checked by eye and by
the sampled pixel's own colour. Legend swatches in CIELAB (median over the 16 sheets): the
closest pairs are hillock-hatch grey vs road grey (ΔE 3.8), transport vs agriculture (15.1),
transport vs road grey (21.1), agriculture vs water (22.6); forest vs park 36.9, commercial vs
water 61.7 (not confusable by swatch).

| Class | Off-class samples (round 3) | What they were | Fix (round 4) |
|---|---:|---|---|
| Forest | 51 / 200 (25.5 %) | park-green zones and green tank buffers (single JPEG pixels fall 17-38 from the forest swatch, nearer than to park); dark text, symbols, brown dots | forest vs park decided on the colour averaged over the green pixels of a 7 x 7 window (~23 m); forest only where G - max(R, B) ≥ 15 (achromatic / dark pixels are filled from neighbours) |
| Water | 6 / 200 (3 %) by colour (by eye ~13 % near-white gaps, all filled back as water) | near-white gaps, text | water only where B - R ≥ 20 (no measurable effect) |
| Commercial | ~6 % | precision is high; the deficit is commercial plots cut by thin grey plot lines that went to transport | see transport |
| Park | ~12 % | white roads / text inside parks | (forest vs park rule) |
| Transport | 128 / 200 not road bands (64 %) | thin grey lines (plot, survey, village boundaries), text, hatch strokes | transport kept only where it survives a 3 x 3 opening (bands ≥ 3 px ≈ 10 m); thin strokes are filled from neighbours. The OSM junction check still runs on the unrefined road mask (same matches and RMSE as round 3) |

Each fix was also run on its own (sheet level, unclipped): the transport opening is what lifts
commercial into the band (-24 % to -8 %) and it also raises water (+7 %) and agriculture; the
forest / park rule takes forest from +31 % to +8 % and park from -12 % to +9 %; the forest and
water colour checks move little.

### 2.2 Still outside ±10 %: water, +20 % (SME pending, default applied)

Measured cause:
- Water precision is high: 97 % of 200 random water pixels are water-coloured.
- Round 3 was already +10.8 % on the plan extent. The round-4 increase (+198 ha) is the thin grey
  survey / bund lines drawn across tanks: no longer transport, they fill from the tank they cross.
- With the transport opening switched off, water stays at +10.8 % but commercial falls back to
  -23.5 %: either way one class is outside. Default applied: keep the opening (every other class
  within ±10 %).
- So the excess is in what the map colours as water against the table's water figure (likely
  notified tank areas), not in the extraction. Open-decisions #19.

### 2.3 Plan-total gap: 43,158 vs 40,230 ha (+2,928 ha)

| Part | ha |
|---|---:|
| Title-map extent outside BMRDA's pre-STRR Anekal LPA (margin) | +2,717.7 |
| of which within 100 / 250 / 500 m of BMRDA's edge | 1,085 / 1,417 / 1,651 |
| of which, by class: transport (title map's boundary band) 961, not coloured 935, agriculture 715, other 106 | |
| of which inside other LPAs: BDA 836, STRR 465, Kanakapura 349, Hoskote 56, BMICAPA 54 | |
| BMRDA pre-STRR LPA not in the title extent | -24.1 |
| BMRDA pre-STRR (40,464.2) vs the table (40,230.0): unexplained | +234.2 (0.6 %) |
| STRR band | 0: the STRR band (14,225 ha) lies inside the plan's own extent, not in the gap |
| **Total** | **+2,927.8** |

### 2.4 Hatched classes inside the over-target classes

Public utility and hillocks / quarries (324 ha in the table) are not extracted. A periodic-stroke
detector (strokes with ≥ 4 transitions per row in a 15 x 15 px window over the class) finds 24 ha
inside forest and 7 ha inside water, and the crops show these are text and road lines, not
hatch. So the hatched classes do not explain the forest or water excess (forest is now within the
band without them).

Earlier rounds:
- Round 3: the map draws road bands **mid-grey**; the legend's transportation swatch is a light
  grey (#ddd8d5). Mid-grey went to the hillock hatch key in round 1 and to forest in round 2;
  round 3 added the road greys (#a0a0a0, #959899, #8c8c8c) as transport keys.
- Round 3 transport (+75 %) included all grey linework; round 4's opening removes it.

LPA: the title map's coloured extent (closed 60 m, holes filled) is **43,157.9 ha**. Area check:
zones 43,157.6 ha vs LPA 43,157.9 ha (-0.001 %, pass). Polygons: 729,417.

## 3. Georeferencing and OSM check

Georeference: primeocr's margin-label affine (3.3 m/px on the 1:10,000 sheets). OSM check:
Overpass was unavailable on 1-2 Oct (504 / 429 on every mirror), so junctions were checked
against the cached OSM extracts of 30 Sep (motorway to tertiary only, 11,841 junctions;
open-decisions #15). With the road bands now transport, **13 of the 16 detail sheets have ≥ 3
matches** (round 2: 4), RMSE 5.9-8.2 m: an independent check that supports the margin-label
georeference.

- **Floor** = median RMSE of the 13 well-matched detail sheets: **7.02 m**.
- **No sheet above 10 m.**

| Sheet | Layer | Scale | m/px | Matched / sheet junctions | OSM RMSE (m) | Georef used (m) | Uncertainty (m) | Unknown px % (round 4) | Flags |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Map No. 41 (Sarjapura SP-1) | detail | 1:10,000 | 3.30 | 3/2887 | 5.9 | 5.9 own | 6.8 | 11.0 | |
| Map No. 42 (Sarjapura SP-2) | detail | 1:10,000 | 3.30 | 2/1964 | 7.6 | 7.0 floor | 7.8 | 6.5 | few ground checks |
| Map No. 43 (Sarjapura SP-3) | detail | 1:10,000 | 3.29 | 11/2620 | 7.8 | 7.8 own | 8.5 | 8.8 | |
| Map No. 45 (Attibele AT-1) | detail | 1:10,000 | 3.32 | 7/4030 | 8.2 | 8.2 own | 8.9 | 10.7 | |
| Map No. 46 (Attibele AT-2) | detail | 1:10,000 | 3.30 | 5/5052 | 6.2 | 6.2 own | 7.0 | 15.1 | |
| Map No. 47 (Attibele AT-3) | detail | 1:10,000 | 3.33 | 12/4969 | 7.6 | 7.6 own | 8.3 | 13.4 | |
| Map No. 49 (Jigani JI-1) | detail | 1:10,000 | 3.33 | 8/3912 | 7.2 | 7.2 own | 8.0 | 11.4 | |
| Map No. 50 (Jigani JI-2) | detail | 1:10,000 | 3.31 | 12/3372 | 7.5 | 7.5 own | 8.2 | 12.1 | |
| Map No. 51 (Jigani JI-3) | detail | 1:10,000 | 3.31 | 3/4606 | 6.2 | 6.2 own | 7.0 | 10.2 | |
| Map No. 52 (Jigani JI-4) | detail | 1:10,000 | 3.35 | 1/5983 | 8.2 | 7.0 floor | 7.8 | 5.4 | few ground checks |
| Map No. 54 (Anekal AN-1) | detail | 1:10,000 | 3.29 | 11/7381 | 6.5 | 6.5 own | 7.3 | 12.2 | |
| Map No. 55 (Anekal AN-2) | detail | 1:10,000 | 3.28 | 8/4387 | 7.0 | 7.0 own | 7.7 | 12.5 | |
| Map No. 56 (Anekal AN-3) | detail | 1:10,000 | 3.30 | 14/4531 | 7.0 | 7.0 own | 7.7 | 11.6 | |
| Map No. 57 (Anekal AN-4) | detail | 1:10,000 | 3.31 | 4/3220 | 7.8 | 7.8 own | 8.5 | 8.5 | |
| Map No. 58 (Anekal AN-5) | detail | 1:10,000 | 3.31 | 1/2698 | 3.6 | 7.0 floor | 7.8 | 4.4 | few ground checks |
| Map No. 59 (Anekal AN-6) | detail | 1:10,000 | 3.31 | 7/3972 | 6.8 | 6.8 own | 7.6 | 8.2 | |
| Map No. 39 (title map) | lpa_map | 1:45,000 | 14.86 | 3/11219 | 5.6 | 7.0 floor | 16.4 | 7.9 | |

## 4. Coverage by layer

| Layer | Area (ha) | Share of LPA |
|---|---:|---:|
| Detail sheets (1:10,000) | 38,783.7 | 89.9 % |
| Title map (1:45,000, `lpa_map`, coarse) | 2,461.8 | 5.7 % |
| Not coloured on the plan | 1,912.1 | 4.4 % |
| **Total** | **43,157.6** | LPA 43,157.9 ha, diff -0.001 % (pass) |

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
- Round 4: forest / park, forest and water colour checks, transport opening (2.1). The unknown
  pixel share rises by 2-4 points (filled from neighbours); OSM junction matches are unchanged
  because the check uses the unrefined road mask.
- Footprint of a merged sheet: coverage union of the per-class unions (a snapped union of every
  piece crashed GEOS), with a snapped fallback on a topology error.

## 7. Round of 2-3 Oct 2026: served from the official Map No. 39 (vector)

The 22-sheet atlas above can no longer be re-fetched: its Drive folder now needs a Google
sign-in (open-decisions #39), so no sheet is indexed from it and the round-4 layer is not
served. Anekal is served instead from `BMRDA-ANK-MP2031-MAP39`, the TPA's own
`39 Proposed Landuse Consolidated.pdf` (anekal.tpa.gov.in, 45.6 MB, sha256 5d7df17a…), a GIS
export where each land-use area is a vector fill (#44).

Method (`vector_fill_sheet`, index row `BMRDA-ANK-MP2031-MAP39#map39`):
- every fill inside the neatline (page box 154, 71, 2746, 2302) whose colour is a legend class
  is one zone; later fills cover earlier ones (paint order); callout / symbol / text-box fills
  are skipped, and so is `#e6e600` (drawn on the map, not in the legend);
- page points to EPSG:32643 by the sheet's own grid-label fit (E = 15.8772 x + 769,760.8,
  N = -15.8759 y + 1,434,916.1; residual 1.1-1.5 m);
- clipped to BMRDA's pre-STRR Anekal extent + 100 m (row `STRR-LPA-MAP#ank`, 404.64 km²,
  from the BMRDA LPA map). The frame strip and grid labels of #32 / L4 are outside the
  neatline, so they are never zones; 1.8 ha of fills fell outside the clip;
- one sheet, so there is no sheet seam (L3);
- LPA area with no fill is "Not coloured on the plan" (mostly roads, which Map 39 draws as
  lines, not fills).

Legend labels are read from the sheet: 7 of 9 found next to their swatch (RESIDENTIAL,
COMMERCIAL, INDUSTRIAL, PUBLIC & SEMI PUBLIC, PARK & OPEN SPACE, AGRICULTURE, WATER BODIES).
Transport and forest have no plain swatch on the legend, so their labels ("Transportation",
"Forest") are ours, and the legend check is `warn`.

Class check against the plan's table, on the clip extent (ha):

| Class | Map No. 39 (vector) | Plan table | Diff | Round 4 (raster, plan extent) |
|---|---:|---:|---:|---:|
| Residential | 11,186.6 | 11,230.69 | -0.4 % | 11,596.3 |
| Commercial | 779.1 | 768.32 | +1.4 % | 705.8 |
| Industrial | 5,159.9 | 5,099.95 | +1.2 % | 5,004.4 |
| Public & semi-public | 845.1 | 840.27 | +0.6 % | 810.3 |
| Park & open space | 2,314.5 | 2,003.78 | +15.5 % | 2,181.7 |
| Agriculture | 9,903.3 | 10,891.44 | -9.1 % | 11,542.2 |
| Water bodies | 2,903.6 | 3,000.99 | -3.2 % | 2,576.1 |
| Forest | 2,096.4 | 2,126.92 | -1.4 % | 2,304.7 |
| Transportation | 2,540.1 | 3,943.22 | -35.6 % | 2,755.3 |

- **#19 re-checked:** the table lists water twice (2,147.11 + 853.88 = 3,000.99 ha). Round 4's
  "+20 %" was measured against 2,147.11 alone; against 3,000.99 round 4 is -14.2 % and the
  vector map is -3.2 %.
- Transport is low because roads are drawn as lines (the gap is in "Not coloured on the plan").
  Park is high, agriculture low, by about the same area (~300-990 ha): the plan's table and its
  own map disagree; not an extraction effect, since every fill is taken as drawn.
- Step B's bar "every class within 0.5 % of round 4" does not apply: the source changed (#44).
- Fit with BMRDA's pre-STRR outline: IoU 0.924 (fills 37,730 ha, outline 40,464 ha). About
  half of the 2,734 ha gap is roads drawn as lines (transport is 1,403 ha under the table).

Placement: the grid-label fit has not had an independent check yet (Overpass was down, #43), so
the sheet is `placement_confirmed: false`, carries the warning "Placement not confirmed by an
independent check; zones may be 100 m or more off. Verify on site." and uses a position
uncertainty of 100 m (#45) until the `--osm-only` pass confirms it.

Build (3 Oct, `build_layer_index.py --plans BMRDA-ANK-MP2031 --no-osm`): 345 s, worker peak
1,098 MB, temp peak 224 MB, 0 bytes left; fetched anekal.tpa.gov.in 45.63 MB and
strrpa.karnataka.gov.in 3.23 MB.

## 8. Plan roads from the Mobility Plan (R1, 4 Oct 2026)

Source: `BMRDA-ANK-MP2031-MOB-10K` (23 sheets, 1:10,000, sha256 `6573467d…`), registered
`reference` (status not stated on the sheets). Built by `infra/scripts/planning/build_roads.py`
(extractor `roads_mob.py`); summary in `layer_index.json` → `roads.BMRDA-ANK-MP2031`.
Decisions #72-#75.

### 8.1 Georeference (no grid printed on the sheets)

| Check | Result |
|---|---|
| Model | 23 sheets = 6 x 5 km cells of one grid (key plan); 2 unknowns E0 / N0 for all |
| Seed | 159 village names on the sheets vs cadastral village points, 121 within 1 km |
| Fit | E0 781,413.0, N0 1,435,698.9 (EPSG:32643); 22 sheets refined on cadastral parcel edges, sheet residual median 2.3 m, max 9.8 m (grid 23 has no Anekal-taluk parcels under it) |
| Per sheet vs null (parcel edges, shifts of 1.5-3.5 km) | match ratio 3.1x - 42.8x, every sheet >= 3x |
| Survey numbers landing in the parcel of that number | 204 of the 383 survey labels that fall inside a parcel (409 read), vs 2.5 for the null shifts |
| Quadrant agreement (rotation / scale) | within 11 m on every sheet (most <= 5 m) |
| OSM (independent) | 63.6 % of OSM centreline points (motorway - tertiary, 2,941 ways) inside the plan corridors vs 2.5 % shifted (25x); median distance to the plan centreline 8.7 m, mean offset (-1.0, +0.7) m: no systematic shift between plan, cadastral and OSM |

### 8.2 Widths

| Check | Result |
|---|---|
| Labelled red-edged roads: drawn ROW (edge spacing) minus label | 331 pieces, 56 km: median +0.2 m, p10 -1.1 m, p90 +1.1 m; 72.8 % within 1 m, 98.5 % within 2 m |
| Ring / radial roads: legend ROW vs ZR "Proposed Building Line" (p. 69) | STRR 90 = 90, ITRR 90 = 90, radial (RR) 60 = 60; building line 6 m. No IRR stroke on the sheets |
| Spot check (20 random labelled pieces, source crop with the extracted centreline and the label-width corridor) | 19 correct; 1 short piece crosses the STRR band at a junction (grid 22) |

### 8.3 What is on the layer

389.6 km of plan roads (ring-road corridors extra): 128.2 km to be widened, 10.9 km proposed,
250.4 km existing roads whose ROW the plan states. By ROW: 18 m 128.6 km, 24 m 78.6 km, 30 m
64.0 km, 45 m 47.8 km, 60 m 20.7 km, 90 m 16.1 km, 12 m 9.3 km, others (incl. drawn-only
widths) < 30 km. Confidence: HIGH 309.4 km, MEDIUM 80.2 km. 925 width labels inside the
sheets' own cells.

Limits: unlabelled existing roads (no plan width) are not on the layer; a bold bare number
is "the plan states its ROW" (the legend does not say existing or proposed); labels claim at
most 600 m along a road, so long unlabelled stretches stay off; ring-road lines are the plan's,
the ZR defers to the BMRDA notified alignments. Worker peak 939 MB (cap 2 GB); temp peak
~250 MB, removed after the build.

### 8.4 Parcel answers and the map (R2-R4, 4 Oct 2026)

Contract `planning` 1.20.0: `/zones/at` returns `abutting_roads` (behind
`feature.planning.roads`) and takes `road_width_m`. Roads published to Supabase Storage
(`roads/BMRDA-ANK-MP2031.20261003T211341Z.geojson`, 4.6 MB, 7,912 features); the map shows
them under "Roads (plan ROW)".

| Check | Result |
|---|---|
| Smoke tests (fixture road 5 m off a 60 x 60 m parcel, 12 m gap across it) | flag off -> null; ROW 18 m, 240 m² inside the ROW, estimate 12.0 m MEDIUM, band over 9 to 12, ZR Table 4 residential FAR 1.75; declared 20 m -> band over 18 to 24, FAR 2.25 and a "more than one band" warning; `road_width_m=0` -> 422. 24 / 24 pass |
| Real parcels, five road types (Menasiganahalli, Kammasandra Agrahara, Chikkahosahalli, A Medihalli, Neraluru) | each lists the right plan roads with ROW, status, frontage and area inside the ROW; ring roads carry the 6 m building line (ZR Table 21); a declared 7 m gives band up to 9 m and Table 4 FAR 1.50 / 1.50 / 1.25 |
| Existing-width estimate on 60 parcels beside plan roads | see open-decisions #76: available for a minority, mostly LOW (not used); the card asks for the measured width |
| Map (Playwright, localhost:3000) | corridors coloured by ROW with labels; popup on a 60 m road; legend card road key; parcel card Roads section with the width box: 7 m entered -> ZR rows shown. No console errors |
| Service memory | 380 MB working set with roads loaded (bar 1 GB) |
