#!/usr/bin/env python3
"""Legend tables of every indexed 2031 plan (step E, round of 3 Oct 2026), from the layer
index and legend_map.csv only (text; no download).

    python legend_tables.py > docs/plans/legend-tables-2031.md
"""

import csv
import json
import os

# legend items that are not zone classes, per plan: (item, status, reason)
SYMBOLS = {
    "BMRDA-HSK-MP2031": [
        (
            "Roads, railway, LPA / village boundaries (line symbols)",
            "not extracted",
            (
                "raster atlas: line symbols share colours with the base map and are not separated (base-map "
                "specks are known, L5)"
            ),
        ),
    ],
    "BMRDA-ANK-MP2031": [
        (
            "PUBLIC UTILITY (31.16 ha, hatched)",
            "not extracted",
            (
                "hatch strokes (#ffbc00 / #bc0000) cannot be told apart from line symbols of the same style "
                "(1,389 km of #ffbc00 strokes in the frame); sheet warning kept (open-decisions #58)"
            ),
        ),
        (
            "HILLOCK'S/QUARRIES (43.31 + 249.98 ha, hatched)",
            "not extracted",
            (
                "the #d2d2d2 hatch is shared with the forest symbol and its 49,604 strokes cover far more than "
                "the 2,420 ha of forest + hillocks; not separable per class (#58)"
            ),
        ),
        (
            "Proposed roads (STRR, PRR, IRR, RR, TRR, widening, 12 m new roads)",
            "not extracted",
            "legend swatches do not map one-to-one to the in-frame stroke styles; would be guesswork (#58)",
        ),
        (
            "Railway, NH / SH / MDR, power lines, LPA / municipal / village boundaries",
            "not extracted",
            "reference line symbols, not plan proposals (#58)",
        ),
    ],
    "BMRDA-NLM-MP2031": [
        (
            "Roads, boundaries, grid edges (line symbols)",
            "not extracted",
            "96 dpi raster sheets: thin line symbols are not separable from the fills",
        ),
    ],
}

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(REPO, "infra", "planning")


def read_csv(name: str) -> list[dict]:
    with open(os.path.join(REG, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    with open(os.path.join(REG, "layer_index.json"), encoding="utf-8") as f:
        ix = json.load(f)
    plans = {r["plan_id"]: r for r in read_csv("plans.csv")}
    print("# 2031 plan legends (as indexed)\n")
    print(
        f"Generated from `infra/planning/layer_index.json` (build `{ix['build_id']}`) and "
        "`legend_map.csv` by `infra/scripts/planning/legend_tables.py`. One table per plan; "
        "`Sheets` counts the indexed sheets using the class. Colours are the sheet's own "
        "swatch (RGB of the source); the web map uses one colour per normalised class for LPA "
        "plans.\n"
    )
    by_plan: dict[str, list[dict]] = {}
    for r in ix["rows"]:
        if r["kind"] == "zones" and r.get("status") == "indexed":
            by_plan.setdefault(r["plan_id"], []).append(r)
    for plan_id in sorted(by_plan):
        rows = by_plan[plan_id]
        p = plans.get(plan_id, {})
        print(f"## {p.get('name', plan_id)} (`{plan_id}`, {p.get('status', '?')})\n")
        if plan_id == "BDA-RMP2031":
            print(
                "| Label on the sheet | Class | Colour | Role | Status | ZR zone | Mapping |"
            )
            print("|---|---|---|---|---|---|---|")
            for m in read_csv("legend_map.csv"):
                if m["plan_id"] == plan_id:
                    st = {
                        "zone": "extracted",
                        "overlay": "overlay",
                        "pattern": "overlay (forest symbol)",
                    }.get(m["role"], "not extracted (line / edge, not a zone)")
                    if m["zone_label_native"] == "Streams":
                        st = "overlay (stream centreline)"
                    print(
                        f"| {m['zone_label_native']} | {m['class_norm']} | `{m['colour_hex']}` | "
                        f"{m['role']} | {st} | {m['zr_zone'] or '—'} | {m['zr_mapping_status'] or '—'} |"
                    )
            print()
            continue
        seen: dict[tuple, dict] = {}
        for r in rows:
            leg = r.get("legend") or {}
            for c in (
                leg.get("classes") or (r.get("extraction") or {}).get("classes") or []
            ):
                label = c.get("label")
                cn = c.get("class_norm") or c.get("cnorm")
                cols = (
                    c.get("colours")
                    or c.get("fills")
                    or ([c["colour"]] if c.get("colour") else [])
                )
                k = (label, cn)
                e = seen.setdefault(k, {"cols": set(), "n": 0})
                e["cols"].update(cols)
                e["n"] += 1
        uncovered = (ix["plans"].get(plan_id) or {}).get("uncovered")
        print("| Label on the sheet | Class | Colour(s) | Sheets | Status |")
        print("|---|---|---|---:|---|")
        for (label, cn), e in seen.items():
            cols = ", ".join(f"`{c}`" for c in sorted(e["cols"]))
            print(f"| {label} | {cn} | {cols} | {e['n']} | extracted |")
        if uncovered:
            print(
                f"| {uncovered['zone_label_native']} (ours: {uncovered['note']}) | "
                f"{uncovered['class_norm']} | — | — | extracted (LPA area on no colour) |"
            )
        for item, st, why in SYMBOLS.get(plan_id, []):
            print(f"| {item} | — | — | — | {st}: {why} |")
        unconf = [r["sheet"] for r in rows if r.get("placement_confirmed") is False]
        if unconf:
            print(f"\nPlacement unconfirmed (dashed on the map): {', '.join(unconf)}.")
        print()


if __name__ == "__main__":
    main()
