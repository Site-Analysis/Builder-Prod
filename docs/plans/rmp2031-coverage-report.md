# BDA RMP 2031 (Draft): coverage and accuracy audit

30 Sep 2026. Branch `feat/planning-2031-phase0`. Audit only: nothing in the layer, the
service or the tables was changed. RMP 2031 is a draft plan, never approved.

CSVs: `<data-root>/planning/audit/` (data-root here `C:\Users\tanny\Downloads\planning`).
Scripts: `infra/scripts/planning/audit_rmp2031.py` (A1-A5, C9, C10, summary),
`audit_service.py` (B6, B7, run with the planning service venv),
`crosscheck_pdr.py --all --extents` (C8 and PD extents).

## Summary

### By district and taluk

"Searchable" = BDA villages that have parcel geometry (so a survey-number search works).
"Sum off" = parcels fully inside the LPA whose `/zones/at` hits add up to less than 95 %
(none were above 101 %). PD lists are the PDs the taluk's BDA villages fall in.

| District | Taluk | Villages | In BDA | BDA, no parcels | Searchable | Outside BDA (no plan loaded yet) | `/zones/at` parcels run | Errors | Sum off (< 95 %) | Villages > 30 % uncoloured/inferred | PDs failing PDR table check | PDs with georef flag |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Bengaluru Urban | Bangalore North | 188 | 107 | 20 | 81.3 % | 81 | 20,417 | 0 | 5,499 (26.9 %) | 28 | 6 7 8 17 18 19 32 33 34 39 40 41 | 39 41 |
| Bengaluru Urban | Bangalore South | 224 | 141 | 10 | 92.9 % | 83 | 11,195 | 0 | 2,589 (23.1 %) | 64 | 3 4 13 14 15 16 17 28 30 32 36 37 38 39 | 28 36 37 39 |
| Bengaluru Urban | Anekal | 304 | 54 | 0 | 100 % | 250 | 19,398 | 0 | 1,765 (9.1 %) | 21 | 13 26 29 30 | 26 |
| Bengaluru Urban | Bangalore East | 186 | 143 | 20 | 86.0 % | 43 | 13,451 | 0 | 2,228 (16.6 %) | 43 | 2 8 9 10 11 12 21 22 23 24 25 | 10 12 22 25 |
| Bengaluru Urban | Yalahanka (incl. North Additional) | 196 | 100 | 2 | 98.0 % | 96 | 28,162 | 0 | 3,546 (12.6 %) | 30 | 7 19 20 21 31 34 35 41 42 | 41 42 |
| Bengaluru Rural | Nelamangala | 367 | 0 | – | – | 367 | – | 0 | – | – | – | – |
| Bengaluru Rural | Doddaballapura | 302 | 0 | – | – | 302 | – | 0 | – | – | – | – |
| Bengaluru Rural | Devanahalli | 226 | 0 | – | – | 226 | – | 0 | – | – | – | – |
| Bengaluru Rural | Hoskote | 294 | 0 | – | – | 294 | – | 0 | – | – | – | – |
| **Total** | | **2,287** | **545** | **52** | **90.5 %** | **1,742** | **92,623** | **0** | **15,627 (16.9 %)** | **186** | 39 of 41 PDs with a usable table | 12 PDs |

"PDs failing the PDR table check" counts any class outside +-5 percentage points; the core
zone classes mostly pass (see A1), so this column overstates the problem. Read it with A1.

### What's missing

| # | Missing | Size | Proposed fix |
|---|---|---|---|
| M1 | **Road corridors have no polygon.** The extractor drops pieces under 2 px wide (`SLIVER_PX`), which are mostly white road corridors between blocks; nothing replaces them. | 3,004 ha of the LPA (2.49 %) in 221,595 pieces 5-8 m wide; 13 ha of that is the LPA edge. 15,627 parcels (16.9 % of those run) have hits summing < 95 %, 1,399 < 80 %. All 5 sample "mismatches" in C10 are this. | Stop dropping slivers: keep thin white pieces as "Not coloured on the plan" (or give them to the neighbouring zone), and label road corridors as such if the SME agrees they are road space. Re-run the extraction, then A2/B6. |
| M2 | **52 BDA villages cannot be searched by survey number**: 38 have no parcel file, 14 have a placeholder file with no geometry. Mostly Bangalore North (hobli 40/41 city-survey villages) and Bangalore East (Marathahalli, Beluru, Kodihalli ...). List: `bda_villages_no_parcels.csv`. | 9.5 % of BDA villages | Cadastral data gap, not a planning bug. Re-scrape these villages; until then `/authority` still answers from the schedule (coverage from text). |
| M3 | **Check points for the georeference are thin** in 12 PDs (none in PD 26, 28, 36, 37; 1-2 in 10, 12, 22, 25, 39, 41, 42). Only OSM major + secondary roads were fetched. | 12 of 42 PDs | Fetch OSM tertiary/residential roads for check points only, re-run C9. |
| M4 | **PDR table for PD 27 is blank in the source** (only Agriculture and Total PD Area, both copied from PD 26). | 1 PD | Nothing to fix on our side; record it and skip PD 27 in the table check. |
| M5 | **ELU sheet for PD 19** is not in the OpenCity dataset. | 1 sheet | Not a 2031 layer; no action. PD 19 is otherwise covered (see A5). |
| M6 | No `/zones/at` result for 5,214 parcels in partial villages. | expected | They lie wholly outside the LPA; `/authority` already says partial. No action. |

### What's wrong

| # | Wrong | Evidence | Proposed fix |
|---|---|---|---|
| W1 | **PD extents for PD 31 and PD 37 are mis-registered** (PDR figure agreement 15.8 % and 2.9 % vs baselines 48.5 % / 46.7 %; PD 37 landed at the edge of the scale search, 3.6 m/px). Their A1, C9, C10 numbers use a wrong outline. | `pdr_crosscheck_all.csv` | Widen the scale search / seed the registration from the PD index map; re-run C8, then A1/C9/C10 for those two PDs. |
| W2 | **Borderline PDR cross-check** for PD 8, 15, 17, 41 (agreement excl. PDR greys 79-87 %, within 5 points of the majority-class baseline). | `pdr_crosscheck_all.csv` | Look at the renders with the SME; likely PDR figure greys/labels, not our zones. |
| W3 | **"Uncoloured" is large and uneven**: median village 13.4 % uncoloured + 13.7 % inferred; 186 BDA villages over 30 % (81 driven by uncoloured, 105 by inferred). Worst: Bairappana Halli 91 % uncoloured, Bannerughatta 74 %, Khane Kandaya 48 % + 60 % inferred. PD 30 is 34.5 % uncoloured. | `village_quality.csv`, `pd_landuse.csv` | Ask the SME what white inside the LPA means on PLUCOMP (forest/reserve? gaothana? not yet planned?). Until then keep showing "Not coloured on the plan". |
| W4 | **NGT buffer is much larger than the PDR tables** in 31 of 41 PDs (e.g. PD 29: ours 15.1 % vs PDR 1.8 %). Our NGT area is the closed hatch extent. | `pd_vs_pdr.csv` | Check `HATCH_CLOSE_PX` against a few PDs with the SME; the PDR may count only the part not already zoned. |
| W5 | **Water, Agriculture, Unclassified/Transport do not reconcile with the PDR tables** (pass 24/41, 17/31, 10/41 even after combining road space). | `pd_vs_pdr.csv` | Mostly W3/W4/M1 plus PD extent error; revisit after M1 and W1 are fixed. |
| W6 | **"Text full + map 75-97 %" rule makes some outside points look inside** (from last round's coverage rule): 5 of 7,650 grid points outside the LPA sit in villages marked full, one 1.04 km outside (Chokkanahalli, map share 86 %). | `authority_points_check.csv` | Only apply the rule from 90 %, or mark such villages `partial` with the text note. Your call. |
| W7 | **Georeference in PD 5 is 20.4 m RMSE** (n=4), over the 20 m flag. Overall 11.5 m (n=212). | `georef_pd.csv` | Add check points (M3) before acting; if it holds, a local correction for the west side. |

Nothing crashed: 0 errors in 92,623 `/zones/at` runs and in 2,287 + 7,650 `/authority` calls.

---

## A. Coverage

### A1. Planning districts (`pd_landuse.csv`, `pd_vs_pdr.csv`)

PD boundaries are not published as vectors. Each PD's area here is the coloured extent of
its own PDR "Proposed Land Use" figure, registered onto PLUCOMP (C8). Extent area vs the
PDR's "Total PD Area": within +-12 % for 34 of 41 PDs; off for PD 2 (-19 %), 10 (+22 %),
17 (+20 %), 26 (-50 %, the PDR's PD 26 total includes 2,194 ha agriculture), 31 (+196 %),
33 (+17 %), 37 (-39 %) (W1).

- Every PD 1-42 has zones. No PD is empty.
- Uncoloured 11-18 % in most PDs (PD 30: 34.5 %); inferred under symbols 7.6-21.6 %;
  area with no polygon at all 0.8-7.1 % (over 5 % in PD 5, 8, 15, 17, 18, 32, 33: M1).
- PDR tables found for all 42 PDs (the PD number is taken from the chapter number because
  captions misprint it: PD 11's table says "PD 10", PD 33's has no PD). PD 27's is blank (M4).

Comparison, +-5 percentage points of PD area ("+-5 %" read as percentage points). PDR
"Unclassified" is grey on the PD figures and compared with our uncoloured + Defense. A combined
row adds road space, because PLUCOMP draws roads as lines over white corridors while the PDR
counts them as Transport.

| Category | PDs passing |
|---|---|
| Commercial | 41/41 |
| Industrial | 39/39 |
| Public Utility | 41/41 |
| Public & Semi Public | 40/41 |
| Parks / open spaces | 40/41 |
| Forest (our forest-symbol overlay) | 37/39 |
| Streams (our zones inferred under the stream symbol) | 35/41 |
| Residential | 34/41 |
| Water Bodies | 24/41 |
| Agriculture | 17/31 |
| NGT Buffer (our overlay) | 10/41 |
| Unclassified + Transport (combined) | 10/41 |
| Transport alone | 8/41 |
| Unclassified alone | 3/38 |

PDs passing every row (using the combined road row): 1, 6, 20.

### A2. Gaps (`gaps.csv`, `gaps_summary.json`, `gaps_top10_interior.csv`)

LPA minus (all zone polygons + NGT/forest overlay polygons): 3,004 ha, 221,595 pieces;
65,682 of them >= 100 m2, 3,369 >= 1,000 m2. Only 13 ha lie on the LPA edge (raster steps
against the smooth boundary). The rest are road corridors (median width 5-8 m), see M1.

Largest 10 interior gaps:

| # | Area m2 | Lat, lng | PD | Nearest village | Width m |
|---|---:|---|---|---|---:|
| 1 | 21,360 | 13.03153, 77.58989 | 7 | Cholanaykana Halli | 6.4 |
| 2 | 20,728 | 13.06851, 77.49499 | 19 | Seededahalli | 5.5 |
| 3 | 18,388 | 12.98219, 77.58442 | 1 | Adugodi | 8.6 |
| 4 | 14,240 | 12.94485, 77.68754 | 12 | Amani Bellandurukhane | 6.6 |
| 5 | 14,217 | 13.05762, 77.64083 | 9 | K Narayanapura | 6.1 |
| 6 | 12,306 | 13.07150, 77.58266 | 7 | Kotihosahalli | 6.1 |
| 7 | 12,213 | 12.97482, 77.49294 | 33 | Giddadakonenahalli | 5.4 |
| 8 | 12,165 | 13.05891, 77.44324 | 40 | Harokyatana Halli | 6.1 |
| 9 | 12,050 | 12.94758, 77.55070 | 15 | Avalahalli | 6.6 |
| 10 | 12,002 | 12.87191, 77.63939 | 13 | Beguru | 5.4 |

(Each is a long connected road network piece, hence large area at ~6 m width.)

### A3. Admin hierarchy (`admin_hobli.csv`, `bda_villages_no_parcels.csv`, `admin_errors.csv`)

2,287 villages (Urban 1,098, Rural 1,189). In BDA: 545 (490 full, 55 partial), all in
Bengaluru Urban. With parcel data: Urban 827, Rural 1,013. BDA villages with no parcel data:
52 (M2). Outside BDA, no plan loaded yet: 1,742 (Urban 553, Rural 1,189); 395 of them have no
parcel data (not an error for planning). Hierarchy errors: 0 (every village is in the
authority table and in the e-Chawadi list).

### A4. Villages (`village_quality.csv`)

493 BDA villages with parcels measured (52 have none). Share of each village's in-LPA area
that is uncoloured or inferred: median 13.4 % + 13.7 %. **186 villages over 30 %** (161
full, 25 partial). Top of the list:

| Village | Key | Uncoloured % | Inferred % | PD |
|---|---|---:|---:|---|
| Bairappana Halli | 20/3/8/12 | 91.2 | 22.1 | 30 |
| Khane Kandaya | 20/4/4/22 | 48.0 | 60.0 | 24 |
| Bannerughatta | 20/3/8/9 | 74.2 | 23.2 | 30 |
| Huvinani | 20/4/10/12 | 38.9 | 48.9 | 22 |
| Hebbala Amani Kere | 20/1/2/19 | 34.7 | 42.1 | 7 |
| Amani Bairatikhane | 20/4/2/34 | 32.8 | 43.0 | 9 |
| Basavanapura | 20/2/17/32 | 67.5 | 5.8 | 30 |
| Kalkere | 20/3/2/56 | 57.8 | 10.0 | 30 |

("Uncoloured" and "inferred" can overlap slightly: a zone inferred under a symbol can itself
be uncoloured.) Tank-bed ("Amani", "Kere") villages are high on inferred, as expected under
water/NGT symbols.

### A5. Documents (`documents.csv`)

95 rows in `plan_docs.csv`:
- Used by a layer (3): PLUCOMP (zones, overlays, LPA), MPD (LPA village schedule), PDINDEX (village to PD).
- Used as QA (43): PDR (per-PD tables) and the 42 PDR PLU figures (C8 and PD extents).
- Not used yet (48): 41 ELU-PD sheets and the ELU composite (existing land use 2015, not a 2031 layer), ZR (needed for `/classify`, which waits for sign-off), VISION, BROCHURE, FORM, DBINFO/OCSINGLE.

**PD 19 is covered:** its PDR figure (p202) and PDR table are used; its extent registered
(agreement 83.0 % excl. PDR greys, baseline 67.4 %, pass); PLUCOMP zones cover it (2,350 ha,
15.8 % uncoloured, 19.7 % inferred). Only ELU-PD19 is missing from the source dataset (M5).

## B. Works everywhere

### B6. `/zones/at` over every BDA parcel (`zones_at_*.csv`, `zones_at_summary.json`)

Function level, same code path as the route (`parcel_geometry` -> `zone_hits` ->
`overlays_nearby`), parcels read with the X/Y swap and passed through WGS84 as `/data` does.

- 507 BDA villages with a parcel file; 14 of those files have no geometry (M2).
- 92,623 survey numbers run. **Errors: 0** (no exception of any type).
- Empty (no zone hit): 5,434. Of these 5,214 lie wholly outside the LPA (partial villages,
  expected), 219 touch zones only under 1 % (reported as trace hits), 1 lies inside a road gap.
- Partly outside the LPA (< 99 % inside): 2,049, not checked for the sum.
- **Sums outside 95-101 %: 15,627** parcels fully inside the LPA, all below 95 % (none above
  101 %); 1,399 below 80 %, 62 below 50 %. Cause: M1 road-corridor gaps (checked on the
  worst 40: the missing part is 3.7-5 m strips whose source pixels are white).

### B7. `/authority` (`authority_villages_check.csv`, `authority_points_check.csv`)

- 2,287 village keys: 0 errors, 0 mismatches with `authority_villages.csv`; operative_plan
  null everywhere; draft plan listed for every BDA village; an unknown code in a listed
  district returns 404.
- 7,650-point grid (1 km) over both districts: 0 errors; 1,199 inside the LPA; 3,949
  consistent with the village table; 3,696 fall between village outlines (roads, lakes,
  villages without parcels); **5 mismatches**, all "point outside the LPA but village full",
  all from the 75-97 % rule (W6).

## C. Accuracy

### C8. PDR figure cross-check, all 42 PDs (`pdr_crosscheck_all.csv`)

Per-cell class agreement inside each PD's own extent (~19 m grid). Pass = agreement
excluding PDR greys >= 80 % and at least 5 points above the majority-class baseline (the
greys are ambiguous: PD-level "Unclassified" vs PLUCOMP Defense, and road lines/labels in the
JPEG). Mean agreement excl. greys: 84.0 %.

- Fail, registration: PD 31 (15.8 % vs baseline 48.5 %), PD 37 (2.9 % vs 46.7 %) (W1).
- Fail, borderline: PD 8 (79.2 vs 77.7), 15 (80.5 vs 78.8), 17 (80.0 vs 53.1: just under 80),
  41 (87.4 vs 86.2) (W2).
- Pass: the other 36, from 80.6 % to 95.2 % (PD 29).
- Largest disagreements are PDR greys (Transport/Defense/Public Utilities) against our
  Residential or Agriculture, i.e. roads and labels on the figures.

### C9. Georeference per PD (`georef_pd.csv`, `georef_points.csv`)

OSM junctions (major + secondary roads) paired with PLUCOMP road-line junctions under the
fitted affine: isolated 150 m, mutual nearest, within 60 m. 227 pairs, 15 dropped as junction
mismatches (same rule as the georef step). Overall **RMSE 11.5 m, n = 212**.

- Over 20 m: PD 5 (20.4 m, n = 4).
- No check points: PD 26, 28, 36, 37. Fewer than 3: PD 10, 12, 22, 25, 39, 41, 42 (M3).
- Others 2.8-18.5 m.

Caveat: these junctions are not independent of the fit (the road ICP used road samples
everywhere except near the original 70 check points); treat per-PD values as a consistency
check.

### C10. Random parcels (`sample_parcels.csv`, `sample_mismatches/*.png`)

3 parcels per PD, one per village, seeded; zone at the parcel centroid in our layer vs the
raw PLUCOMP pixel colour at the same spot (inverse affine onto the source raster). 124
parcels (PD 1 has only one BDA village parcel inside its extent).

| Result | Count |
|---|---:|
| Match | 84 |
| Source shows a map symbol (NGT hatch / stream / forest), ours inferred | 35 |
| **Mismatch** | **5 (4.0 %)** |

All 5 mismatches are "(no polygon)" on our side: the centroid falls on a dropped
road-corridor sliver (M1). No sample had a different zone class. Pages like the acceptance
pack are in `audit/sample_mismatches/` (PD 7, 14, 16, 33, 39).

## Method notes and limits

- "+-5 %" in A1 is read as +-5 percentage points of the PD's area.
- PD extents come from registered PDR figures (C8), so A1/C9/C10 per-PD numbers are only as
  good as the registration; PD 31 and 37 are wrong (W1), PD 2, 10, 17, 26, 33 have area
  differences over 15 %.
- A4 and B6 use village outlines built from parcels (union), so villages without parcels
  are not measured.
- B6 does not go over HTTP; B7 calls the router function with a store built from the same
  files the service loads.
