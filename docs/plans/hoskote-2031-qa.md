# Hoskote Master Plan 2031: extraction QA

Plan `BMRDA-HSK-MP2031`, status final, condition "subject to the High Court's final judgment in
W.P. 4188/2016" (GO UDD 152 BMR 2013, 30-01-2018). Source: `BMRDA-HSK-MP2031-MP` (atlas, 87 pages).
Built by `infra/scripts/planning/extract_hoskote.py` (round 2: 2 Oct 2026); authority rows by
`build_authority_hsk.py` and `build_authority_all.py`. Output:
`<data-root>/planning/zones/BMRDA-HSK-MP2031.parquet` (1,029,504 polygons), `_lpa.parquet`,
`_qa.json`; round 1 QA kept as `_qa_v1.json`.

Self-checked. SME pending.

## 0. Round 2 (2 Oct): what changed and before / after

Three fixes, each with evidence (`docs/plans/open-decisions.md` #1-#3):

1. **Forest added.** The legend swatch is green tree icons on white with a dark green border, not a
   colour fill, so round 1 had no forest class: the icons were "unknown" pixels and the white
   between them "not coloured". Now the icons' raster colour (`#57a634`, median of tree pixels on
   Map 57) is a class; the icon mask is closed over the gaps (20 px at 150 dpi, tuned on Map 57)
   and opened 4 px to drop green linework, and the white / unknown / pale pixels inside become
   FOREST.
2. **Unclassified halos dropped.** Isolated light-grey pixels (≤ 12 of the 49 in a 7×7 window)
   are the anti-aliased edges of black symbols (hillocks, dots) on white: 73-100 % of the
   unclassified pixels on the sampled sheets (Map 24 aside, a real unclassified block). They are
   set unknown and filled from their neighbours.
3. **Transport specks dropped.** Isolated mid-grey pixels (≤ 6 of 49, below a 1 px line, so thin
   roads stay) likewise. Road widths were *measured* and not changed: road bands are 11.5-22.5 m
   on detail sheets and 17-31 m on hobli maps (drawn wider than the road). Transport is
   cartographic for this comparison (not area-comparable).

| Class | Tables 66 + 67 (ha) | Round 1 (ha) | Round 2 (ha) | Round 2 diff | ±10 %? |
|---|---:|---:|---:|---:|---|
| Residential | 4,429.01 | 4,390.46 | 4,425.59 | -0.1 % | yes |
| Commercial | 473.24 | 471.62 | 476.78 | +0.7 % | yes |
| Industrial | 2,684.27 | 2,675.88 | 2,698.59 | +0.5 % | yes |
| Public & semi-public | 292.40 | 295.20 | 299.84 | +2.5 % | yes |
| Park & open space | 972.54 | 1,041.79 | 1,002.85 | +3.1 % | yes |
| Public utility | 67.87 | 70.41 | 70.53 | +3.9 % | yes |
| Agriculture | 21,350.00 | 19,818.72 | 19,660.34 | -7.9 % | yes |
| **Forest** | 2,878.72 | 0 | **2,641.02** | -8.3 % | **yes** |
| Water body | 3,799.19 | 3,523.57 | 3,582.38 | -5.7 % | yes |
| Unclassified | 245.09 | 1,061.95 | 343.93 | +40.3 % | explained (below) |
| Transportation | 2,729.02 | 3,976.91 | 3,120.86 | +14.4 % | cartographic, not compared |
| Not coloured on the plan | — | 9,607.62 | 8,612.46 | | see 3 |

Unclassified: one polygon of 238.6 ha on Map 24 (Bidarahalli) is the plan's unclassified area
(Table 66: 245.09 ha, -2.6 %). The other 105 ha are 35,000 specks (72 ha of them under 100 m²)
too dense to count as isolated halos; 0.2 % of the LPA.

Polygons: 1,762,246 → 1,029,504. Pieces before / after the per-sheet dissolve: 652,527 / 652,658.
Area check: zones 46,935.2 ha vs LPA 46,936.2 ha (-0.002 %, pass).

## 1. Land use vs the plan report (round 1, for the record)


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

## 2. Georeferencing and OSM junction check (round 2)

Each sheet is fitted to its printed UTM 43N grid (residual 0.1–1.6 m). Ground check: road
junctions on the sheet (skeleton of the TRANSPORTATION class) vs OSM road junctions, matched only
within 10 m and with the same degree. No refit. Pass bar: RMSE ≤ 10 m.

- **Floor** = median RMSE of the 33 well-matched detail sheets (≥ 3 matches): **6.85 m** (round 1:
  6.62 m from 37 sheets). Used for sheets with < 3 matches ("few ground checks") and every hobli map.
- Junction counts are lower than in round 1 because forest and the halo specks are no longer
  transport; 12 detail sheets now have "few ground checks" (29, 30, 32, 35, 39, 40, 41, 44, 45, 62, 66, 67).
- **No sheet is above 10 m** (well-matched detail sheets: 4.0–8.6 m).

| Map | Layer | Scale | m/px | Matched / sheet junctions | OSM RMSE (m) | Georef used (m) | Uncertainty (m) | Flags |
|---|---|---|---:|---:|---:|---:|---:|---|
| 50 | detail | 1:5,000 | 0.85 | 118/305 | 6.4 | 6.4 own | 6.5 |  |
| 51 | detail | 1:5,000 | 0.85 | 70/342 | 6.9 | 6.9 own | 6.9 |  |
| 52 | detail | 1:5,000 | 0.85 | 188/499 | 5.9 | 5.9 own | 5.9 |  |
| 23 | detail | 1:10,000 | 1.69 | 32/275 | 7.3 | 7.3 own | 7.5 |  |
| 24 | detail | 1:10,000 | 1.69 | 13/99 | 6.9 | 6.9 own | 7.1 |  |
| 25 | detail | 1:10,000 | 1.69 | 8/213 | 6.0 | 6.0 own | 6.2 |  |
| 28 | detail | 1:10,000 | 1.69 | 52/444 | 6.7 | 6.7 own | 6.9 |  |
| 29 | detail | 1:10,000 | 1.69 | 1/77 | 1.3 | 6.9 floor | 7.1 | few ground checks |
| 30 | detail | 1:10,000 | 1.69 | 2/216 | 6.1 | 6.9 floor | 7.1 | few ground checks |
| 31 | detail | 1:10,000 | 1.69 | 4/206 | 5.4 | 5.4 own | 5.6 |  |
| 32 | detail | 1:10,000 | 1.69 | 2/230 | 3.0 | 6.9 floor | 7.1 | few ground checks |
| 33 | detail | 1:10,000 | 1.69 | 3/42 | 4.0 | 4.0 own | 4.3 |  |
| 34 | detail | 1:10,000 | 1.69 | 6/251 | 6.9 | 6.9 own | 7.1 |  |
| 35 | detail | 1:10,000 | 1.69 | 2/512 | 7.9 | 6.9 floor | 7.1 | few ground checks |
| 36 | detail | 1:10,000 | 1.69 | 3/129 | 7.5 | 7.5 own | 7.7 |  |
| 39 | detail | 1:10,000 | 1.69 | 0/4 | — | 6.9 floor | 7.1 | few ground checks |
| 40 | detail | 1:10,000 | 1.69 | 1/86 | 6.5 | 6.9 floor | 7.1 | few ground checks |
| 41 | detail | 1:10,000 | 1.69 | 0/52 | — | 6.9 floor | 7.1 | few ground checks |
| 42 | detail | 1:10,000 | 1.69 | 4/292 | 7.4 | 7.4 own | 7.6 |  |
| 43 | detail | 1:10,000 | 1.69 | 4/112 | 6.3 | 6.3 own | 6.6 |  |
| 44 | detail | 1:10,000 | 1.69 | 0/32 | — | 6.9 floor | 7.1 | few ground checks |
| 45 | detail | 1:10,000 | 1.69 | 1/73 | 1.0 | 6.9 floor | 7.1 | few ground checks |
| 48 | detail | 1:10,000 | 1.69 | 9/72 | 6.3 | 6.3 own | 6.5 |  |
| 49 | detail | 1:10,000 | 1.69 | 147/509 | 6.2 | 6.2 own | 6.4 |  |
| 53 | detail | 1:10,000 | 1.69 | 8/318 | 6.1 | 6.1 own | 6.4 |  |
| 54 | detail | 1:10,000 | 1.69 | 6/348 | 8.0 | 8.0 own | 8.2 |  |
| 55 | detail | 1:10,000 | 1.69 | 24/222 | 6.0 | 6.0 own | 6.3 |  |
| 56 | detail | 1:10,000 | 1.69 | 76/483 | 6.4 | 6.4 own | 6.6 |  |
| 59 | detail | 1:10,000 | 1.69 | 16/115 | 7.9 | 7.9 own | 8.0 |  |
| 60 | detail | 1:10,000 | 1.69 | 9/149 | 6.8 | 6.8 own | 7.0 |  |
| 61 | detail | 1:10,000 | 1.69 | 12/314 | 7.2 | 7.2 own | 7.4 |  |
| 62 | detail | 1:10,000 | 1.69 | 2/104 | 8.5 | 6.9 floor | 7.1 | few ground checks |
| 63 | detail | 1:10,000 | 1.69 | 7/35 | 5.2 | 5.2 own | 5.5 |  |
| 64 | detail | 1:10,000 | 1.69 | 4/61 | 5.9 | 5.9 own | 6.1 |  |
| 65 | detail | 1:10,000 | 1.69 | 4/90 | 7.4 | 7.4 own | 7.6 |  |
| 66 | detail | 1:10,000 | 1.69 | 0/24 | — | 6.9 floor | 7.1 | few ground checks |
| 67 | detail | 1:10,000 | 1.69 | 1/105 | 6.5 | 6.9 floor | 7.1 | few ground checks |
| 68 | detail | 1:10,000 | 1.69 | 5/296 | 8.3 | 8.3 own | 8.5 |  |
| 69 | detail | 1:10,000 | 1.69 | 6/121 | 6.9 | 6.9 own | 7.1 |  |
| 70 | detail | 1:10,000 | 1.69 | 3/45 | 7.1 | 7.1 own | 7.3 |  |
| 71 | detail | 1:10,000 | 1.69 | 7/73 | 6.3 | 6.3 own | 6.5 |  |
| 74 | detail | 1:10,000 | 1.69 | 9/164 | 7.6 | 7.6 own | 7.8 |  |
| 75 | detail | 1:10,000 | 1.69 | 25/136 | 6.9 | 6.9 own | 7.1 |  |
| 76 | detail | 1:10,000 | 1.69 | 3/161 | 8.6 | 8.6 own | 8.7 |  |
| 77 | detail | 1:10,000 | 1.69 | 9/50 | 6.9 | 6.9 own | 7.1 |  |
| 21 | hobli (Bidarahalli) | 1:15,000 | 2.54 | 45/505 | 7.2 | 6.9 floor | 7.3 |  |
| 46 | hobli (Hoskote) | 1:21,000 | 3.56 | 107/1130 | 6.4 | 6.9 floor | 7.7 |  |
| 72 | hobli (Anugondanahalli) | 1:30,000 | 5.08 | 13/304 | 7.8 | 6.9 floor | 8.5 |  |
| 26 | hobli (Sulibele) | 1:32,000 | 5.42 | 9/294 | 6.5 | 6.9 floor | 8.7 |  |
| 37 | hobli (Nandagudi) | 1:32,000 | 5.42 | 10/584 | 7.0 | 6.9 floor | 8.7 |  |
| 57 | hobli (Jadigenahalli) | 1:35,000 | 5.93 | 21/622 | 6.3 | 6.9 floor | 9.1 |  |

## 3. Coverage by layer

Detail sheets win where both exist. Anything covered by neither = "Not coloured on the plan".

| Layer | Round 1 (ha) | Round 2 (ha) | Share of LPA (round 2) |
|---|---:|---:|---:|
| Detail sheets (1:5,000 / 1:10,000) | 35,646.7 | 37,459.0 | 79.8% |
| Hobli maps (1:15,000–1:35,000) | 1,679.8 | 863.7 | 1.8% |
| Not coloured on the plan | 9,607.6 | 8,612.5 | 18.3% |
| **Total** | **46,934.1** | **46,935.2** | LPA 46,936.2 ha, diff -0.002% (pass ≤ 0.5%) |

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

### Re-check (round 2, step D5)

- The 27 Annexure-1 villages "listed in the 2006 LPA but outside the revised LPA, most likely moved
  to the STRR LPA": all 27 lie **fully inside the STRR LPA** on BMRDA's LPA map (current extent).
  Their rows now say STRR (`lpa_no_zone_map`: no STRR master plan).
- The 26 unmatched Annexure-1 names, re-matched against every village in Hoskote, Bangalore East,
  Anekal, Yelahanka and Devanahalli taluks (looser fuzzy match, "(B)" and "Plantation" dropped):
  22 have a plausible match that the spatial rule already assigns (21 Hoskote, 1 STRR), e.g.
  Estur → EESTURU, K.Sheetyhalli → K SHETTIHALLI, Gorvehalli → GORAVEHALLE, Appasandra
  Plantation (B) → APPASANDRA. 4 only match a different taluk's village (Korati → KODATI in BDA,
  Thindlu Plantation (B) → TINDLU in Anekal, Dodda Amanikere → DODDAJALA AMANIKERE in BIAAPA,
  Devanagondi Hosahalli → DEVANAYKANAHALLI in BIAAPA): almost certainly other villages, not used.

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
