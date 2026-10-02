#!/usr/bin/env python3
"""Random-pixel QA for a raster plan: sample pixels per extracted class and build montages
of the source sheet around each one, for checking by eye.

Usage:
    python qa_pixel_sample.py --data-root <dir> --primeocr-dir <dir> [--classes forest water ...]
        [--n 200] [--seed 2031]

Anekal LPA Master Plan 2031 (the 16 detail sheets). Each sheet is classified exactly as
extract_anekal.py / raster_plan.py do (nearest legend colour, halos dropped, unknown filled
from neighbours); pixels are drawn per class in proportion to that class's pixel count on
each sheet. For every sample the montage shows a 33 x 33 source-pixel crop (about 110 m),
enlarged 3x, with a red crosshair on the sampled pixel and its number.

Writes <data-root>/planning/audit/ank_pixel_sample/<class>_<k>.png (100 per montage) and
samples.json (sheet, pixel, class) for recording the verdicts.
"""

import argparse
import json
import os
import random

import numpy as np
import pymupdf
from extract_anekal import sheets
from raster_plan import (
    UNKNOWN,
    WHITE,
    class_keys,
    classify,
    drop_halos,
    fill_from_neighbours,
)

R, ZOOM, COLS = 16, 3, 10


def class_raster(s):
    a = s["load"]()
    x0, y0, x1, y1 = s["map_rect"]
    keys, codes = class_keys(s["classes"])
    cls = classify(a, keys, codes, 45.0)
    if s.get("refine"):
        s["refine"](a, cls, s["classes"])
    frame = np.zeros(cls.shape, bool)
    frame[y0:y1, x0:x1] = True
    cls[~frame] = WHITE
    drop_halos(cls, s["classes"])
    cls = fill_from_neighbours(cls, (cls == UNKNOWN) & frame)
    return a, cls


def tile(a, y, x, n):
    pad = np.pad(a, ((R, R), (R, R), (0, 0)), constant_values=255)
    c = pad[y : y + 2 * R + 1, x : x + 2 * R + 1].repeat(ZOOM, 0).repeat(ZOOM, 1).copy()
    m = R * ZOOM + ZOOM // 2
    c[m, :] = (255, 0, 0)
    c[:, m] = (255, 0, 0)
    c[m - 1 : m + 2, m - 1 : m + 2] = (255, 255, 255)
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--primeocr-dir", required=True)
    ap.add_argument(
        "--classes",
        nargs="+",
        default=["forest", "water", "commercial", "open_space", "transport"],
    )
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2031)
    args = ap.parse_args()
    out = os.path.join(args.data_root, "planning", "audit", "ank_pixel_sample")
    os.makedirs(out, exist_ok=True)
    doc = os.path.join(
        args.data_root, "raw", "BMRDA-ANK-MP2031", "BMRDA-ANK-MP2031-MP.pdf"
    )
    sh = [s for s in sheets(doc, args.primeocr_dir) if s["layer"] == "detail"]
    rnd = random.Random(args.seed)
    counts, cache = {}, {}
    for s in sh:
        a, cls = class_raster(s)
        cache[s["key"]] = (a, cls)
        for c in args.classes:
            code = 10 + [k["cnorm"] for k in s["classes"]].index(c)
            counts[(s["key"], c)] = int((cls == code).sum())
        print(f"  classified {s['name']}", flush=True)
    samples = []
    for c in args.classes:
        tot = sum(counts[(s["key"], c)] for s in sh)
        picks = []
        for _ in range(args.n):
            r = rnd.random() * tot
            for s in sh:
                r -= counts[(s["key"], c)]
                if r <= 0:
                    break
            a, cls = cache[s["key"]]
            code = 10 + [k["cnorm"] for k in s["classes"]].index(c)
            ys, xs = np.nonzero(cls == code)
            i = rnd.randrange(len(ys))
            picks.append(
                {
                    "class": c,
                    "sheet": s["name"],
                    "key": s["key"],
                    "y": int(ys[i]),
                    "x": int(xs[i]),
                }
            )
        for k in range(0, len(picks), 100):
            chunk = picks[k : k + 100]
            T = (2 * R + 1) * ZOOM
            rows = (len(chunk) + COLS - 1) // COLS
            img = np.full((rows * (T + 14), COLS * (T + 4), 3), 255, np.uint8)
            for j, p in enumerate(chunk):
                a, _ = cache[p["key"]]
                rr, cc = divmod(j, COLS)
                img[
                    rr * (T + 14) + 12 : rr * (T + 14) + 12 + T,
                    cc * (T + 4) : cc * (T + 4) + T,
                ] = tile(a, p["y"], p["x"], k + j)
            pm = pymupdf.Pixmap(
                pymupdf.csRGB,
                img.shape[1],
                img.shape[0],
                np.ascontiguousarray(img).tobytes(),
                False,
            )
            path = os.path.join(out, f"{c}_{k // 100}.png")
            pm.save(path)
            # number each tile (PDF text over the image, rendered back to PNG)
            d = pymupdf.open()
            pg = d.new_page(width=img.shape[1], height=img.shape[0])
            pg.insert_image(pg.rect, filename=path)
            for j in range(len(chunk)):
                rr, cc = divmod(j, COLS)
                pg.insert_text(
                    (cc * (T + 4) + 2, rr * (T + 14) + 10),
                    str(k + j),
                    fontsize=9,
                    color=(0, 0, 0.8),
                )
            pg.get_pixmap(dpi=72).save(path)
        samples += picks
        print(f"  {c}: {len(picks)} samples", flush=True)
    with open(os.path.join(out, "samples.json"), "w") as f:
        json.dump(samples, f, indent=1)


if __name__ == "__main__":
    main()
