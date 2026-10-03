# 2031 plan legends (as indexed)

Generated from `infra/planning/layer_index.json` (build `idx-2026-10-03-22e80c9`) and `legend_map.csv` by `infra/scripts/planning/legend_tables.py`. One table per plan; `Sheets` counts the indexed sheets using the class. Colours are the sheet's own swatch (RGB of the source); the web map uses one colour per normalised class for LPA plans.

## BDA Revised Master Plan 2031 (`BDA-RMP2031`, draft)

| Label on the sheet | Class | Colour | Role | Status | ZR zone | Mapping |
|---|---|---|---|---|---|---|
| Residential | residential | `#ffff00` | zone | extracted | R: Residential Use Zone (ZR 3.2 item 1; ch. 5) | unconfirmed |
| Commercial | commercial | `#005ce6` | zone | extracted | C: Commercial Use Zone, C-1 to C-5 (ZR 3.2 item 2; 6.1) | unconfirmed |
| Industrial | industrial | `#ca7af5` | zone | extracted | I: Industrial Use Zone, I-1 to I-4 (ZR 3.2 item 3; 6.2) | unconfirmed |
| Public and Semi Public | public_semi_public | `#ff0000` | zone | extracted | PSP: Public and Semi-public Use Zone, PSP-1 to PSP-4 (ZR 3.2 item 6; 6.3) | unconfirmed |
| Defense | defence | `#b2b2b2` | zone | extracted | PSP-UC: Public and Semi-public - Unclassified (ZR 3.2 item 7; 6.8 'include Defence and notified lands') | unconfirmed |
| Public Utilities | public_utility | `#732600` | zone | extracted | PU: Public Utilities Use Zone (ZR 3.2 item 5; 6.4) | unconfirmed |
| Parks and Open spaces | open_space | `#267300` | zone | extracted | OS: Open Spaces Use Zone (ZR 3.2 item 8; 6.5) | unconfirmed |
| Transport and Communication | transport | `#686868` | zone | extracted | T&C: Transportation and Communication Use Zone, T-1 to T-4 (ZR 3.2 item 4; 6.6) | unconfirmed |
| Agriculture | agriculture | `#a3ff73` | zone | extracted | A: Agricultural Use Zone (ZR 3.2 item 9; 6.7) | unconfirmed |
| Water Bodies | water | `#97dbf2` | zone | extracted | OS: Open Spaces Use Zone, water bodies (ZR 6.5; 6.5.3 eco-sensitive zones and water bodies) | unconfirmed |
| Water bodies | water | `#97dbf2` | zone | extracted | OS: Open Spaces Use Zone, water bodies (ZR 6.5; 6.5.3 eco-sensitive zones and water bodies) | unconfirmed |
| Streams | stream | `#0084a8` | zone | overlay (stream centreline) | OS: Open Spaces Use Zone, valley/streams (ZR 6.5.3); drawn as an overlay | unconfirmed |
| Streams | stream | `#97dbf2` | zone | overlay (stream centreline) | OS: Open Spaces Use Zone, valley/streams (ZR 6.5.3); drawn as an overlay | unconfirmed |
| Forest | forest | `#55ff00` | pattern | overlay (forest symbol) | OS: Open Spaces Use Zone, forests (ZR 6.5); drawn as an overlay over the background zone | unconfirmed |
| NGT Buffer | ngt_buffer | `#38a800` | overlay | overlay | (no zone) buffer restriction over the underlying zone (ZR 6.5.3 'buffers'; ZR p.17: prohibited-area rules prevail over the designated use) | unconfirmed |
| Special Development Zone | special_development_zone | `#a87000` | overlay | overlay | — | — |
| Conurbation Boundary 2031 |  | `#ff00c5` | line | not extracted (line / edge, not a zone) | — | — |
| LPD BDA Boundary |  | `#000000` | line | not extracted (line / edge, not a zone) | — | — |
| RailwayLine |  | `#000000` | line | not extracted (line / edge, not a zone) | — | — |
| Road Network |  | `#b2b2b2` | line | not extracted (line / edge, not a zone) | — | — |
| Not coloured on the plan | uncoloured | `#ffffff` | zone | extracted | (no zone) not in the ZR zone list | unconfirmed |
| Road space (not coloured on the plan) | road_space | `#ffffff` | zone | extracted | (no zone) cartographic; ZR 3.2 note ii: roads permitted in all use zones | unconfirmed |
| (no legend entry: white outside LPA) |  | `#ffffff` | background | not extracted (line / edge, not a zone) | — | — |
| (no legend entry: 24 blend colours) |  | `various` | edge | not extracted (line / edge, not a zone) | — | — |

## Anekal Master Plan 2031 (`BMRDA-ANK-MP2031`, final)

| Label on the sheet | Class | Colour(s) | Sheets | Status |
|---|---|---|---:|---|
| RESIDENTIAL | residential | `#ffff73` | 1 | extracted |
| COMMERCIAL | commercial | `#0070ff` | 1 | extracted |
| INDUSTRIAL | industrial | `#c500ff` | 1 | extracted |
| PUBLIC & SEMI PUBLIC | public_semi_public | `#ff0000` | 1 | extracted |
| PARK & OPEN SPACE | open_space | `#55ff00` | 1 | extracted |
| Transportation | transport | `#828282`, `#9c9c9c`, `#b2b2b2` | 1 | extracted |
| AGRICULTURE | agriculture | `#d3ffbe` | 1 | extracted |
| WATER BODIES | water | `#97dbf2` | 1 | extracted |
| Forest | forest | `#267300` | 1 | extracted |
| Not coloured on the plan (ours: LPA area not coloured on Map No. 39 (mostly roads drawn as lines)) | uncoloured | — | — | extracted (LPA area on no colour) |
| PUBLIC UTILITY (31.16 ha, hatched) | — | — | — | not extracted: hatch strokes (#ffbc00 / #bc0000) cannot be told apart from line symbols of the same style (1,389 km of #ffbc00 strokes in the frame); sheet warning kept (open-decisions #58) |
| HILLOCK'S/QUARRIES (43.31 + 249.98 ha, hatched) | — | — | — | not extracted: the #d2d2d2 hatch is shared with the forest symbol and its 49,604 strokes cover far more than the 2,420 ha of forest + hillocks; not separable per class (#58) |
| Proposed roads (STRR, PRR, IRR, RR, TRR, widening, 12 m new roads) | — | — | — | not extracted: legend swatches do not map one-to-one to the in-frame stroke styles; would be guesswork (#58) |
| Railway, NH / SH / MDR, power lines, LPA / municipal / village boundaries | — | — | — | not extracted: reference line symbols, not plan proposals (#58) |

Placement unconfirmed (dashed on the map): Map No. 39 (Proposed Land Use, consolidated).

## Hoskote Master Plan 2031 (`BMRDA-HSK-MP2031`, final)

| Label on the sheet | Class | Colour(s) | Sheets | Status |
|---|---|---|---:|---|
| RESIDENTIAL | residential | `#fcfc00` | 51 | extracted |
| COMMERCIAL | commercial | `#0070fc` | 51 | extracted |
| INDUSTRIAL | industrial | `#a800e4` | 51 | extracted |
| PUBLIC & SEMI PUBLIC | public_semi_public | `#fc0000` | 51 | extracted |
| PARK & OPEN SPACE | open_space | `#54fc00` | 51 | extracted |
| PUBLIC UTILITY | public_utility | `#fcaa00` | 51 | extracted |
| TRANSPORTATION | transport | `#9c9c9c` | 51 | extracted |
| UNCLASSIFIED | unclassified | `#e1e1e1` | 51 | extracted |
| AGRICULTURE | agriculture | `#d4fcc0` | 51 | extracted |
| WATER BODY | water | `#98dcf0` | 51 | extracted |
| FOREST | forest | `#57a634` | 51 | extracted |
| Not coloured on the plan (ours: LPA area on no detail sheet or hobli map) | uncoloured | — | — | extracted (LPA area on no colour) |
| Roads, railway, LPA / village boundaries (line symbols) | — | — | — | not extracted: raster atlas: line symbols share colours with the base map and are not separated (base-map specks are known, L5) |

## Nelamangala Master Plan 2031 (`BMRDA-NLM-MP2031`, final)

| Label on the sheet | Class | Colour(s) | Sheets | Status |
|---|---|---|---:|---|
| Residential | residential | `#fdf769` | 5 | extracted |
| Commercial | commercial | `#27a2ec` | 5 | extracted |
| Industrial | industrial | `#a217a6` | 5 | extracted |
| Public & Semi-Public | public_semi_public | `#f10401` | 5 | extracted |
| Park & Open Space | open_space | `#a3e458` | 5 | extracted |
| Public Utility | public_utility | `#fd8300` | 5 | extracted |
| Transportation | transport | `#a3a29b` | 5 | extracted |
| Water Bodies | water | `#9bf0f9` | 5 | extracted |
| Roads, boundaries, grid edges (line symbols) | — | — | — | not extracted: 96 dpi raster sheets: thin line symbols are not separable from the fills |

Placement unconfirmed (dashed on the map): Grid A3 (Nelamangala), Grid B1 (Nelamangala), Grid D1 (Nelamangala).

