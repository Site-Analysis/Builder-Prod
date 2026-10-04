# Plan roads (ROW) on the 2031 map: sources and QA (4 Oct 2026)

Road layers for the three plans on the map, published gzip to the public Supabase bucket
`planning-tiles` (`roads/<plan>.<stamp>.geojson.gz`, listed under `roads` in `manifest.json`).
QA summaries are in `infra/planning/layer_index.json` → `roads.<plan_id>`. Decisions #72-#81.

| | Anekal | Hoskote | BDA |
|---|---|---|---|
| Source | Mobility Plan, 23 sheets 1:10,000 (`BMRDA-ANK-MP2031-MOB-10K`, status not stated) | Master Plan atlas (`BMRDA-HSK-MP2031-MP`, final): 45 detail sheets + 6 circulation sheets + 6 hobli maps | RMP 2031 PLUCOMP composite (`BDA-RMP2031-PLUCOMP`, draft) |
| Width from | labels ("18m", bare numbers), red ROW edges drawn to scale, grey bands | bold numbers in circles; red vector ROW edges to scale (detail sheets) | labels along vector centrelines (lines not to scale) |
| Placement | one grid fit for all sheets on cadastral parcel edges (residual median 2.3 m); OSM: 63.6 % of OSM centreline points inside the corridors vs 2.5 % shifted, offset ~1 m | each sheet's zone grid fit (UTM grid labels on the sheets) | PLUCOMP affine (RMSE 10 m) + generalisation: ±30 m |
| Drawn vs label | median +0.2 m, 98.5 % within 2 m (56 km) | median -0.7 m, 93 % within 2 m (759 pieces) | n/a (labels only; snap median 1.9 pt, angle 1.2°) |
| Km with a width | 472 (labelled / drawn 390, small roads as drawn 83) | 670 | 3,096 of 3,743 km network |
| Confidence | HIGH 309 km, MEDIUM 163 km | HIGH 251 km, MEDIUM 419 km | HIGH 2,216 km, MEDIUM 881 km |
| Published | 0.9 MB gzip (5.4 MB raw), 9,122 features | 1.1 MB gzip (7.6 MB raw), 16,154 features | 1.6 MB gzip (9.6 MB raw), 18,772 features |
| Spot check | 19 / 20 right (one stub across the STRR band) | about 13 / 20 clean; the rest short stubs at junctions, 1-2 off-centre by a few metres | labels sit on the right line in 20 / 20 crops |

## Hoskote vs the Master Plan report (Table 76, km of planned roads by ROW)

| ROW | 9 | 12 | 15 | 18 | 24 | 30 | 45 | 60 | 80 | 90 |
|---|---|---|---|---|---|---|---|---|---|---|
| Report | 1.0 | 76.5 | 6.3 | 217.5 | 205.2 | 84.9 | 45.8 | 15.4 | 16.8 | 66.5 |
| Extracted | 36.8 | 95.0 | 28.2 | 164.1 | 131.7 | 46.1 | 20.0 | 9.2 | 21.8 | 37.4 |

Main roads come out at 50-75 % of the report's length: the report counts the whole network,
the layer only roads a label reaches or the detail sheets draw. 9 and 15 m are over the report:
narrow red-edged lanes on detail sheets that the report's main-road table does not list.

## Existing width (parcel card)

Declared width first; else a MEDIUM cadastral-gap estimate; the plan's drawn band and LOW
estimates are shown, not used (#76, #78). In practice the card asks for the measured width.
