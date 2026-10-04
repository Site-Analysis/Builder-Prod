# Step 1.6: Hoskote, Nelamangala and BIAAPA master plans, register and probe

1 Oct 2026. Register only: nothing extracted. Sources are the planning authorities' own sites
(plain http; they serve no https): hoskote.tpa.gov.in, nelamangala.tpa.gov.in,
biaapa.tpa.gov.in. The BMRDA "Local Planning Areas" page is a directory (addresses and site
links), with no plan documents. No KGIS, KSRSAC Dishaank or Bhoomi Land Beat used.

Script: `infra/scripts/planning/fetch_lpa_sources.py` (fixed URL allowlist, sha256, one
`plan_docs.csv` row per file; 44 rows). Files: `<data-root>/raw/<plan_id>/`.
Probe: `probe_pdfs.py --plan-id <plan>` (now also reads image sheets and reports printed
scale, legend ("LEGEND" or "INDEX"), raster size and resolution).

## GO findings

GOs are scanned (no text layer); read from the page images.

| Plan | Status | GO (final approval, KTCP Act s.13(3)) | Provisional | Notes |
|---|---|---|---|---|
| Hoskote Master Plan 2031 (`BMRDA-HSK-MP2031`) | **final** | **UDD 152 BMR 2013, Bengaluru, 30-01-2018** (site: "final order") | UDD 152 BMR 2013, 16-09-2013 | Condition 1: subject to the High Court's final judgment in W.P. 4188/2016. LPA declared by UDD 118 Bem Ru Pra 2003 (03-03-2006). 51 villages moved to the STRR LPA (UDD 36 BMR 2016, 24-06-2016); Nandagudi township excluded. |
| Nelamangala Master Plan 2031 (`BMRDA-NLM-MP2031`) | **final** | **UDD 150 BMR 2013, Bengaluru, 01-06-2015** (site: "FINAL") | UDD 150 BMR 2013, 16-09-2013 (site: "PROVISIONAL", registered as superseded) | LPA later extended by 37 villages of Madhure hobli, Doddaballapura taluk (UDD 141 BMR 2015, 08-12-2015), agricultural zone until the plan is revised: not covered by these sheets. Sheets carry a red GO stamp citing the 01-06-2015 GO. |
| BIAAPA (`BIAAPA-MP2021`) | **final, 2021 horizon** | **UDD 157 BMR 2005, Bengaluru, 27-01-2009** (Karnataka Gazette Part I, 27 Jan 2009; corrigendum 29-01-2009) | UDD 248 Bem Ru Pra 2003, 13-09-2004 (superseded) | **No 2031 plan is published**: the site carries Master Plan 2021 only. `BIAAPA-MP2031` stays draft/unconfirmed with that note. MP 2021 is registered as its own plan (not a 2031 layer). |

**Zoning regulations: all three draft.** Each final GO approves the master plan "report and
land-use plan maps" (Hoskote, Nelamangala) or "the final master plan" (BIAAPA); none names the
zoning regulations. Rule applied: final only with a GO that approves them. Hoskote's ZR cover
says "(Final)" (inner pages still "Master Plan (Provisional)"); the BIAAPA GO preamble says new
zoning regulations were framed for the plan. **Decision for Tanmay:** keep the three ZRs
draft, or treat a plan's final GO as approving the ZR published with it.

Other GOs registered as reference: UDD 53 BMR 2013 (04-03-2013, BMRDA empowered to prepare
the five LPA plans), UDD 31 BRA 2006 (19-07-2006, planning authorities constituted), the
2006 integrated-townships proceedings (both sites), the BIAAPA LPA declaration (12-01-1996).

## Probe

| doc_id | Pages | Kind | Scale | Legend | Raster | Contents |
|---|---:|---|---|---|---|---|
| BMRDA-HSK-MP2031-LPAMAP | 1 | raster | (none printed) | no | 761 x 891 px, 121 dpi | LPA outline, thumbnail quality |
| BMRDA-HSK-MP2031-MP | 87 | **vector** (67 vector pages, 9 raster, 11 other) | 1:90,000 LPA; 1:15,000-1:35,000 hobli; **1:10,000 / 1:5,000** detail | yes ("INDEX") | 300 dpi on raster pages | Atlas: context maps, ELU (p12-19), proposed land use LPA-wide 1:90,000 (p22-23), per hobli (p24, 29, 40, 49, 60, 75) and detail sheets Map No. 23-77 |
| BMRDA-NLM-MP2031-MAP002 | 1 | raster | 1:70,000 | in image | 3,445 x 2,379 px, 96 dpi | Drg 03 Local Planning Area |
| BMRDA-NLM-MP2031-MAP003 | 1 | raster | 1:70,000 | in image | 96 dpi | Drg 08 TGR reservoir catchment zones |
| BMRDA-NLM-MP2031-MAP004 | 1 | raster | 1:21,000 | in image | 96 dpi | Drg 31 Proposed land use, Nelamangala |
| BMRDA-NLM-MP2031-MAP005-015 | 1 each | raster | 1:5,000 | in image | ~3,450 x 2,380 px, 96 dpi | Drg 32-43 Proposed land use, Nelamangala grids A1-D2 |
| BMRDA-NLM-MP2031-MAP012 (jpg) | 1 | raster | 1:5,000 | in image | **1,024 x 705 px** | Drg 39 grid B3: only a small JPG is published |
| BMRDA-NLM-MP2031-MAP014J (jpg) | 1 | raster | 1:5,000 | in image | 1,024 x 707 px | Drg 41 grid D1 again (PDF also published) |
| BMRDA-NLM-MP2031-MAP017-022 | 1 each | raster | 1:5,000 | in image | 96 dpi | Drg 44-49 Proposed land use, Sompura grids A1-B3 |
| BMRDA-NLM-MP2031-MAP023 | 1 | raster | 1:5,000 | in image | 96 dpi | Drg 50 Proposed land use, Thyamagondlu |
| BIAAPA-MP2021-LPAMAP | 1 | **vector** | 1:60,000 | no | – | LPA map, 12 layers |
| BIAAPA-MP2021-MP | 42 | raster | (in image) | in image | 6,622 x 4,676 px, 200 dpi | p1-7 context, p8 plan-wide proposed land use with area table, p9-42 Proposed Land Use 2021 grids A1-H1 |

Raster "in image" = the scale and legend are visible on the sheet but the PDF has no text.

## What this means for extraction (not started)

- **Hoskote:** vector atlas with 1:10,000 / 1:5,000 detail sheets: the best source of the
  three; extraction like a vector plan.
- **Nelamangala:** only the urbanisable towns are mapped (Nelamangala, Sompura,
  Thyamagondlu at 1:5,000; Nelamangala at 1:21,000); drawing numbers 04-07, 09-30 and 51+
  are not on the site, so there is **no LPA-wide proposed land use sheet**. Sheets are
  96 dpi images (~1.3 m per pixel if printed at 1:5,000), grid B3 only as a 1,024 px JPG. The rest
  of the LPA (outside the towns) has no published zone map.
- **BIAAPA:** the operative plan is 2021, not 2031; 34 raster grid sheets at 200 dpi. Whether
  to load a 2021 plan at all is a scope decision ("2031 layers only").
