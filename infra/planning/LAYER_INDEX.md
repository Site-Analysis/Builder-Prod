# Layer index (`layer_index.json`)

The planning service keeps **no plan data on disk**. `layer_index.json` (text only, no geometry)
names every sheet of every indexed 2031 plan and how to turn its published source into zones.
When a query needs a sheet, the service downloads the source, extracts it, keeps the result in
RAM and deletes the download straight away (contract 1.18).

## What a row holds

One row per sheet (`kind: zones`) and per LPA outline (`kind: lpa_outline`):

| Field | Meaning |
|---|---|
| `row_id`, `plan_id`, `doc_id`, `sheet`, `page` | which sheet of which register document |
| `source_url`, `sha256` | where the source is fetched from, and its expected hash |
| `source_layer`, `priority` | detail / hobli / lpa_map / composite; `priority.rank` then `priority.order` is the original merge order ("most detailed sheet wins") |
| `extent` | the sheet's footprint box, WGS84 and EPSG:32643 (used to find the sheets a query needs) |
| `georef` | the stored calibration: affine page/pixel -> EPSG:32643, or the grid-label fit; no OSM is needed at run time |
| `legend`, `extraction` | palette and the extraction method + settings (`sheet_worker.py`) |
| `clip`, `merge` | the LPA outline row the pieces are clipped to; the cut grid of the priority merge |
| `qa` | RMSE, null check, class check, counts, worker time; `pending` where a check could not run |
| `sheet_qa`, `position_uncertainty_m`, `placement_confirmed`, `warnings` | what every zone from this sheet reports (SheetQA, contract 1.18) |
| `status` | `indexed` / `rejected` / `skipped` (only `indexed` rows are served) |

`plans.<plan_id>` holds per-plan settings: the outline row, the "Not coloured on the plan" rule for
LPA area on no sheet, the georef floor and the overlay kinds. `build_id` changes whenever a row
changes and is returned by `/authority`, `/zones`, `/overlays` and `/zones/at`.

## How on-demand extraction works

1. A query (`/zones`, `/overlays`, `/zones/at`, a point `/authority`) finds the rows whose `extent`
   meets its window, plus the plan's LPA outline row.
2. Rows not in memory are queued. Up to three sources download at once, never two from the
   same host (honest User-Agent, backoff; open-decisions #47), to a unique folder under
   `%TEMP%\qnit_planning\svc\`, and each sha256 is checked against the row. A mismatch marks the rows `source_changed`: they are never served, and the map shows
   "Source changed; needs re-indexing".
3. `infra/scripts/planning/sheet_worker.py` extracts the sheet in a subprocess capped at 2 GB
   (Windows Job Object; one extraction at a time), with the row's stored calibration, and streams the zones back as
   zstd-compressed Arrow chunks (2 km). The download and scratch files are deleted.
4. The service caches chunks in RAM (LRU, 800 MB in all: compressed sheets, decoded chunks,
   merged chunks). Evicted sheets are fetched again on the next view. Nothing survives a restart.
5. Priority merge, per chunk and cached: a sheet's pieces are clipped to the LPA outline and cut by
   the footprints of every higher-priority sheet they overlap; LPA area on no sheet becomes the
   plan's "Not coloured on the plan" zone (2 km tiles). This is the merge the old build ran once
   for the whole plan, so the answers are the same.
6. While a needed sheet is loading, the answer lists it in `pending_sheets` (state, host, message,
   retry_after_s); `/zones/at` withholds that plan's hits until all its sheets there are ready.
   A point `/authority` waits up to 180 s instead (open-decisions #42).

When a source downloads, the service keeps extracting that source's other sheets while nothing
else is asked for and the cache has room (one download serves a whole atlas).

## Building or re-indexing

```powershell
# one or more plans, from their source URLs (resumable: indexed rows are skipped)
infra\scripts\dev\run_detached.ps1 -Name build_index `
  -Python infra\scripts\planning\.venv\Scripts\python.exe `
  -ScriptArgs "infra/scripts/planning/build_layer_index.py --plans BDA-RMP2031,BMRDA-HSK-MP2031,BMRDA-LPA-MAP"
```

Plans the build knows: `BDA-RMP2031`, `BMRDA-HSK-MP2031`, `BMRDA-LPA-MAP`, `BMRDA-ANK-MP2031`,
`BMRDA-NLM-MP2031` (Nelamangala grid sheets: MAP002 fitted to the LPA, grid priors from the
plan's own maps, OSM refine within 300 m; A3 / B1 / C3 / D1 may be indexed unconfirmed, other
grids that fail the checks get `status: rejected` with a reason; open-decisions #48, #49).

`--osm-only` re-runs the OSM-dependent checks of plans already indexed (Overpass outages,
open-decisions #43): BDA's ICP-on-roads affine, RMSE and null check; Hoskote's per-sheet
junction RMSE, floor and null checks; Anekal Map No. 39's junction check (#45, #50). The
previous values are read from the index and kept when the re-run agrees within the #40
tolerance; otherwise the new value is used and listed under `changed` in the REPORT line.

The build downloads each source to `%TEMP%\qnit_planning\build\`, re-runs its calibration (OSM
is fetched into memory or the build's temp area, never kept), runs the worker for QA (the zones
are discarded), writes the rows and deletes everything. Its log is under
`%TEMP%\qnit_planning\logs\build_index\`; read it, then delete it.

### When a source changes

The service reports the sheet as `source_changed` (the map says "Source changed; needs
re-indexing"). To re-index it:

1. Check the new file is the same plan (title block, GO, map number) and update its row in
   `infra/planning/plan_docs.csv`: `sha256`, `retrieved_on`, and `source_url` if it moved.
2. Mark the affected rows in `layer_index.json` as `status: "skipped"` (or delete them), then run
   the build for that plan. The calibration is redone from the new file; the build reports any
   calibration value that moved beyond 1 m (affine) or 0.5 m (RMSE / floor).
3. Commit `plan_docs.csv` and `layer_index.json` together (gitleaks on the staged files first).

If a source can no longer be fetched, the plan's rows stay as they are, and the service reports
the sheets as `failed` with a retry. Nothing is indexed from a file that cannot be re-fetched
(open-decisions #39).
