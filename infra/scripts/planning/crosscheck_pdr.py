#!/usr/bin/env python3
"""Cross-check extracted PLUCOMP zone classes against the PDR Proposed Land Use figures.

Usage:
    python crosscheck_pdr.py --data-root <dir> [--pds 2 7 12 17 20 28 36]

For each PD figure (BDA-RMP2031-PDR-PLU-PD<n>): classify the JPEG by nearest legend
colour (within MAX_RGB_DIST, white = unknown), register it onto the extracted class raster
by FFT cross-correlation on a coarse grid then a direct search on a fine grid (both maps
are north-up, no rotation), then report
per-pixel class agreement and the largest disagreements. Figures carry labels and JPEG noise,
so agreement is a consistency check, not a truth score.

Writes <data-root>/planning/zones/BDA-RMP2031_pdr_crosscheck.json.
"""

import argparse
import csv
import json
import os
import sys

import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(__file__))
from extract_plucomp import close, dilate
from fetch_sources import PDR_PLU_FIGURES

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LEGEND_CSV = os.path.join(REPO, "infra", "planning", "legend_map.csv")
PLAN_ID = "BDA-RMP2031"
MAX_RGB_DIST = 70
M_PER_PX = 4.86  # PLUCOMP pixel size (georef)
GRID = 4  # compare on a grid of GRID PLUCOMP pixels (~19 m)
COARSE = 16  # coarse search grid (~78 m)
SCALES_C = np.exp(np.linspace(np.log(6), np.log(24), 48))  # figure px per coarse cell
FINE_SHIFT = 8  # +- fine cells
FINE_SCALES = np.linspace(0.95, 1.05, 11)
PD_CLOSE_PX = 6
MAIN_CLASSES = (
    "Residential",
    "Public and Semi Public",
    "Industrial",
    "Commercial",
    "Water Bodies",
    "Defense",
)


def zone_colours(codes):
    """legend colour -> class code used in the extracted raster (zones only)."""
    by_label = {v: int(k) for k, v in codes.items()}
    out = {}
    with open(LEGEND_CSV, encoding="utf-8") as f:
        legend = list(csv.DictReader(f))
    for r in legend:
        h = r["colour_hex"]
        if (
            r["role"] == "zone"
            and h.startswith("#")
            and h != "#ffffff"
            and r["zone_label_native"] in by_label
        ):
            out.setdefault(h, by_label[r["zone_label_native"]])
    return out


def classify(rgb, colours):
    keys = np.array(
        [[int(h[i : i + 2], 16) for i in (1, 3, 5)] for h in colours], float
    )
    codes = np.array(list(colours.values()), np.int16)
    flat = rgb.reshape(-1, 3).astype(float)
    d = ((flat[:, None, :] - keys[None]) ** 2).sum(-1)
    j = d.argmin(1)
    out = np.where(np.sqrt(d[np.arange(len(j)), j]) <= MAX_RGB_DIST, codes[j], -1)
    white = flat.min(1) > 225
    out[white] = -1
    return out.reshape(rgb.shape[:2])


def resample(a, s, shape_out=None):
    """Nearest-neighbour: output pixel k samples input pixel floor(k * s)."""
    h, w = a.shape
    H, W = shape_out or (int(h / s), int(w / s))
    yi = np.minimum((np.arange(H) * s).astype(int), h - 1)
    xi = np.minimum((np.arange(W) * s).astype(int), w - 1)
    return a[yi][:, xi]


def grow(seed, allowed):
    """Region grown from seed pixels through allowed pixels (8 px per step)."""
    comp = seed & allowed
    while True:
        nxt = dilate(comp, 8) & allowed
        if nxt.sum() == comp.sum():
            return comp
        comp = nxt


def pd_extent(fig, greys):
    """The PD's own area on its PDR figure: each figure colours only its own PD and draws
    neighbouring districts as grey base map. Coloured (non-grey) pixels are closed, holes
    are filled (grey inside the PD stays in), and the largest region is kept (drops the
    legend swatches)."""
    coloured = close((fig >= 10) & ~np.isin(fig, greys), PD_CLOSE_PX)
    border = np.zeros_like(coloured)
    border[[0, -1], :] = True
    border[:, [0, -1]] = True
    outside = grow(border & ~coloured, ~coloured)
    inside = ~outside
    ys, xs = np.nonzero(coloured)
    cy, cx = int(np.median(ys)), int(np.median(xs))
    j = np.argmin((ys - cy) ** 2 + (xs - cx) ** 2)
    seed = np.zeros_like(inside)
    seed[ys[j], xs[j]] = True
    return grow(seed, inside)


def agreement(t, ref, dy, dx):
    """Share of cells where both are coloured zones and agree; template placed at (dy, dx)."""
    H, W = ref.shape
    th, tw = t.shape
    y0, x0 = max(dy, 0), max(dx, 0)
    y1, x1 = min(dy + th, H), min(dx + tw, W)
    if y1 <= y0 or x1 <= x0:
        return 0.0, 0, None, None
    a = t[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
    b = ref[y0:y1, x0:x1]
    both = (a >= 10) & (b >= 10)
    n = int(both.sum())
    return (float((a[both] == b[both]).mean()) if n else 0.0), n, a, b


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--pds", type=int, nargs="+", default=[2, 7, 12, 17, 20, 28, 36])
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    with open(os.path.join(zdir, f"{PLAN_ID}_classes.json")) as f:
        meta = json.load(f)
    cls = np.load(os.path.join(zdir, f"{PLAN_ID}_classes.npy"))
    colours = zone_colours(meta["codes"])
    names = {int(k): v for k, v in meta["codes"].items()}
    by_label = {v: int(k) for k, v in meta["codes"].items()}
    main_codes = [by_label[n] for n in MAIN_CLASSES if n in by_label]
    ref_f = cls[::GRID, ::GRID].astype(np.int16)
    ref_f[ref_f < 10] = -1
    ref_c = cls[::COARSE, ::COARSE].astype(np.int16)
    ref_c[ref_c < 10] = -1
    Hc, Wc = ref_c.shape
    pdr = pymupdf.open(
        os.path.join(args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-PDR.pdf")
    )
    results = []
    for pd in args.pds:
        page_no, _caption = PDR_PLU_FIGURES[pd]
        p = pdr[page_no - 1]
        big = max(
            p.get_image_info(xrefs=True),
            key=lambda x: pymupdf.Rect(x["bbox"]).get_area(),
        )
        pix = pymupdf.Pixmap(pdr, big["xref"])
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, pix.n
        )[..., :3]
        fig = classify(rgb, colours)
        greys = [
            by_label[g]
            for g in ("Defense", "Transport and Communication")
            if g in by_label
        ]
        pd_mask = pd_extent(fig, greys)
        # coarse: FFT correlation per scale, candidate judged by direct agreement
        best = None
        for s in SCALES_C:
            t = resample(fig, s)
            th, tw = t.shape
            if th < 12 or tw < 12:
                continue
            FH, FW = 1 << (Hc + th).bit_length(), 1 << (Wc + tw).bit_length()
            score = np.zeros((FH, FW), np.float32)
            for c in main_codes:
                ind = (t == c).astype(np.float32)
                if ind.sum() < 10:
                    continue
                ind -= ind.mean()
                score += np.fft.irfft2(
                    np.fft.rfft2((ref_c == c).astype(np.float32), s=(FH, FW))
                    * np.conj(np.fft.rfft2(ind, s=(FH, FW))),
                    s=(FH, FW),
                )
            dy, dx = divmod(int(np.argmax(score)), FW)
            dy = dy - FH if dy > FH - th else dy
            dx = dx - FW if dx > FW - tw else dx
            agr, n, _, _ = agreement(t, ref_c, dy, dx)
            known = int((t >= 10).sum())
            if n >= 0.5 * known and (best is None or agr > best[0]):
                best = (agr, s, dy, dx)
        if best is None:
            print(f"PD {pd}: no registration found", flush=True)
            continue
        _, s_c, dy_c, dx_c = best
        # fine: direct search around the coarse solution on the ~19 m grid
        k = COARSE // GRID
        fbest = None
        for f in FINE_SCALES:
            s_f = s_c / k * f
            t = resample(fig, s_f)
            for dy in range(dy_c * k - FINE_SHIFT, dy_c * k + FINE_SHIFT + 1):
                for dx in range(dx_c * k - FINE_SHIFT, dx_c * k + FINE_SHIFT + 1):
                    agr, n, _, _ = agreement(t, ref_f, dy, dx)
                    if n >= 0.5 * int((t >= 10).sum()) and (
                        fbest is None or agr > fbest[0]
                    ):
                        fbest = (agr, s_f, dy, dx)
        agr, s_f, dy, dx = fbest
        t = resample(fig, s_f)
        agr_all, n_all, _, _ = agreement(t, ref_f, dy, dx)
        t = np.where(resample(pd_mask, s_f), t, -1)  # compare inside the PD only
        agr, n, a, b = agreement(t, ref_f, dy, dx)
        both = (a >= 10) & (b >= 10)
        dis = both & (a != b)
        keep = both & ~np.isin(a, greys)
        pairs = {}
        for x, y in zip(a[dis].tolist(), b[dis].tolist(), strict=True):
            pairs[(x, y)] = pairs.get((x, y), 0) + 1
        top = sorted(pairs.items(), key=lambda kv: -kv[1])[:3]
        _, cnt = np.unique(b[both], return_counts=True)
        res = {
            "pd": pd,
            "doc_id": f"{PLAN_ID}-PDR-PLU-PD{pd}",
            "pdr_page": page_no,
            "figure_m_per_px": float(GRID * M_PER_PX / s_f),
            "offset_fine_cells": [int(dy), int(dx)],
            "compared_cells": n,
            "agreement": agr,
            "agreement_whole_figure": agr_all,
            "compared_cells_whole_figure": n_all,
            "pd_extent_source": "coloured extent of the PDR figure (holes filled)",
            "majority_class_share": float(cnt.max() / cnt.sum()),
            # PDR greys are ambiguous: PD-level legends have an "Unclassified" grey close to
            # PLUCOMP Defense, and road lines/labels in the JPEG read as Transport
            "agreement_excl_pdr_greys": float((a[keep] == b[keep]).mean())
            if keep.any()
            else None,
            "top_disagreements": [
                {
                    "pdr": names.get(x, str(x)),
                    "plucomp": names.get(y, str(y)),
                    "share": c / n,
                }
                for (x, y), c in top
            ],
        }
        results.append(res)
        print(
            f"PD {pd:2d} (p{page_no}): figure {res['figure_m_per_px']:.2f} m/px, compared {n:,} cells, "
            f"agreement {agr:.1%} (majority-class baseline {res['majority_class_share']:.1%}; excl. PDR greys {res['agreement_excl_pdr_greys']:.1%}); top: "
            + "; ".join(
                f"{d['pdr']}->{d['plucomp']} {d['share']:.1%}"
                for d in res["top_disagreements"]
            ),
            flush=True,
        )
    with open(os.path.join(zdir, f"{PLAN_ID}_pdr_crosscheck.json"), "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
