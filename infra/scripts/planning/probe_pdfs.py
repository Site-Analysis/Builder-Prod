#!/usr/bin/env python3
"""Probe plan-sheet PDFs: is each one vector (filled paths) or raster (scanned image)?

Usage:
    python probe_pdfs.py --data-root <dir> [--plan-id BDA-RMP2031]

Reads plan_sheet rows for the plan from infra/planning/plan_docs.csv, opens
<data-root>/raw/<plan_id>/<doc_id>.pdf and records per PDF: pages, page size,
page titles (Existing / Proposed Land Use Map), total, filled and coloured drawing
paths, distinct fill colours, image tiles and their page coverage, text span count,
and optional content groups (layers) with names.

Writes <data-root>/probe/<plan_id>_probe.csv (summary) and _probe.json (with fill
colour counts and layer names) and prints a markdown table. Extracts nothing.
Needs pymupdf (see requirements.txt).
"""

import argparse
import collections
import csv
import json
import os
import re
import subprocess
import sys
import time

import pymupdf

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DOCS_CSV = os.path.join(REPO, "infra", "planning", "plan_docs.csv")

# Verdict thresholds, applied per page (printed with the table so they can be judged).
# Colour fills exclude pure white and black (frames, legend boxes, text backgrounds).
VECTOR_MIN_COLOUR_FILLS = 200
RASTER_MIN_TILE_COVER = 0.20
NEUTRAL = {"#ffffff", "#000000"}

PT_TO_MM = 25.4 / 72


def hex_colour(rgb):
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb[:3])


def page_title(page):
    text = re.sub(r"\s+", " ", page.get_text().upper())
    if "PROPOSED LAND USE MAP" in text or "PROPOSED LANDUSE" in text:
        return "PLU"
    if "EXISTING LAND USE MAP" in text or "EXISTING LANDUSE" in text:
        return "ELU"
    return "?"


def probe(path):
    doc = pymupdf.open(path)
    fills = collections.Counter()
    paths = filled = colour_fills = images = spans = 0
    max_tile_cover = 0.0
    sizes, titles = set(), []
    pages_vector = pages_raster = 0
    for page in doc:
        rect = page.rect
        sizes.add(f"{rect.width * PT_TO_MM:.0f}x{rect.height * PT_TO_MM:.0f}")
        page_area = rect.width * rect.height or 1.0
        page_colour = 0
        for d in page.get_drawings():
            paths += 1
            if d.get("fill") is not None:
                filled += 1
                c = hex_colour(d["fill"])
                fills[c] += 1
                if c not in NEUTRAL:
                    page_colour += 1
        colour_fills += page_colour
        tile_area = 0.0
        for info in page.get_image_info():
            images += 1
            b = pymupdf.Rect(info["bbox"]) & rect
            tile_area += b.width * b.height
        tile_cover = min(tile_area / page_area, 1.0)
        max_tile_cover = max(max_tile_cover, tile_cover)
        if page_colour >= VECTOR_MIN_COLOUR_FILLS:
            pages_vector += 1
        elif tile_cover >= RASTER_MIN_TILE_COVER:
            pages_raster += 1
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans += len(line.get("spans", []))
        titles.append(page_title(page))
    layers = [c.get("text", "").rstrip("\x00") for c in doc.layer_ui_configs()]
    n = doc.page_count
    doc.close()
    if pages_vector == n:
        verdict = "vector"
    elif pages_raster == n:
        verdict = "raster"
    else:
        verdict = (
            f"mixed v{pages_vector}/r{pages_raster}/?{n - pages_vector - pages_raster}"
        )
    return {
        "pages": n,
        "page_titles": "".join(sorted(set(titles)))
        if len(set(titles)) == 1
        else ",".join(titles),
        "page_mm": ";".join(sorted(sizes)),
        "paths": paths,
        "fill_paths": filled,
        "colour_fill_paths": colour_fills,
        "fill_colours": len(fills),
        "images": images,
        "max_tile_cover_pct": round(max_tile_cover * 100, 1),
        "text_spans": spans,
        "layers": len(layers),
        "layer_names": layers,
        "top_fills": fills.most_common(25),
        "verdict": verdict,
    }


def probe_isolated(path):
    """Run probe() in a child process: PyMuPDF can segfault on some large sheets."""
    proc = subprocess.run(
        [sys.executable, __file__, "--single", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "verdict": "probe_error",
            "error": f"exit {proc.returncode}: {proc.stderr.strip()[-300:]}",
        }
    return json.loads(proc.stdout)


def sort_key(row):
    m = re.search(r"-PLU-PD(\d+)$", row["doc_id"])
    return (1 if m else 0, int(m.group(1)) if m else 0, row["doc_id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--plan-id", default="BDA-RMP2031")
    ap.add_argument("--single", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.single:
        print(json.dumps(probe(args.single)))
        return
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")

    with open(DOCS_CSV, newline="", encoding="utf-8") as f:
        docs = [
            r
            for r in csv.DictReader(f)
            if r["plan_id"] == args.plan_id and r["type"] == "plan_sheet"
        ]
    if not docs:
        sys.exit(f"error: no plan_sheet rows for {args.plan_id} in {DOCS_CSV}")

    raw_dir = os.path.join(args.data_root, "raw", args.plan_id)
    out_dir = os.path.join(args.data_root, "probe")
    os.makedirs(out_dir, exist_ok=True)

    results = []
    for row in sorted(docs, key=sort_key):
        path = os.path.join(raw_dir, f"{row['doc_id']}.pdf")
        if not os.path.exists(path):
            print(f"  MISSING {path}", file=sys.stderr)
            continue
        t0 = time.time()
        r = probe_isolated(path)
        r.update(
            doc_id=row["doc_id"], applies_to=row["applies_to"], status=row["status"]
        )
        r["mb"] = round(os.path.getsize(path) / 1e6, 1)
        r["seconds"] = round(time.time() - t0, 1)
        print(
            f"  {row['doc_id']}: {r['verdict']} ({r['seconds']}s)",
            file=sys.stderr,
            flush=True,
        )
        results.append(r)

    cols = [
        "doc_id",
        "applies_to",
        "status",
        "mb",
        "pages",
        "page_titles",
        "page_mm",
        "paths",
        "fill_paths",
        "colour_fill_paths",
        "fill_colours",
        "images",
        "max_tile_cover_pct",
        "text_spans",
        "layers",
        "verdict",
    ]
    base = os.path.join(out_dir, f"{args.plan_id}_probe")
    with open(base + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)

    head = ["doc_id", "page_titles", "pages", "fill_paths", "colour_fill_paths"]
    head += ["fill_colours", "images", "max_tile_cover_pct", "text_spans", "layers"]
    head += ["verdict"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for r in results:
        print("| " + " | ".join(str(r.get(c, "")) for c in head) + " |")
    print(
        f"\nverdict per page: vector = >= {VECTOR_MIN_COLOUR_FILLS} coloured fills "
        f"(not white/black); raster = image tiles cover >= {RASTER_MIN_TILE_COVER:.0%} "
        "of page with fewer coloured fills; mixed shows v/r/? page counts"
    )
    print(f"wrote {base}.csv and {base}.json")


if __name__ == "__main__":
    main()
