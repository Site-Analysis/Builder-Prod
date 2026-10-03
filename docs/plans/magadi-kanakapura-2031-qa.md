# Magadi and Kanakapura Master Plans 2031: register QA (round of 3 Oct 2026)

Plans `MAGADI-MP2031` and `KPA-MP2031`. **Result: registered, zones not loaded.** Their villages
get `plan_registered_not_loaded` (contract 1.18: a plan that exists is registered, never
`no_master_plan_found`). Decisions: open-decisions #51, #53. Self-checked; SME pending.

## 1. What the plans publish

| Plan | Sheet (doc_id) | Content | Size | Coordinates? | Usable as zones? |
|---|---|---|---|---|---|
| Magadi | `MAGADI-MP2031-PLU-MAGADI` | Dwg-37 Proposed Land Use 2031, Magadi town | 1,024 x 706 px JPG, 230 KB | none printed | No (see 2) |
| Magadi | `MAGADI-MP2031-PLU-TAVAREKERE` | Dwg-45 Proposed Land Use 2031, Tavarekere | 1,024 x 707 px JPG, 171 KB | none | No |
| Magadi | `MAGADI-MP2031-CIRC-MAGADI`, `-CIRC-TAVAREKERE` | Circulation maps | 1,024 px JPGs | none | Not zones |
| Magadi | `MAGADI-LPA-DECL` | LPA declaration (5 pages) | 607 KB PDF | — | — |
| Kanakapura | `KPA-MP2031-PLU-KANAKAPURA` | Master Plan of Kanakapura | 1,024 x 739 px JPG | none | No |
| Kanakapura | `KPA-MP2031-PLU-SATHANUR`, `-KAGGALIPURA` | Master Plans of Sathanur, Kaggalipura | 1,240 px scans in PDF | none | No |
| Kanakapura | `KPA-MP2031-PLU-HAROHALLI` | Master Plan of Harohalli | 1,024 x 745 px JPG | none | No |
| Kanakapura | `KPA-MP2031-GO-FINAL-2015` | Approval order, UDD 153 BMR 2013, 07-08-2015 (site label; scan not read) | 1.3 MB scan | — | — |
| Kanakapura | `KPA-MP2031-GO-IMP-2013` | Interim approval, 16-09-2013 (superseded) | 0.9 MB scan | — | — |

Status:
- **Magadi**: `draft`, "Status unconfirmed". The maps are dated 01-06-2015, the date of
  Nelamangala's final GO, but magadi.tpa.gov.in publishes no approval GO.
- **Kanakapura**: `final` from the site's label; the scanned GO has not been read (`unverified`).

Neither site publishes a plan report or zoning regulations.

## 2. Why the zones are not loaded

Each map is a whole-town drawing of about 1,000 px:
- 15-25 m per pixel at a typical town extent;
- no grid ticks, coordinate labels or scale bar readable at that resolution.

The only placement route would be the one Nelamangala needed (matching against the plan's own
LPA map, then OSM). Even at 1.3 m per pixel, that route failed its null check (nelamangala-2031-qa.md
§5). At 15-25 m per pixel the bars (RMSE <= 10 m) cannot be met. Loading them would place zones
tens to hundreds of metres off.

What would unblock it:
- ground control for each town map (3 or more identifiable points), or
- the plans' vector or GIS files from the planning authorities.

## 3. Checks

| Check | Bar | Result |
|---|---|---|
| Source reachable, sha256 recorded | every registered doc | 11 of 11 fetched 3 Oct (HTTP only; HTTPS refused), hashed, deleted. Peak temp 86 MB; 0 bytes left |
| Georef floor <= 10 m | per indexed sheet | not applicable: nothing indexed (no coordinates) |
| Null check 3x | per indexed sheet | not applicable |
| Classes within +/-10 % of the plan table | indexed part | not applicable; no land-use table published |

## 4. Village rows

Every village whose entry names `MAGADI` or `KANAKAPURA` gets:
- `plan_ids`: `MAGADI-MP2031` / `KPA-MP2031`;
- `plan_coverage`: `plan_registered_not_loaded`;
- the authority note from `authorities.csv`.

The rows are rewritten by `coverage_118.py` (step F).

## 5. Not registered

Each LPA's 2026 mobility plan is not registered (#53):
- Magadi: `Whole LPA Mobility plan.pdf` and four 15-19 MB grid PDFs;
- Kanakapura: two parts.

They are not land use, and they are large.
