# 2031 plans: seams and overlaps (step E)

Built by `infra/scripts/planning/seams.py` on 2 Oct 2026. Polygons: each loaded plan's own LPA
(BDA RMP 2031, Hoskote Map No. 19, Anekal Map No. 39) and BMRDA's LPA map (current extents)
for the rest. Overlap villages: villages whose parcels touch the overlap by > 100 m².

## Overlaps

| A | B | ha | villages | How /zones/at and /authority answer |
|---|---|---:|---:|---|
| BMRDA-ANK | STRR | 14,224.6 | 79 | BMRDA-ANK-MP2031 hits; /authority lists both authorities (BMRDA-ANK, STRR) |
| BDA | BMICAPA | 1,203.5 | 83 | BDA-RMP2031 hits; /authority lists both authorities (BDA, BMICAPA) |
| BDA | BMRDA-ANK | 813.8 | 69 | both plans' hits, each with its own status (never merged); /authority lists both authorities |
| BMRDA-ANK | KANAKAPURA | 348.5 | 9 | BMRDA-ANK-MP2031 hits; /authority lists both authorities (BMRDA-ANK, KANAKAPURA) |
| BMRDA-HSK | STRR | 311.3 | 64 | BMRDA-HSK-MP2031 hits; /authority lists both authorities (BMRDA-HSK, STRR) |
| BDA | KANAKAPURA | 185.4 | 11 | BDA-RMP2031 hits; /authority lists both authorities (BDA, KANAKAPURA) |
| BMRDA-ANK | BMRDA-HSK | 80.3 | 18 | both plans' hits, each with its own status (never merged); /authority lists both authorities |
| BIAAPA | BMRDA-HSK | 75.3 | 19 | BMRDA-HSK-MP2031 hits; /authority lists both authorities (BIAAPA, BMRDA-HSK) |
| BDA | BMRDA-HSK | 72.8 | 24 | both plans' hits, each with its own status (never merged); /authority lists both authorities |
| BMICAPA | BMRDA-ANK | 53.6 | 6 | BMRDA-ANK-MP2031 hits; /authority lists both authorities (BMICAPA, BMRDA-ANK) |
| BDA | BIAAPA | 38.4 | 24 | BDA-RMP2031 hits; /authority lists both authorities (BDA, BIAAPA) |
| BDA | MAGADI | 14.3 | 13 | BDA-RMP2031 hits; /authority lists both authorities (BDA, MAGADI) |
| BDA | BMRDA-NLM | 12.9 | 9 | BDA-RMP2031 hits; /authority lists both authorities (BDA, BMRDA-NLM) |
| BDA | STRR | 10.1 | 8 | BDA-RMP2031 hits; /authority lists both authorities (BDA, STRR) |

Notes:
- Anekal ∩ STRR is the STRR band (2021) inside the extent the Anekal plan (2014) was made for: both are returned, Anekal with a note that the area moved to the STRR LPA after the plan.
- BDA ∩ BMICAPA: the RMP 2031 LPA includes land BMRDA's map shows in the BMIC corridor; /authority lists both (BMICAPA's ODP is registered, not loaded).
- Where two loaded plans overlap (BDA–Hoskote 72.8 ha, BDA–Anekal, Anekal–Hoskote), /zones/at returns each plan's hits separately with its own status; they are never merged.

## Gaps

- Holes inside the union of all LPAs (within the villages of Bengaluru Urban + Rural): 70 holes, 400.5 ha in all; largest 77.77 ha. These are on BMRDA's map itself (land in no LPA); villages there get `no_master_plan_found` unless another rule applies.
- Land in the two districts outside every LPA: 1,188.0 ha (holes plus the outer fringe).
- Slivers along shared edges (within 100 m of two LPAs' boundaries, in neither):

| A | B | ha |
|---|---|---:|
| BDA | BMICAPA | 158.18 |
| BDA | BMRDA-NLM | 59.21 |
| BDA | BIAAPA | 43.3 |
| BDA | MAGADI | 17.42 |
| BDA | KANAKAPURA | 13.89 |
| BDA | BMRDA-ANK | 13.47 |
| BIAAPA | BMRDA-HSK | 10.7 |
| BDA | STRR | 4.18 |
| BIAAPA | BMRDA-NLM | 2.93 |
| BMRDA-HSK | STRR | 2.49 |
| BMRDA-ANK | STRR | 1.86 |
| BMICAPA | BMRDA-ANK | 1.34 |
| BDA | BMRDA-HSK | 1.01 |
| BMRDA-ANK | BMRDA-HSK | 0.98 |
| BMRDA-NLM | MAGADI | 0.77 |
| BMICAPA | MAGADI | 0.69 |
| BMRDA-NLM | STRR | 0.42 |
| BMRDA-ANK | KANAKAPURA | 0.33 |

The slivers come from mixing boundary sources (a plan's own LPA vs BMRDA's map, ~15 m apart
at the median). `/authority` for a point in a sliver returns the LPAs within 100 m as partial,
with a note (open-decisions #18), so no point falls between two LPAs. Villages are assigned by
parcel-area shares and are not affected.
