# BDA RMP 2031 (Draft): SME questions, defaults applied

Status of every item below: **SME pending, default applied** (decided by Tanmay, 1 Oct 2026,
so that the SME answers no longer block the build). If the SME answers differently, the
item is reopened and the layer re-extracted.

| # | Question for the SME | Default applied | Where it lives |
|---|---|---|---|
| D1 | What does white inside the LPA mean on PLUCOMP? | Zone "Not coloured on the plan" (`uncoloured`). Never guessed into another class. Thin white strips between zones are "Road space (not coloured on the plan)", a cartographic class. | `legend_map.csv`, `extract_plucomp.py` |
| D2 | The two stream colours (teal core #0084a8 and light-blue casing #97dbf2) | Both are the stream symbol: a stream centreline overlay; the zone under the symbol is filled from the surrounding land and flagged "zone inferred under stream symbol". Light blue away from the teal core stays Water Bodies. | `extract_plucomp.py` (stream casing rule) |
| D3 | Zone under the NGT buffer | Default given: the colour between the hatch lines, flagged inferred. **Note:** on PLUCOMP the NGT buffer is a solid #38a800 band, not a line hatch (audit W4), so there is no colour between lines; the zone under the band is filled from the land on either side and flagged "zone inferred under hatch", which is the same default applied to a solid band. | `extract_plucomp.py` |
| D4 | Forest pattern (tree glyphs) | Overlay ("forest symbol area") over the background zone; the zone is the ground colour under the glyphs (white, so "Not coloured on the plan"). | `extract_plucomp.py`, overlays |
| D5 | PD 17 / 31 / 37: PD-level PDR figures disagree with the composite | The Proposed Land Use composite (PLUCOMP) wins. PD 31 and 37 PDR figures do not register at all ("cross-check unavailable"). | audit report C8 |
| D6 | Village name aliases between the LPA schedule and our village list | Accepted aliases stay matched and keep their mismatch/alias note (flagged), rejected ones stay unmatched and listed. | `village_aliases.csv`, `authority_villages.csv` |
| D7 | NGT buffer extent | The drawn band (closed by 2 px to bridge road lines), not a measured buffer; the PDR's lower NGT figures in outer PDs are left as a difference to explain. | `extract_plucomp.py` (`HATCH_CLOSE_PX = 2`) |
| D8 | Mapping of the 13 legend colours to the draft Zoning Regulations (Vol 6) | Matched by name, `zr_mapping_status` = "unconfirmed" (step 1.1). | `legend_map.csv` (`zr_zone`) |
| D9 | Step 1.8 acceptance pack | Self-checked, SME pending. | `rmp2031-coverage-report.md`, `<data-root>/planning/acceptance/` |
