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
