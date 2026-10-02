# Build plan: Bengaluru CDP / RMP 2031 planning layers

For: Claude Code in VS Code, working in `Site-Analysis/Builder-Prod`
Written: 30 Sep 2026
Inputs: the three QNIT user-story docs, the US-02 spike sources PDF, `overview/builder-prod-and-user-stories.md` (repo state as of `Cadestral` @ `a476e22`), `registers/anekal-source-register.md`, and a fresh look at OpenCity and other open sources.

---

## 0. Read this first (the one thing that changes the build)

"2031" means two different things in Bengaluru, and the build has to treat them differently.

| Area | The 2031 plan | Status today | Can it be the builder's answer? |
|---|---|---|---|
| BDA area (core Bengaluru Urban, about 1,207 sq km, 537 villages) | BDA Revised Master Plan 2031 | **Draft.** Provisionally approved Nov 2017, sent back and binned by the state in 2020 (DH, 7 Nov 2020). BDA floated a global tender for **RMP 2041** on 21 Aug 2025 (DH, 22 Aug 2025). | **No.** RMP 2015 is still the operative plan. RMP 2031 is shown only as "Draft, never approved". |
| BMRDA Local Planning Areas around it (Anekal in Bengaluru Urban; Hoskote, Nelamangala, BIAAPA / Devanahalli in Bengaluru Rural) | Each LPA's own Master Plan 2031 | **Final where a GO sanctions it.** Anekal is verified final (GO UDD 151 BMR 2013, 03.09.2014). The others need their GO confirmed. | **Yes, once the GO is recorded** in the source register. |

**Decision (Tanmay, 30 Sep 2026): build the 2031 layers first and get them right; RMP 2015 and every other layer come after.** So Phases 0 and 1 are 2031 only. Until RMP 2015 is added, anything shown for the BDA area carries a Draft badge, and nothing there is presented as the builder's answer. The BMRDA 2031 plans (Anekal now, the others once their GO is confirmed) are shown as Final.

Two more things the story docs get wrong that the build must not copy:

| In the story docs | Problem | What the build does instead |
|---|---|---|
| Mixed-use example says "Compliant with BDA RMP 2031 Zonal Rules"; AC3 caps (0 / 15 / 25 / 35 % by road width) and parking (50 / 75 sq m per ECS) are cited to RMP 2031 | RMP 2031 is a draft, and these numbers look generic (UDCPR, HMDA, MPD-style), not typed from a Karnataka ZR clause. The second doc gives a different RMP 2015 figure (about 20 % or 50 sq m, road at least 12.5 m). | No rule enters the rules table without a clause number from a specific ZR document in the register. The doc numbers are test fixtures only. |
| AC4 sources and the confidence formula use KSRSAC Dishaank and Bhoomi Land Beat | These are KSRSAC GIS products built on the state GIS; that falls under the "no KGIS in any form" rule. | Parcel match quality comes from our own cadastral lake + overlap maths. Dishaank / Land Beat are not used. |
| UDCPR (Maharashtra), HMDA (Telangana), MPD 2021 (Delhi) listed as data sources | Not law in Karnataka. | Reference only, never cited in an answer. |

---

## 1. What exists in open sources (checked 30 Sep 2026)

### 1a. BDA area

| Source | What's in it | Format | Status | Role in the build |
|---|---|---|---|---|
| [OpenCity: BDA Revised Master Plan 2031](https://data.opencity.in/dataset/bda-revised-master-plan-2031) | Vision doc, Master Plan doc, Planning District report, PD index map, **Existing Land Use composite on RMP 2015**, Proposed Land Use composite, **Zoning Regulations**, brochure, 41 Proposed Land Use maps (PD 1 to 42, PD 19 missing) | PDF | Draft | Draft zone layer + draft ZR (context only). The Existing Land Use map is a useful "what's on the ground" reference layer. |
| [RMP 2031 Vol 3, Master Plan Document](https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Bengaluru-BDA-RMP-2031-Volume_3_MasterPlanDocument.pdf) | LPA 1,206.97 sq km (1,294 with BMICAPA part), 537 villages; base map from GeoEye-1 / WorldView 0.5 m imagery, DGPS 2 points per village; survey numbers translated to English | PDF | Draft | Area tables per land use are the QA target for digitising (see 4c). |
| [OpenCity: Bengaluru Revised Master Plan 2031](https://data.opencity.in/dataset/bengaluru-revised-master-plan-2031) | Single PDF; OpenCity says it has moved under the BDA dataset and will be deleted | PDF | Draft | Don't link to it; use the BDA dataset. |
| [OpenCity: BDA Revised Master Plan 2015](https://data.opencity.in/dataset/bda-revised-master-plan-2015) | RMP 2015 ZR, Feb 2015 ZR amendments, PD index, 43 Proposed Land Use maps (PD 101 to 322, ring-numbered) | PDF | **Final (operative)** | The answer layer for the BDA area, added after the 2031 layers are done. |
| [OpenCity: RMP 2015 ZR Amendment Regulations 2025](https://data.opencity.in/dataset/zonal-regulations-of-the-rmp-2015-of-local-planning-area-of-bengaluru-amendment-regulations-2025) and [Greater Bengaluru (Amendment) Regulations 2025](https://data.opencity.in/dataset/greater-bengaluru-amendment-regulations-2025) | ZR amendments, mixed final and draft per PDF | PDF | Tag each PDF | Rules table amendments (later). |
| [OpenCity: RMP 2041 tender documents](https://data.opencity.in/dataset/tender-documents-for-preperation-of-revised-master-plan-2041-for-bengaluru) | Scope of the next plan | PDF | Reference | "What could change this answer" note. |
| [DPPlans Bengaluru](https://dpplans.com/bengaluru-dp-plan/) | Georeferenced RMP 2031 overlay with survey search; says itself "this plan is currently a draft publication" | Web viewer, JPEG | Draft, no reuse terms | **Cross-check only.** Don't ingest; no licence. |

### 1b. BMRDA LPAs (Bengaluru Urban outside BDA, and Bengaluru Rural)

| LPA | District | Plan | Status | Where to get it |
|---|---|---|---|---|
| Anekal | Bengaluru Urban | Master Plan 2031 | **Final**, GO UDD 151 BMR 2013, 03.09.2014 (verified on sheet stamps) | Already scanned and run through primeocr; ZR at `anekal.tpa.gov.in` (India only) |
| Hoskote | Bengaluru Rural | Master Plan 2031 (about 592 sq km, 316 villages per secondary sources) | GO **to confirm** | `hoskote.tpa.gov.in` or BMRDA LPA page (India only); team "DTCP Docs" Drive has Hoskote sheets |
| Nelamangala | Bengaluru Rural | Master Plan 2031; ZR PDF is titled **"Provisional Zoning Regulations"** | **Unclear**: "provisional" may mean pre-sanction. Treat as Draft until a GO is found. | [nelamangala.tpa.gov.in master plan page](http://www.nelamangala.tpa.gov.in/en/master-plan) (India only) |
| BIAAPA (Devanahalli, parts of Doddaballapura) | Bengaluru Rural | BIAAPA Master Plan (2031 horizon to confirm) | GO **to confirm** | BIAAPA office / BMRDA LPA page (India only) |
| Region-wide | Urban, Rural, Bengaluru South | [BMRDA Revised Structure Plan 2031](https://data.opencity.in/dataset/bmrda-revised-structure-plan-2031-draft-report) | OpenCity labels it a **Draft report** | Regional context only; no parcel-level zones. Lists the LPAs, useful to build the authority table. |

Magadi, Kanakapura, Ramanagara-Channapatna and Bidadi now sit in Bengaluru South district (ex-Ramanagara) and are out of scope for this phase.

### 1c. Can't reach from here

OpenCity's CKAN API and file CDN are blocked by this cloud session's network policy, and `*.tpa.gov.in` / `bmrda.karnataka.gov.in` are India-only. Claude Code on your machine can fetch all of the OpenCity files directly; the `.tpa.gov.in` and BMRDA ones need an Indian network.

---

## 2. Design rules the code must follow

1. **The document chain is the model.** Every answer walks authority, operative plan (+GO), amendments / CLU, zone, ZR (+amendments), overlays, parcel facts, answer. Plan layers are one step in it, never the answer on their own.
2. **Status lives on the document, and layers inherit it.** A zone polygon has no status of its own; it points at a `doc_id`, and the register says Final / Draft / Superseded / Reference. A derived layer is never stronger than its source.
3. **Drafts are shown, never answered with.** If the only plan for a site is a draft, the answer is "No sanctioned plan data here", with the draft zone shown underneath and labelled Draft.
4. **No plan-year comparison per parcel.** RMP 2015 and RMP 2031 are separate `plan_id`s. The API never returns a "2015 vs 2031 diff". It returns the operative plan's answer and, separately, "a draft plan also proposes X".
5. **Plan-native labels are kept.** Every polygon keeps the exact legend text from its sheet; a normalised class (URDPFI-style) is added next to it for search and filters, never in place of it.
6. **Contract first, flag per feature.** New `contracts/planning.yaml` before code; one flag per feature and per plan.
7. **Hard rules.** No KGIS or anything built on it (includes Dishaank, Land Beat). No secrets in commits. Don't push until Tanmay says so.

---

## 3. Data model

### 3a. Source register (`plan_docs`)

One row per document. Starts as a CSV checked into a data folder (same pattern as `survey_index.db`: built into SQLite at startup), with the fields the SME suggested.

| Field | Example | Notes |
|---|---|---|
| `doc_id` | `BDA-RMP2031-PLU-PD11` | Scheme: `<AUTH>-<PLAN>-<TYPE>-<part>` |
| `authority` | `BDA` | BDA, BMRDA-ANK, BMRDA-HSK, BMRDA-NLM, BIAAPA, UDD, ... |
| `plan_id` | `BDA-RMP2031` | Groups sheets, ZR, GO of one plan |
| `type` | `plan_sheet` | plan_sheet, plan_report, zr, zr_amendment, go, clu, overlay, reference |
| `status` | `draft` | final / draft / superseded / reference |
| `go_ref`, `go_date` | `UDD 151 BMR 2013`, `2014-09-03` | Empty for drafts |
| `applies_to` | `PD 11 (Doddanekkundi-Whitefield)` | LPA, PD, village list, or survey numbers |
| `amends`, `superseded_by` | `BDA-RMP2015-ZR` | Links in the chain |
| `source_url`, `sha256`, `retrieved_on` | | So we know exactly which file a polygon came from |
| `checked` | `verified` / `unverified` | As in the Anekal register |

### 3b. Plan registry (`plans`)

| `plan_id` | Authority | Horizon | Status | Operative for | Phase |
|---|---|---|---|---|---|
| `BDA-RMP2031` | BDA | 2031 | draft | nothing | 1 |
| `BMRDA-ANK-MP2031` | Anekal PA | 2031 | final | Anekal LPA | 1 |
| `BMRDA-HSK-MP2031` | Hoskote PA | 2031 | to confirm | Hoskote LPA if final | 1 |
| `BMRDA-NLM-MP2031` | Nelamangala PA | 2031 | draft until GO found | | 1 |
| `BIAAPA-MP2031` | BIAAPA | to confirm | to confirm | | 1 |
| `BDA-RMP2015` | BDA | 2015 | final | BDA LPA | Later (after 2031 is done) |

### 3c. Authority boundaries (`authorities`)

Which authority and plan covers a village. Build it as a **village-to-LPA table** from the plan reports' village lists (each plan lists its villages), joined to our LGD / parquet village keys. Polygons come from dissolving our own cadastral village boundaries, so no outside boundary source is needed. Villages in two LPAs or split by a boundary are marked `partial`.

### 3d. Zone layer (`plan_zones`, GeoParquet per `plan_id`)

Stored in EPSG:32643, served as WGS84 GeoJSON (same as the cadastral service).

| Property | Example | From |
|---|---|---|
| `zone_uid` | `BDA-RMP2031-PD11-000123` | generated |
| `plan_id`, `doc_id` | `BDA-RMP2031`, `BDA-RMP2031-PLU-PD11` | register |
| `zone_label_native` | `Residential (Main)` | sheet legend, verbatim |
| `zone_code_native` | `R-M` | legend, if the plan has codes |
| `class_norm` | `residential` | legend mapping table (3e) |
| `extraction` | `vector_pdf` / `primeocr_raster` / `manual` | pipeline |
| `georef_rmse_m` | `4.2` | georeferencing step |
| `legend_check` | `pass` / `warn` / `fail` | primeocr per-sheet check |
| `sheet_scale` | `1:10000` | sheet title block |

### 3e. Legend mapping (`legend_map.csv`)

One row per (plan_id, native label): native label, colour sampled from the legend box, `class_norm`, and the ZR zone it maps to. Built by hand per plan (a few dozen rows each) and reviewed by the SME. This is also what primeocr's legend check uses.

### 3f. Zonal rules (`zr_rules`), later phase

`plan_id, zr_doc_id, zone_code_native, use, permission (permitted/conditional/prohibited), conditions (road_width_min_m, plot_area_min_sqm, cap_pct, cap_base, floors), clause_ref, amended_by`. Only typed from final ZR text, with clause numbers. Draft ZR (RMP 2031 Vol 6) rows can be loaded with the draft's `doc_id`, and inherit Draft status.

---

## 4. Pipeline: from PDF sheet to zone layer

### 4a. Acquire and register (script: `infra/scripts/planning/fetch_sources.py`)

1. Download every file in the OpenCity RMP 2031 dataset (RMP 2015 later, same script) (CKAN API `package_show`), save under the data mount (`/data/planning/raw/<plan_id>/`), record `sha256` and `retrieved_on`.
2. Add one register row per file. Status comes from the plan registry, not from the file name.
3. BMRDA LPA sheets: drop them into the same folder by hand (India-only sites), same registration step.

### 4b. Extract zones: vector first, OCR as fallback

The single biggest accuracy lever. Many BDA PDFs are exported from GIS, so the zones may still be vector fills inside the PDF.

1. **Probe each PDF** (PyMuPDF `page.get_drawings()`): count filled paths and distinct fill colours. If a sheet has hundreds of filled polygons whose colours match the legend, it's vector.
2. **Vector sheets:** read filled paths directly as polygons in PDF page coordinates, assign class by exact fill colour against the legend map, then georeference with an affine from the sheet's grid ticks or control points. No raster classification, no OCR error.
3. **Raster / scanned sheets:** run primeocr (`primeocr add / run / review`) as today. Keep its per-sheet legend source, georef status and legend check.
4. Record which path each sheet took in `extraction`.

### 4c. Georeference and QA

| Check | Pass rule | Why |
|---|---|---|
| Control points | At least 6 per sheet, spread across corners; RMSE reported in metres | Feeds AC4 match quality |
| Cadastral fit | Re-use the Anekal test: share of sampled survey-number labels that fall in their own parcel (Anekal got 81 %) | Measures real alignment with our parcels, which are raw EPSG:32643 after the X/Y swap (no Kalianpur correction) |
| Area totals | Sum of digitised area per land use vs the plan document's land-use table, within 3 % per class | Catches missed or mis-coloured polygons without reading every sheet |
| Sheet seams | Adjacent PD sheets agree within 10 m along shared edges; overlaps dissolved | PD maps overlap at edges |
| Topology | No self-intersections, no gaps above 50 sq m inside the LPA | Clean point-in-polygon |

A sheet that fails a check is still loaded, but its polygons carry the failure and the answer drops to LOW with the reason.

### 4d. Publish

`/data/planning/zones/<plan_id>.parquet` (GeoParquet) plus an STRtree index built at service start, same approach as the parcel lake. Vector tiles are not needed yet; the map asks for zones by bbox.

---

## 5. API contract: `contracts/planning.yaml` v0.1.0

A new service (`services/planning`, FastAPI, port 8012) rather than a router in cadastral, to keep one contract per service as the repo does now. It calls cadastral for parcel geometry.

| Endpoint | Returns | Flag |
|---|---|---|
| `GET /plans` | Plan registry rows with status and GO | `feature.planning.layers` |
| `GET /docs/{doc_id}` | One register row | `feature.planning.layers` |
| `GET /authority?district&taluk&hobli&village` or `?lat&lng` | Authority, LPA, operative plan (or none), draft plans that also cover it | `feature.planning.layers` |
| `GET /zones/at?district&taluk&hobli&village&survey` | Every zone from every loaded plan that touches the parcel: native label, overlap %, distance to nearest edge, plan status, sheet QA. No answer, no confidence | `feature.planning.layers` |
| `GET /zones?plan_id&bbox` | Zone polygons (GeoJSON) for the map, with `status` copied from the register on every feature | `feature.planning.layers` + `feature.planning.plan.<plan_id>` |
| `GET /classify?district&taluk&hobli&village&survey[&intended_use][&road_width_m]` | The chain answer below | `feature.planning.classify` |

Example `/classify` response (shape, not real data):

```json
{
  "parcel": {"survey": "45/2", "village": "...", "area_sqm": 2400},
  "chain": [
    {"step": "authority", "value": "BDA", "docs": ["BDA-LPA-NOTIF"]},
    {"step": "operative_plan", "value": "BDA-RMP2015", "docs": ["BDA-RMP2015-GO"], "status": "final"},
    {"step": "amendments", "value": [], "note": "No CLU records loaded for this village yet"},
    {"step": "zone", "value": {"native": "Residential (Main)", "class_norm": "residential"},
     "overlap_pct": 96.4, "edge_distance_m": 38, "docs": ["BDA-RMP2015-PLU-PD214"]},
    {"step": "zonal_regulations", "value": null, "note": "Rules not typed yet"},
    {"step": "overlays", "value": []}
  ],
  "draft_context": [
    {"plan_id": "BDA-RMP2031", "status": "draft",
     "zone": {"native": "Residential (Mixed)"}, "overlap_pct": 88.0,
     "label": "Draft, never approved. Shown for context only."}
  ],
  "answer": {"class_norm": "residential", "wording": "The operative plan shows this site as Residential (Main)..."},
  "confidence": {"level": "MEDIUM", "reasons": ["Zonal rules not entered yet"]},
  "sources": [{"doc_id": "BDA-RMP2015-PLU-PD214", "status": "final", "go_ref": "...", "url": "..."}]
}
```

`sources` must list every `doc_id` touched, with status (AC4). If the operative plan step is empty, `answer` is null and `confidence.level` is LOW.

### Flags

| Flag | Where | Turns on |
|---|---|---|
| `feature.planning.layers` | planning service | `/plans`, `/docs`, `/authority`, `/zones` |
| `feature.planning.plan.<plan_id>` | planning service | Each plan separately, so a plan can go live after QA without a deploy |
| `feature.planning.classify` | planning service | `/classify` |
| `NEXT_PUBLIC_ENABLE_PLANNING_LAYERS` | web | Zone layer toggle and legend on the map |
| `NEXT_PUBLIC_ENABLE_PLANNING_PANEL` | web | Planning panel on the parcel card |

The existing `NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER` is not read by any code; wire the new flags through a small `isEnabled()` helper so that doesn't repeat.

---

## 6. Build sequence (one PR each, draft PRs, no push until Tanmay says)

### Phase 0: groundwork

| # | Work | Done when |
|---|---|---|
| 0.1 | Fix `CLAUDE.md` datum claim (no Kalianpur; raw 32643 after swap) and fill the 1.3 / 1.4 gap in `contracts/CHANGELOG.md` | Docs match code |
| 0.2 | `contracts/planning.yaml` v0.1.0 + changelog entry, reviewed before any code | Contract merged |
| 0.3 | `infra/scripts/planning/fetch_sources.py` + `plan_docs.csv` + `plans.csv` for Phase 1 plans | All OpenCity RMP 2031 files downloaded with hashes; one register row each |
| 0.4 | `infra/scripts/planning/probe_pdfs.py`: vector or raster per sheet | A table of 41 RMP 2031 sheets with fill-path counts |

### Phase 1: 2031 layers, built properly

| # | Work | Done when |
|---|---|---|
| 1.1 | Legend map for `BDA-RMP2031` from the composite map legend and Vol 6 ZR | SME has reviewed the native-to-normal mapping |
| 1.2 | Extract `BDA-RMP2031` (vector path if the probe says so, else primeocr) | All 41 sheets pass the QA table in 4c; area totals within 3 % of Vol 3 tables |
| 1.3 | Planning service skeleton: `/plans`, `/docs`, `/zones`, `/zones/at` + smoke test in CI | Contract test passes; zones render with a Draft badge |
| 1.4 | Web: zone layer toggle, legend with native labels, status badge per plan | Flag off: nothing changes; flag on: layer shows |
| 1.5 | `BMRDA-ANK-MP2031`: load the existing primeocr Anekal output under the new schema, re-run QA | Anekal in the same service, marked Final |
| 1.6 | Hoskote, Nelamangala, BIAAPA: register + GO lookup first, then extract and QA | Each has a confirmed status before its flag is turned on |
| 1.7 | Authority table from plan-report village lists | `/authority` returns BDA / LPA / none for every village in Bengaluru Urban and Rural |
| 1.8 | Layer acceptance on sample parcels (section 8) | `/zones/at` gives the expected zone, overlap and status for every sample; results reviewed with the SME |

**"Built properly" means:** every sheet passes QA or carries its failure, every plan has a confirmed status, the authority table covers both districts, and the sample parcels check out by eye against the source PDFs.

### Later: RMP 2015 and the answer engine (after Phase 1 is signed off)

| Work | Notes |
|---|---|
| `BDA-RMP2015` through the same pipeline (43 PD sheets + legend map) | Becomes the operative layer for the BDA area; 2031 then shows as the draft strip |
| `/classify` with the full chain and confidence | Needs an operative plan per area, so it waits for RMP 2015 in the BDA area |
| Web planning panel on the parcel card | Answer, sources with status, confidence reasons |

### Later still: other layers on top

ZR rules table (final ZR only, with clauses), CLU notifications per survey number, overlays one flag each (lakes and drains from OpenCity + ZR buffer clause, AAI height, ESZ, heritage), parcel facts (RTC land type, conversion, RERA, KIADB), then intended use and road width saved on the project.

---

## 7. Strategies so builders use this data accurately

These are ideas to discuss here before they become ACs.

| # | Strategy | What the builder sees | Why it helps |
|---|---|---|---|
| 1 | **"Which plan is in force here?" comes first** | Top line of every answer: "Operative plan: RMP 2015 (BDA), sanctioned. A draft RMP 2031 exists but was never approved." | Stops the most common mistake: reading the newest-looking map as law |
| 2 | **Draft as a separate, labelled strip** | Under the answer: "Draft RMP 2031 proposed: Residential (Mixed). Not in force." Grey, with a Draft badge | Builders want the direction of travel; this gives it without mixing it into the answer |
| 3 | **Show the split, not one colour** | "96 % Residential (Main), 4 % Park. Nearest zone edge 38 m." If under 90 % in one zone: MEDIUM plus the split | Parcels straddle zones, and our parcel-to-plan alignment has a few metres of error |
| 4 | **Uncertainty band on the map** | A thin ring around the parcel sized to the sheet's georef error; if the ring crosses a zone edge, say so | Makes positional error visible instead of hiding it |
| 5 | **Native label always, normal class second** | "Residential (Main) · residential" | Legal wording matches the plan; the normal class is for filters only |
| 6 | **"What could change this answer"** | CLU notices not yet checked for this village; ZR amendments 2025 / 2026 exist; RMP 2041 in preparation | Honest about gaps; tells the builder what to verify |
| 7 | **Road-widening lines are a warning, not an input** | "A proposed road in the draft plan crosses this parcel" | Authorities use existing road width for mixed-use; proposed widening can still take land |
| 8 | **Every number carries its clause** | "25 % commercial, ZR clause 4.x, RMP 2015 ZR (Final)" | No figure from the story docs reaches a builder without a real source |
| 9 | **Snapshot every answer** | Saved on the project with register version and date | Builders come back later; we can show what was shown and on which data |
| 10 | **Conditional wording only** | "The operative plan shows...", never "You can build..." | The story docs' own principle; also limits liability |

### Confidence levels (rule-based, replaces the weighted formula in the story doc)

| Level | When |
|---|---|
| HIGH | Final plan with GO recorded, sheet passed QA, parcel at least 90 % in one zone and at least 15 m from an edge, ZR rules typed with clauses |
| MEDIUM | Final plan, but any of: split parcel, near an edge, ZR rules not typed, CLU not checked for the village, sheet QA warning |
| LOW / VERIFY | Only a draft covers the site, no plan data, sheet QA failed, or authority unknown |

Overlays are separate constraint flags; they change what can be built, not how reliable the data is.

---

## 8. Sample parcels to test the 2031 layers (step 1.8)

| # | Case | Area | Expected from `/zones/at` |
|---|---|---|---|
| 1 | Deep inside one zone | BDA, a built-up PD (e.g. Jayanagar or HSR) | One RMP 2031 zone, over 90 % overlap, Draft badge |
| 2 | Straddles two zones | Any BDA PD | Both zones with their split, edge distance under 15 m |
| 3 | On a sheet seam | Parcel on the edge of two PD sheets | Same zone from both sheets, no double counting |
| 4 | Final 2031 LPA plan | Anekal (already digitised) | Anekal zone, Final, GO shown |
| 5 | Across the BDA / LPA boundary | A village the authority table marks `partial` | Zones from both plans, each with its own status |

---

## 9. Open items

| Item | Who | Why it matters |
|---|---|---|
| GO number and date for Hoskote, Nelamangala and BIAAPA Master Plans 2031 | Tanmay / SME, Indian network | Decides whether each is Final (answer) or Draft (context) |
| Is Nelamangala's "Provisional Zoning Regulations" the sanctioned version? | SME | Same |
| Which authority does the Greater Bengaluru Authority now route BDA-area plan questions to? | SME | Affects the authority step after the 2025 GBA changes |
| New `services/planning` (my default) or a router inside cadastral | Tanmay | Contract and CI layout |

---

## 10. First prompt for Claude Code in VS Code

> Read `plans/bengaluru-2031-planning-layers-build-plan.md` (this file). Do Phase 0 only: fix the CLAUDE.md datum note and the contract changelog gap, draft `contracts/planning.yaml` v0.1.0 with the endpoints and response shape in section 5, then write `infra/scripts/planning/fetch_sources.py` and `probe_pdfs.py` and run them on the OpenCity BDA RMP 2031 dataset only (RMP 2015 and other layers come after the 2031 layers are done). Show me the probe table (vector vs raster per sheet) before extracting anything. Rules: contract first, feature flags per section 5, no KGIS or KSRSAC Dishaank in any form, never commit secrets, work on a branch and do not push until I say so.

---

## Sources checked for this plan

- OpenCity datasets: [BDA RMP 2031](https://data.opencity.in/dataset/bda-revised-master-plan-2031), [BDA RMP 2015](https://data.opencity.in/dataset/bda-revised-master-plan-2015), [Bengaluru RMP 2031 (moved)](https://data.opencity.in/dataset/bengaluru-revised-master-plan-2031), [BMRDA RSP 2031 draft](https://data.opencity.in/dataset/bmrda-revised-structure-plan-2031-draft-report)
- [RMP 2031 Vol 3 Master Plan Document (draft)](https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Bengaluru-BDA-RMP-2031-Volume_3_MasterPlanDocument.pdf)
- [Deccan Herald, 7 Nov 2020: BDA bins draft RMP 2031](https://www.deccanherald.com/amp/story/india%2Fkarnataka%2Fbengaluru%2Fbda-bins-draft-revised-master-plan-2031-to-start-from-scratch-912405.html)
- [Deccan Herald, 22 Aug 2025: BDA floats RMP 2041 tender](https://www.deccanherald.com/amp/story/india%2Fkarnataka%2Fbengaluru%2Fafter-decades-delay-bengalurus-bda-floats-tender-for-new-city-master-plan-3691783)
- [Deccan Herald, 16 Oct 2017: High Court on RMP approval](https://www.deccanherald.com/india/karnataka/bengaluru/dont-approve-rmp-court-consent-2029494)
- [DPPlans Bengaluru](https://dpplans.com/bengaluru-dp-plan/) (states the RMP 2031 overlay is a draft)
- [Nelamangala Planning Authority master plan page](http://www.nelamangala.tpa.gov.in/en/master-plan) (India only, not opened)
- Hoskote figures (592 sq km, 316 villages) are from a secondary site (1acre.in) and are unverified.
- Could not open: OpenCity's second page of master-plan search results (rate-limited), and any `*.tpa.gov.in` or BMRDA page (India only).

---

## Decisions log, 30 Sep 2026

Recorded in the repo because the updated plan file did not come through. Where this log and the sections above disagree, this log wins.

### Sources

| Decision | Detail |
|---|---|
| OpenCity PD sheets are Existing Land Use, not Proposed | All 41 "Land Use Maps - Planning District n" PDFs (172 pages) are titled "Existing Land Use Map". Registered as `BDA-RMP2031-ELU-PD<n>` (plan_sheet, draft). The plan's "41 Proposed Land Use maps" (section 1a, step 1.2) was wrong. |
| ELU as a reference layer | New plan `BDA-ELU2015` (status reference) derived from the ELU sheets. First extraction, since 26 of 41 sheets are vector (11 raster, 4 mixed). |
| Proposed 2031 zones come from PLUCOMP | `BDA-RMP2031-PLUCOMP`, the A0 Proposed Land Use composite. Chosen over the PDR figures because it is lossless (Flate, exactly 15 main colours = 99.8% of pixels; PDR figures are JPEG with 290k colours), covers the whole LPA on one sheet (no seams, includes PD19), carries its labels as vector text (not burned into zone colour), and has vector road lines usable for georeferencing. About 4.9 m per pixel at the fitted scale 1:57,340; the "1:5,000" in its title block is wrong. |
| PDR figures are a cross-check only | `BDA-RMP2031-PDR-PLU-PD1..42` (Planning District Report Proposed Land Use figures, PDR page in `applies_to`; PD14/16 caption typos fixed in title with the printed caption kept). After extraction, compare zone class per PD and flag disagreements. |
| Moved OpenCity dataset | `bengaluru-revised-master-plan-2031` holds one PDF, byte-identical to `BDA-RMP2031-DBINFO`; no Proposed Land Use pages. BDA's site (kbda.karnataka.gov.in) failed TLS and was not used. |

### Georeferencing PLUCOMP (`infra/scripts/planning/georef_plucomp.py`)

1. Coarse affine from lakes: raster water blobs matched to OSM water polygons, seeded by the 5 lakes labelled on the sheet that match OSM names, then RANSAC (60 lake points).
2. Check points fixed before the fine fit: 72 road junctions (degree >= 3) on the sheet's vector road network paired with OSM junctions, isolated by 250 m. Road samples within 200 m of them are excluded from the fit.
3. Fine fit: trimmed ICP (affine) of the sheet's grey vector road network onto OSM motorway..tertiary lines (85k pairs, pair RMSE 4.0 m, converged).
4. Result: scale 1:57,340, rotation 0.00 deg, anisotropy 0.9998, `m_per_px` 4.86. Check points: median 5.8 m; 58 of 72 within 25 m (RMSE 8.0 m); 11 at 50-126 m, likely mispaired junctions or realigned roads (not yet verified). No quadrant pattern (mean residuals under 10 m); 2nd-order polynomial no better, so affine is kept.
5. Independent check: the LPA boundary polygonised from the sheet's 1.92 pt black lines measures 1,207 km2 at the fitted scale (official 1,206.97 km2).
6. Rail is not vector on the sheet, so level crossings could not be used. Sources: OpenStreetMap only. No KGIS, Dishaank or Land Beat.

### Contract

- 1.9.0: `SheetQA.m_per_px`, `SheetQA.georef_method`.
- 1.10.0: `raster_palette` in `SheetQA.extraction`; examples use real doc_ids (`ELU-PD<n>`, `PLUCOMP`).

### Legend defaults for BDA-RMP2031 (`infra/planning/legend_map.csv`, uncommitted until SME review)

All rows are "default, SME to confirm".

- White inside the LPA boundary: class `uncoloured`, never guessed. 14.2% of LPA pixels; 16% of that white lies within about 10 m of a road line; 11.9% of the LPA is white away from roads.
- Two "Streams" legend rows (`#0084a8` and `#97dbf2`) both kept with native label, class `stream`. `#97dbf2` is also Water Bodies, so those pixels are classed water unless the SME decides otherwise.
- NGT Buffer (`#38a800` hatch): stored as a separate overlay layer. The zone underneath is filled from surrounding non-hatch pixels of the same area; those polygons are marked "zone inferred under hatch".
- Forest (`#55ff00` tree glyphs): extent = glyph pixels closed and dilated to a solid area; method recorded.
- Blend colours (24 colours, 0.19%): nearest main class.

### Deferred

- Snapping zone edges to ELU road, lake and drain lines.
- RMP 2015, `/classify` and all other layers until Phase 1 sign-off.

### Round 2 (30 Sep 2026): extraction of BDA-RMP2031 from PLUCOMP

- **Strip flip.** PLUCOMP's raster strips are stored bottom-up (negative y scale); both scripts now flip them. The first extraction was scrambled by this; the georeference was not (its fine fit uses vector roads). Final fit unchanged: 1:57,341, `m_per_px` 4.86.
- **Georef QA.** 70 junction check points; 10 dropped as junction-identity mismatches (sheet junction on an OSM road, its cross road missing from the fetched OSM classes; renders in `georef/BDA-RMP2031/outliers/`). `georef_rmse_m` 10.1 m (robust, n=60); 30.6 m on all 70. No genuine map error found.
- **Symbols are overlays, not zones.** Each is removed from the zone raster and the zone underneath is filled from surrounding land pixels (lakes never a source):
  - NGT Buffer hatch -> overlay (hatch closed 10 px); zone parts flagged "zone inferred under hatch".
  - Stream symbol (teal core + thin light-blue casing) -> Zhang-Suen centreline overlay (1,178 km); zone parts flagged "zone inferred under stream symbol" (3,181 ha).
  - Forest glyphs -> "forest symbol area" overlay (2,267 ha; closed 30 px, dilated 2 px). Ground colour under the glyphs is white (#ffffff, 86%), so the zone there is "uncoloured".
- **Output.** `<data-root>/planning/zones/BDA-RMP2031.parquet` (67,709 zone polygons, EPSG:32643, ZoneProperties + SheetQA struct, status draft) and `BDA-RMP2031_overlays.parquet` (NGT, forest symbol area, stream centrelines). Slivers under 2 px wide dropped (3,542 ha, mostly white road corridors).
- **Area check vs Vol 3 Table 10-1** (per-class `area_check`): land zones pass/fail at 3 % on the table's basis (NGT area excluded); Transport, Water/Stream, NGT and Forest are "not comparable: cartographic" with a one-line reason. Result: Residential, Industrial, PSP, Defence pass; Commercial (-5.1 %), Agriculture (-35 %) fail; Public Utility (+6.0 %) and Parks (+8.4 %) fail with note, not tuned.
- **PDR cross-check** (7 PDs, automatic registration): agreement 47-76 %, 78-91 % excluding PDR greys. PD17 is below its baseline: its figure colours only PD17 and draws the surrounding districts as grey base map, which the colour classifier reads as Defense (14,765 of 18,776 such cells fall on neighbouring Residential). Not fixed.
- **Uncoloured** stays unguessed until the SME answers: 15.3 % of the LPA as polygons.
- **Next:** step 1.3, planning service skeleton serving this layer. Overlays are not in the contract yet.

## Decisions log, 1-2 Oct 2026: every village in Bengaluru Urban + Rural gets an answer

Full list with the default applied and what would change it: `docs/plans/open-decisions.md`.

| Decision | Detail |
|---|---|
| Contract 1.16.0 (Tanmay, 1 Oct) | `plan_coverage` (plan_loaded / plan_registered_not_loaded / lpa_no_zone_map / no_master_plan_found), `authorities[]` per covering authority (never merged), `sources_checked`; `source_layer` (detail / hobli / lpa_map / composite), `sheet`, `mixed_source_layers` on hits; a hit spanning layers takes the coarser layer's uncertainty. |
| Hoskote | Forest added (tree icons, not a fill), grey halos dropped; every class within ±10 % of Tables 66/67 except unclassified (explained) and transport (cartographic). `docs/plans/hoskote-2031-qa.md`. |
| Nelamangala | Final plan registered; **zones not loaded**: the grids print no coordinates, and OSM-only georeferencing failed validation on Hoskote sheets with a known grid. Villages: `plan_registered_not_loaded`, Madhure `lpa_no_zone_map`. `docs/plans/nelamangala-2031-qa.md`. |
| Anekal | Loaded (final). primeocr merged layer failed QA (overlapping sheets); Hoskote method on the raw sheets with primeocr's georeference and palette. Hatched classes not extracted; road bands needed the map's mid-grey as a transport key (found on the acceptance pages). `docs/plans/anekal-2031-qa.md`. |
| Authorities | BMRDA's LPA map (vector, strrpa site) georeferenced to our LPAs; STRR band painted over older LPAs. BIAAPA: MP2021 operative, registered not loaded. STRR: no master plan. BMICAPA ODP 2004, Doddaballapura PA registered. BDA keeps Anekal-taluk villages it covers fully. |
| Seams | Overlaps reported (`docs/plans/2031-seams.md`); points between LPAs whose boundaries come from different sources get the LPAs within 100 m. |
| Web | One sub-switch per plan with "Draft" / "Final" / "Final, subject to court case"; card lists every plan hit with its own status. |
