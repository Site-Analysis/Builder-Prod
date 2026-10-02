# Round of 2 Oct 2026: progress checkpoint (battery stop, ~17:00)

Branch `feat/planning-2031-phase0`. Not pushed. PR #20 untouched.

## Done (committed)

| Step | Commit | Result |
|---|---|---|
| 0 | 1a08346 | contract 1.17.0 (SheetQA.warnings, near-edge note) |
| A | 54331a8 | Anekal round 4: plan extent, all classes ±10 % except water +20 % (measured cause, SME pending); gap breakdown; hatch check |
| C | f74ae01 | BLR North 38→2 no-plan (36 via Annexure sl 261); BLR South 47 Magadi/Kanakapura → no_master_plan_found; Devanahalli 2 + 15 Hoskote STRR-band villages → lpa_no_zone_map entry (uncoloured sliver rule); 1.17.0 wording; smoke 18 passed |
| D | 67db7c4 | web: switches from /plans loaded:true, "(coarse map)", sheet warnings; tsc OK |

D5: done in round 2 (22 plausible matches, 4 open, 27 STRR villages all in STRR).
E2: `seams.py` re-run: slivers 333 ha, 259 village touches, largest width 197.5 m (BIAAPA–Hoskote) → no tightening (#30).

## In progress (not committed)

- **B Nelamangala** (`infra/scripts/planning/georef_nlm.py`, WIP commit):
  - B1 MAP002 outline fit: IoU 0.981 vs pre-STRR less Madhure (pass). Junction refine: 1 pair only (road mask grabs red developed-area boundary) → junction bar not met. Classes not extractable (taluk colours).
  - B2 priors done (`<data-root>/planning/zones/nlm_sheets/grids_georef.json`): Nelamangala grids via MAP004 colour match (peak 1.34–2.05); Sompura/Thyamagondlu via MAP002 tanks (weak, 1.04–1.37).
  - OSM refine waits on Overpass (504/429): tiles cached in `<data-root>/osm/nlm_nelamangala_tiles` (44/63). Resume: `run_nlm.ps1` (WMI), it reuses priors and cached tiles.
  - Still to do: extraction of accepted sheets (none yet), B3 rows, run_services flag (added), nelamangala-2031-qa.md.
- **E**: planning service restarted 16:53 with new Anekal + authority rows. First request per plan 47–60 s (BDA 48, HSK 60, ANK 47), private memory ≈10.4 GB after 3 plans (peak not final). Anekal function audit was running (`run_audit_all.ps1 -plans BMRDA-ANK-MP2031`); re-run it, then taluk step + `run_http500.ps1`, Anekal acceptance pages (`acceptance_lpa.py`).
- **F**: docs (coverage report, CHANGELOG data notes, plan_docs.csv, decisions log).

## After the round

Restart OneDrive (`"C:\Program Files\Microsoft OneDrive\OneDrive.exe"`).
