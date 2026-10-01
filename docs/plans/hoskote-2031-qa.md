# Hoskote Master Plan 2031: extraction QA

Plan `BMRDA-HSK-MP2031`, status final, condition "subject to the High Court's final judgment in
W.P. 4188/2016" (GO UDD 152 BMR 2013, 30-01-2018). Source: `BMRDA-HSK-MP2031-MP` (atlas, 87 pages).
Built by `infra/scripts/planning/extract_hoskote.py` on 2026-10-01; authority rows by
`build_authority_hsk.py`. Output: `<data-root>/planning/zones/BMRDA-HSK-MP2031.parquet`
(1,762,246 polygons), `_lpa.parquet`, `_qa.json`.

Self-checked. SME pending.

## 1. Land use vs the plan report (Tables 66 + 67)

Tables 66 (inside the conurbation) and 67 (outside) cover the LPA without the STRR and
Nandagudi: 39,921 ha. Our LPA (Map 19 dashed boundary) is 46,936 ha (report: 47,410 ha).

| Class | Table 66 + 67 (ha) | Extracted (ha) | Diff |
|---|---:|---:|---:|
| Residential | 4,429.01 | 4,390.46 | -0.9% |
| Commercial | 473.24 | 471.62 | -0.3% |
| Industrial | 2,684.27 | 2,675.88 | -0.3% |
| Public & semi-public | 292.40 | 295.20 | +1.0% |
| Park & open space | 972.54 | 1,041.79 | +7.1% |
| Public utility | 67.87 | 70.41 | +3.7% |
| Transportation | 2,729.02 | 3,976.91 | **+45.7%** |
| Agriculture | 21,350.00 | 19,818.72 | -7.2% |
| Forest | 2,878.72 | 0 | **no forest class extracted** |
| Unclassified | 245.09 | 1,061.95 | **+333%** |
| Water body | 3,799.19 | 3,523.57 | -7.3% |
| Not coloured on the plan | — | 9,607.62 | see 3 |
| **Total** | **39,921.35** | **46,934.11** | |

Findings (not fixed: each changes outputs, needs sign-off):
- **Forest is missing.** The raster palette has no forest colour, so forest land went to
  other classes or to "not coloured". Agriculture + forest in the tables: 24,229 ha; extracted
  agriculture: 19,819 ha.
- **Transportation +1,248 ha.** Not yet traced. Likely causes: road bands on the coarse hobli
  maps (1:21k–1:35k) drawn wider than the roads, and proposed widenings on the detail sheets.
- **Unclassified +817 ha.** Not yet traced. The pale grey (#e1e1e1) may also be matching
  other neutral tints.
- Residential, commercial, industrial, PSP, public utility: within ±4%.

## 2. Georeferencing and OSM junction check

Each sheet is fitted to its printed UTM 43N grid (residual 0.1–1.6 m). Ground check: road
junctions on the sheet (skeleton of the TRANSPORTATION class) vs OSM road junctions, matched only
within 10 m and with the same degree. No refit. Pass bar: RMSE ≤ 10 m.

- **Floor** = median RMSE of the 37 well-matched detail sheets (≥ 3 matches): **6.62 m**. Used for
  sheets with < 3 matches ("few ground checks") and for every hobli map.
- Position uncertainty = sqrt(georef² + m/px²).
- **No sheet is above 10 m.**

| Map | Layer | Scale | m/px | Grid res (m) | Matched / sheet junctions | OSM RMSE (m) | Georef used (m) | Uncertainty (m) | Flags |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 50 | detail | 1:5,000 | 0.85 | 0.5 | 125/448 | 6.5 | 6.5 own | 6.6 | |
| 51 | detail | 1:5,000 | 0.85 | 0.3 | 70/404 | 6.7 | 6.7 own | 6.8 | |
| 52 | detail | 1:5,000 | 0.85 | 0.4 | 187/670 | 5.9 | 5.9 own | 6.0 | |
| 23 | detail | 1:10,000 | 1.69 | 0.4 | 42/397 | 7.1 | 7.1 own | 7.3 | |
| 24 | detail | 1:10,000 | 1.69 | 0.1 | 17/216 | 6.6 | 6.6 own | 6.8 | |
| 25 | detail | 1:10,000 | 1.69 | 0.7 | 9/275 | 6.2 | 6.2 own | 6.5 | |
| 28 | detail | 1:10,000 | 1.69 | 0.4 | 51/941 | 6.6 | 6.6 own | 6.8 | |
| 29 | detail | 1:10,000 | 1.69 | 0.4 | 4/430 | 5.2 | 5.2 own | 5.4 | |
| 30 | detail | 1:10,000 | 1.69 | 0.4 | 4/637 | 8.1 | 8.1 own | 8.3 | |
| 31 | detail | 1:10,000 | 1.69 | 0.1 | 7/642 | 5.4 | 5.4 own | 5.7 | |
| 32 | detail | 1:10,000 | 1.69 | 0.3 | 9/853 | 6.5 | 6.5 own | 6.8 | |
| 33 | detail | 1:10,000 | 1.69 | 0.4 | 2/242 | 2.4 | 6.6 floor | 6.8 | few ground checks |
| 34 | detail | 1:10,000 | 1.69 | 0.6 | 6/685 | 7.6 | 7.6 own | 7.8 | |
| 35 | detail | 1:10,000 | 1.69 | 0.2 | 2/2076 | 2.3 | 6.6 floor | 6.8 | few ground checks |
| 36 | detail | 1:10,000 | 1.69 | 0.7 | 5/315 | 7.6 | 7.6 own | 7.8 | |
| 39 | detail | 1:10,000 | 1.69 | 0.4 | 0/349 | — | 6.6 floor | 6.8 | few ground checks |
| 40 | detail | 1:10,000 | 1.69 | 0.1 | 4/434 | 5.4 | 5.4 own | 5.6 | |
| 41 | detail | 1:10,000 | 1.69 | 0.4 | 0/182 | — | 6.6 floor | 6.8 | few ground checks |
| 42 | detail | 1:10,000 | 1.69 | 0.4 | 7/791 | 8.1 | 8.1 own | 8.3 | |
| 43 | detail | 1:10,000 | 1.69 | 0.4 | 5/243 | 7.0 | 7.0 own | 7.2 | |
| 44 | detail | 1:10,000 | 1.69 | 0.4 | 2/120 | 4.4 | 6.6 floor | 6.8 | few ground checks |
| 45 | detail | 1:10,000 | 1.69 | 0.7 | 1/183 | 1.0 | 6.6 floor | 6.8 | few ground checks |
| 48 | detail | 1:10,000 | 1.69 | 0.4 | 12/212 | 6.9 | 6.9 own | 7.1 | |
| 49 | detail | 1:10,000 | 1.69 | 0.4 | 225/994 | 6.2 | 6.2 own | 6.5 | |
| 53 | detail | 1:10,000 | 1.69 | 0.4 | 8/850 | 4.9 | 4.9 own | 5.2 | |
| 54 | detail | 1:10,000 | 1.69 | 0.4 | 12/999 | 7.0 | 7.0 own | 7.2 | |
| 55 | detail | 1:10,000 | 1.69 | 0.6 | 26/527 | 5.9 | 5.9 own | 6.1 | |
| 56 | detail | 1:10,000 | 1.69 | 0.4 | 106/1036 | 6.5 | 6.5 own | 6.7 | |
| 59 | detail | 1:10,000 | 1.69 | 0.9 | 18/407 | 8.0 | 8.0 own | 8.2 | |
| 60 | detail | 1:10,000 | 1.69 | 0.4 | 9/417 | 6.4 | 6.4 own | 6.6 | |
| 61 | detail | 1:10,000 | 1.69 | 0.4 | 12/1040 | 6.5 | 6.5 own | 6.7 | |
| 62 | detail | 1:10,000 | 1.69 | 0.4 | 2/587 | 7.9 | 6.6 floor | 6.8 | few ground checks |
| 63 | detail | 1:10,000 | 1.69 | 0.9 | 11/129 | 5.1 | 5.1 own | 5.4 | |
| 64 | detail | 1:10,000 | 1.69 | 1.6 | 3/183 | 6.4 | 6.4 own | 6.6 | |
| 65 | detail | 1:10,000 | 1.69 | 0.4 | 4/268 | 6.7 | 6.7 own | 6.9 | |
| 66 | detail | 1:10,000 | 1.69 | 1.0 | 0/329 | — | 6.6 floor | 6.8 | few ground checks |
| 67 | detail | 1:10,000 | 1.69 | 0.3 | 4/358 | 7.0 | 7.0 own | 7.2 | |
| 68 | detail | 1:10,000 | 1.69 | 0.6 | 8/909 | 8.2 | 8.2 own | 8.4 | |
| 69 | detail | 1:10,000 | 1.69 | 0.4 | 9/435 | 7.5 | 7.5 own | 7.7 | |
| 70 | detail | 1:10,000 | 1.69 | 0.2 | 3/206 | 6.9 | 6.9 own | 7.1 | |
| 71 | detail | 1:10,000 | 1.69 | 0.4 | 14/223 | 6.3 | 6.3 own | 6.5 | |
| 74 | detail | 1:10,000 | 1.69 | 1.2 | 14/521 | 7.5 | 7.5 own | 7.7 | |
| 75 | detail | 1:10,000 | 1.69 | 0.6 | 26/358 | 7.0 | 7.0 own | 7.2 | |
| 76 | detail | 1:10,000 | 1.69 | 0.6 | 8/535 | 6.6 | 6.6 own | 6.8 | |
| 77 | detail | 1:10,000 | 1.69 | 0.7 | 9/132 | 6.4 | 6.4 own | 6.6 | |
| 21 | hobli (Hoskote) | 1:15,000 | 2.54 | 1.0 | 58/665 | 7.2 | 6.6 floor | 7.1 | |
| 46 | hobli (Hoskote) | 1:21,000 | 3.56 | 1.1 | 180/2286 | 6.7 | 6.6 floor | 7.5 | |
| 72 | hobli (Anugondanahalli) | 1:30,000 | 5.08 | 1.3 | 31/617 | 7.0 | 6.6 floor | 8.3 | |
| 26 | hobli (Sulibele) | 1:32,000 | 5.42 | 0.8 | 14/893 | 6.5 | 6.6 floor | 8.6 | |
| 37 | hobli (Nandagudi) | 1:32,000 | 5.42 | 1.0 | 20/1403 | 7.6 | 6.6 floor | 8.6 | |
| 57 | hobli (Jadigenahalli) | 1:35,000 | 5.93 | 1.1 | 30/1503 | 7.1 | 6.6 floor | 8.9 | |

8 detail sheets have "few ground checks" (33, 35, 39, 41, 44, 45, 62, 66). Sheets were classified
on the 150 dpi grid (300 dpi strips capped): 1.69 m/px at 1:10,000.

## 3. Coverage by layer

Detail sheets win where both exist. Anything covered by neither = "Not coloured on the plan".

| Layer | Area (ha) | Share of LPA |
|---|---:|---:|
| Detail sheets (1:5,000 / 1:10,000) | 35,646.7 | 75.9% |
| Hobli maps (1:15,000–1:35,000) | 1,679.8 | 3.6% |
| Not coloured on the plan | 9,607.6 | 20.5% |
| **Total** | **46,934.1** | LPA 46,936.2 ha, diff -0.004% (pass ≤ 0.5%) |

"Not coloured" breakdown: 8 blocks over 100 ha = 7,712.5 ha (the largest: 6,622.9 ha around
13.20 N, 77.88 E, i.e. Nandagudi). That matches the 7,489 ha the report leaves out of
Tables 66/67 (STRR + Nandagudi). The other 1,895 ha are 427k small gaps (labels, lines, symbols):
424k under 100 m² (432 ha).

Polygons per sheet were dissolved by class after the priority cut, with no size filter in the dissolve:
1,334,568 → 1,334,946. The count rises slightly because snapping to the 1 cm grid splits some
pieces at pinch points. Cut slivers below half a pixel are dropped, as before.

## 4. Authority rows (`infra/planning/authority_villages.csv`)

- Annexure-1 (MPR pp. 280–287): 289 names, 243 matched to cadastral villages, 26 unmatched (mostly
  "Plantation (B)" uninhabited villages and merged names in the PDF text).
- Spatial share vs the Hoskote LPA, on dist 21 (Hoskote taluk) and dist 20 Bidarahalli hoblis.
- 306 rows written, only where BDA coverage was none: **257 full, 22 partial, 27 none** (listed
  but < 2% in the LPA: STRR note). No BDA row changed.
- **Conflicts with BDA (left BDA):** 3 villages BDA has as full that also overlap the Hoskote LPA by
  5–7%: Chikkanekkundi (20/3/9/15) 6.6%, Valepura (20/4/4/19) 7.2%, Baiyyappanahalli
  (20/4/7/10) 5.4%.

## 5. BDA overlaps

- BDA LPA ∩ Hoskote LPA: 72.8 ha (a sliver along the shared edge).
- Hoskote colouring inside the BDA LPA: ≤ 21.4 ha (Maps 21, 23, 25, 46, 48, 49, 72, 74, 75).
  Both plans return there.

## 6. Service check over HTTP

Planning (8012) and cadastral (8011) run on their own (no Docker), flags
`feature.planning.layers feature.planning.plan.BDA-RMP2031 feature.planning.plan.BMRDA-HSK-MP2031`.
`audit_service.py --steps http`: 200 sampled parcels (117 BDA, 83 Hoskote), real `/zones/at` and
`/authority` calls: **400/400 HTTP 200**, `/zones/at` p50 2.9 s, p95 3.1 s.

The Hoskote responses carry status final + `status_condition`; `sheets_qa[].sheet_scale`
gives the source scale. `source_layer` and `sheet` are in the parquet but not in the API
(would need a contract bump).

## 7. Run notes (fixes during the build; none changes outputs)

- Merge rewritten to work one sheet at a time with per-sheet checkpoints (`merged_NN.parquet`,
  `foot_NN.wkb`), only footprint boxes in memory, footprints read on demand. Resumable.
- Final write concatenates the per-sheet files (no global union of pieces).
  "Not coloured" = LPA minus the footprints on 2 km tiles, dissolved back across the seams.
- Silent deaths were native GEOS crashes (access violation), not memory. Causes found by running
  with `python -X faulthandler`: `clip_by_rect` output and mixed collections passed to
  `union_all`. Fixed by clipping with `intersection` and keeping polygon parts only.
- The LPA polygon carried a stray 10.8 m LineString from chaining the dash loops (zero area).
  It is now polygon parts only.
- Long runs launched through WMI (`Win32_Process.Create`) so a session exit doesn't kill them.
