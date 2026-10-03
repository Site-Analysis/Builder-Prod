#!/usr/bin/env python3
"""Nelamangala Master Plan 2031, second route (round of 2 Oct 2026): the LPA map as a coarse
georeference, then the 1:5,000 grid sheets refined against OSM near that prior.

Usage:
    python georef_nlm.py --data-root <dir> --cadastral-dir <dir> [--stage lpa|grids]

Stage lpa (B1): MAP002 (Drg 03, LPA map, 1:70,000, about 18.5 m/px). Its coloured extent is
fitted (affine, iterative closest point on the outline, as lpa_map_bmrda.py) to the
Nelamangala LPA on BMRDA's georeferenced LPA map; the extent the plan was made for is the
pre-STRR polygon less the 37 Madhure villages added in 2015 (our parcel outlines). Then an
affine refine on major-road junctions (OSM motorway-primary): half the matches fit, the other
half are held out. Bars: IoU >= 0.97; held-out RMSE <= 30 m on >= 10 points.
MAP002 is coloured by taluk (Nelamangala / Bangalore North / Magadi), not by land use, so no
zone classes are extracted from it; its georeference is only the prior for the grid sheets.

Stage grids (B2): each 1:5,000 grid sheet gets a prior position from the plan's own maps:
the sheet's land-use colours are matched (FFT cross-correlation over scale) to Drg 31
(MAP004, the 1:21,000 conurbation land-use map, same legend), whose tanks are matched to
MAP002 (Sompura / Thyamagondlu grids, off MAP004: their tanks straight to MAP002). The prior
is then refined against OSM roads within +-300 m only (georef_osm.py: road correlation, then
junction affine with a held-out half and a null baseline). A sheet is accepted if held-out
RMSE <= 10 m, shift from the prior <= 300 m and >= 6 matches; 3-5 matches: accepted on the
median-floor rule, flagged "few ground checks"; otherwise rejected (the LPA map covers it).

Writes <data-root>/planning/zones/nlm_sheets/map002_georef.json and grids_georef.json.
"""

import argparse
import json
import os
import random

import numpy as np
import pyarrow.parquet as pq
import pymupdf
import shapely
from extract_hoskote import osm_junctions, raster_junctions, read_json
from extract_plucomp import dilate, erode, polygonise
from lpa_map_bmrda import fit_icp, outline_stats
from pyproj import Transformer
from raster_plan import hexrgb
from shapely.affinity import affine_transform

PLAN_ID = "BMRDA-NLM-MP2031"
LEGEND = {  # read from MAP005 (docs/plans/nelamangala-2031-qa.md)
    "residential": "#fdf769",
    "commercial": "#27a2ec",
    "industrial": "#a217a6",
    "public_semi_public": "#f10401",
    "open_space": "#a3e458",
    "public_utility": "#fd8300",
    "transport": "#a3a29b",
    "water": "#9bf0f9",
}
MATCH_CLASSES = [
    "residential",
    "commercial",
    "industrial",
    "public_semi_public",
    "open_space",
    "water",
]
GRIDS = {  # doc suffix -> (grid, town); MAP012 (grid B3) is only a 1,024 px JPG
    "MAP005": ("A1", "Nelamangala"),
    "MAP006": ("A2", "Nelamangala"),
    "MAP007": ("A3", "Nelamangala"),
    "MAP008": ("B1", "Nelamangala"),
    "MAP009": ("B2", "Nelamangala"),
    "MAP010": ("C1", "Nelamangala"),
    "MAP011": ("C2", "Nelamangala"),
    "MAP012": ("B3", "Nelamangala"),
    "MAP013": ("C3", "Nelamangala"),
    "MAP014": ("D1", "Nelamangala"),
    "MAP015": ("D2", "Nelamangala"),
    "MAP017": ("S1", "Sompura"),
    "MAP018": ("S2", "Sompura"),
    "MAP019": ("S3", "Sompura"),
    "MAP020": ("S4", "Sompura"),
    "MAP021": ("S5", "Sompura"),
    "MAP022": ("S6", "Sompura"),
    "MAP023": ("T1", "Thyamagondlu"),
}
PRIOR_WIN_M, SHIFT_MAX_M, GRID_HELD_MAX_M, GRID_MIN_MATCH = 300.0, 300.0, 10.0, 6
IOU_MIN, HELD_RMSE_MAX, HELD_MIN, FLOOR_M = 0.97, 30.0, 10, 18.0
MADHURE = ("MADURE", "MADHURE")


def render(path, page=0):
    d = pymupdf.open(path)
    pg = d[page]
    imgs = pg.get_images()
    W, H = (imgs[0][2], imgs[0][3]) if imgs else (pg.rect.width, pg.rect.height)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(W / pg.rect.width, H / pg.rect.height))
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return np.ascontiguousarray(a[..., :3])


def coloured_extent(a, frame):
    """Largest coloured region inside the map frame, holes filled (px coords)."""
    x0, y0, x1, y1 = frame
    m = np.zeros(a.shape[:2], bool)
    m[y0:y1, x0:x1] = a[y0:y1, x0:x1].min(-1) < 215
    m = erode(dilate(m, 4), 4)
    g = polygonise(m.astype(np.uint8))[1]
    big = max(shapely.get_parts(g), key=lambda p: p.area)
    return shapely.Polygon(big.exterior).simplify(1.0)


def madhure_villages(data_root, cad_dir):
    with open(
        os.path.join(
            os.path.dirname(cad_dir.rstrip("/\\")), "echawadi_village_list.json"
        ),
        encoding="utf-8",
    ) as f:
        hob = {}
        for v in json.load(f)["Vlglist"]:
            p = v["vlgcode"].split(",")
            n = v["vlgname"].split("|")
            if len(p) >= 4 and len(n) >= 2:
                hob[(p[3], p[2], p[1])] = n[1].strip().upper()
    t = pq.read_table(os.path.join(data_root, "planning", "villages.parquet"))
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    keys = zip(
        *(t.column(c).to_pylist() for c in ("dist", "taluk", "hobli")), strict=True
    )
    sel = [
        x
        for k, x in zip(keys, g, strict=True)
        if x is not None and hob.get(tuple(k), "") in MADHURE
    ]
    return shapely.union_all(np.array(sel, dtype=object)), len(sel)


def fit_affine(src, dst):
    M = np.column_stack([src, np.ones(len(src))])
    cx, *_ = np.linalg.lstsq(M, dst[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(M, dst[:, 1], rcond=None)
    return np.array([cx[0], cx[1], cy[0], cy[1], cx[2], cy[2]])


def apply(A, xy):
    return np.column_stack(
        [
            A[0] * xy[:, 0] + A[1] * xy[:, 1] + A[4],
            A[2] * xy[:, 0] + A[3] * xy[:, 1] + A[5],
        ]
    )


def match(G, deg, oxy, odeg, tree, radius):
    """Nearest same-degree OSM junction within radius, one-to-one (closest wins)."""
    idx = tree.query(shapely.points(G), predicate="dwithin", distance=radius)
    best = {}
    for a, b in zip(*idx, strict=True):
        if deg[a] != odeg[b]:
            continue
        d = float(np.hypot(*(G[a] - oxy[b])))
        if a not in best or d < best[a][0]:
            best[a] = (d, b)
    used, out = set(), []
    for a, (d, b) in sorted(best.items(), key=lambda kv: kv[1][0]):
        if b in used:
            continue
        used.add(b)
        out.append((a, b, d))
    return out


def junction_refine(px, deg, A, oxy, odeg, tree, seed=2031):
    """Affine refine on junction pairs (radius shrinking 150 -> 40 m), then a split: fit on
    half the pairs, RMSE on the held-out half."""
    for r in (150.0, 100.0, 70.0, 50.0, 40.0):
        pr = match(apply(A, px), deg, oxy, odeg, tree, r)
        if len(pr) < 6:
            break
        S = px[[a for a, _b, _d in pr]]
        D = oxy[[b for _a, b, _d in pr]]
        A = fit_affine(S, D)
    pr = match(apply(A, px), deg, oxy, odeg, tree, 40.0)
    rnd = random.Random(seed)
    idx = list(range(len(pr)))
    rnd.shuffle(idx)
    fit_i, held_i = idx[: len(idx) // 2], idx[len(idx) // 2 :]
    res = {"pairs": len(pr), "fit": len(fit_i), "held_out": len(held_i)}
    if len(fit_i) < 3 or not held_i:
        return A, res
    S = px[[pr[i][0] for i in fit_i]]
    D = oxy[[pr[i][1] for i in fit_i]]
    Af = fit_affine(S, D)
    Hs = px[[pr[i][0] for i in held_i]]
    Hd = oxy[[pr[i][1] for i in held_i]]
    e = np.hypot(*(apply(Af, Hs) - Hd).T)
    res.update(
        {
            "held_rmse_m": float(np.sqrt(np.mean(e**2))),
            "held_median_m": float(np.median(e)),
            "fit_rmse_m": float(np.sqrt(np.mean(np.hypot(*(apply(Af, S) - D).T) ** 2))),
        }
    )
    return fit_affine(px[[a for a, _b, _d in pr]], oxy[[b for _a, b, _d in pr]]), res


def road_mask(a):
    """MAP002 roads: national highways green, state highways / MDR red bands."""
    r, g, b = (a[..., i].astype(np.int16) for i in range(3))
    red = (r > 170) & (g < 110) & (b < 110)
    green = (g > 120) & (r < 90) & (b < 90)
    return red | green


def map_frame(a):
    H, W = a.shape[:2]
    return (int(0.045 * W), int(0.03 * H), int(0.82 * W), int(0.97 * H))


def onehot(a, names, max_d=60.0, rows=256):
    """Nearest of the named legend colours (or white) within max_d: one bool layer per name,
    inside the map frame."""
    keys = np.array([hexrgb(LEGEND[n]) for n in names] + [[255, 255, 255]], np.float32)
    out = np.empty(a.shape[:2], np.int16)
    for r0 in range(0, a.shape[0], rows):
        f = a[r0 : r0 + rows].reshape(-1, 3).astype(np.float32)
        d = ((f[:, None] - keys[None]) ** 2).sum(-1)
        j = d.argmin(1)
        ok = np.sqrt(d[np.arange(len(j)), j]) <= max_d
        out[r0 : r0 + rows] = np.where(ok, j, -1).reshape(-1, a.shape[1])
    x0, y0, x1, y1 = map_frame(a)
    fr = np.zeros(a.shape[:2], bool)
    fr[y0:y1, x0:x1] = True
    return np.stack([(out == i) & fr for i in range(len(names))])


def resample(m, s):
    """(C, H, W) bool -> share of True per output cell, scaled by s (< 1 shrinks)."""
    _c, H, W = m.shape
    Hn, Wn = int(H * s), int(W * s)
    ys = np.minimum((np.arange(Hn + 1) / s).astype(int), H)
    xs = np.minimum((np.arange(Wn + 1) / s).astype(int), W)
    cs = np.pad(m.astype(np.int32), ((0, 0), (1, 0), (1, 0))).cumsum(1).cumsum(2)
    Y0, Y1, X0, X1 = ys[:-1], ys[1:], xs[:-1], xs[1:]
    tot = (
        cs[:, Y1][:, :, X1]
        - cs[:, Y0][:, :, X1]
        - cs[:, Y1][:, :, X0]
        + cs[:, Y0][:, :, X0]
    )
    return tot / np.maximum((Y1 - Y0)[:, None] * (X1 - X0)[None], 1)


def xcorr(O, S):
    """Sum over layers of the zero-mean cross-correlation; C[iy, ix] = S's (0, 0) at O's."""
    Hf = 1 << (O.shape[1] + S.shape[1] - 1).bit_length()
    Wf = 1 << (O.shape[2] + S.shape[2] - 1).bit_length()
    C = 0
    for o, s in zip(O, S, strict=True):
        o = o.astype(np.float32) - o.mean()
        s = s.astype(np.float32) - s.mean()
        C = C + np.fft.irfft2(
            np.fft.rfft2(o, (Hf, Wf)) * np.conj(np.fft.rfft2(s, (Hf, Wf))), (Hf, Wf)
        )
    return C[: O.shape[1], : O.shape[2]]


def register(src, dst, scales, excl=15):
    """Scale + translation of src onto dst (layer stacks): dst_px = s * src_px + (tx, ty).
    Score = correlation peak / template energy; peak_ratio = peak / best peak outside
    +-excl px."""
    best = None
    D = dst.astype(np.float32)
    for s in scales:
        S = resample(src, s)
        C = xcorr(D, S)
        iy, ix = np.unravel_index(int(np.argmax(C)), C.shape)
        v = float(C[iy, ix]) / (
            np.sqrt(sum(float(((x - x.mean()) ** 2).sum()) for x in S)) + 1e-9
        )
        if best is None or v > best["score"]:
            C2 = C.copy()
            C2[max(0, iy - excl) : iy + excl, max(0, ix - excl) : ix + excl] = C.min()
            best = {
                "score": v,
                "s": float(s),
                "tx": int(ix),
                "ty": int(iy),
                "peak_ratio": float(C[iy, ix] / max(float(C2.max()), 1e-9)),
            }
    return best


def lpa_targets(cur, pre, mad):
    """The three extents MAP002 is fitted to (EPSG:32643): BMRDA's current Nelamangala LPA,
    its pre-STRR extent, and pre-STRR less the Madhure villages (the LPA the plan was made
    for)."""
    return {
        "current": cur,
        "pre_strr": pre,
        "pre_strr_less_madhure": shapely.difference(pre, mad.buffer(30)),
    }


def fit_map002(a, targets, major_elements, log=print):
    """B1: MAP002's coloured extent fitted to the targets (ICP), then the junction refine on
    OSM major roads (`major_elements`, Overpass `out geom` ways). Returns the georef dict."""
    H, W = a.shape[:2]
    frame = (int(0.045 * W), int(0.03 * H), int(0.82 * W), int(0.97 * H))
    ext_px = coloured_extent(a, frame)
    log(
        f"MAP002 extent {ext_px.area:.0f} px2; targets "
        + ", ".join(f"{k} {v.area / 1e6:.1f} km2" for k, v in targets.items())
    )
    fits = {}
    for name, T in targets.items():
        # initial: scale from the areas, y flipped, centroids aligned
        s = float(np.sqrt(T.area / ext_px.area))
        p, q = np.array(ext_px.centroid.coords[0]), np.array(T.centroid.coords[0])
        A0 = [s, 0.0, 0.0, -s, q[0] - s * p[0], q[1] + s * p[1]]
        A = fit_icp([(ext_px, T)], A0, iters=80)
        g = shapely.make_valid(affine_transform(ext_px, A))
        st = outline_stats(g, T)
        st["km2"] = round(g.area / 1e6, 1)
        st["m_per_px"] = float(np.sqrt(abs(A[0] * A[3] - A[1] * A[2])))
        fits[name] = {"A": list(map(float, A)), **st}
        log(f"  fit to {name}: {st}")
    best = max(fits, key=lambda k: fits[k]["iou"])
    A = np.array(fits[best]["A"])

    # junction refine on major roads (skipped when Overpass gave none: outline fit only)
    if not major_elements:
        g1 = shapely.make_valid(affine_transform(ext_px, A))
        return {
            "sheet": f"{PLAN_ID}-MAP002",
            "scale": "1:70,000",
            "image_px": [W, H],
            "frame_px": frame,
            "outline_fits": fits,
            "outline_target": best,
            "iou_bar": IOU_MIN,
            "iou_pass": fits[best]["iou"] >= IOU_MIN,
            "junction_refine": {"skipped": "Overpass unavailable; outline fit only"},
            "after_refine_vs_target": outline_stats(g1, targets[best]),
            "held_pass": False,
            "A_px_to_32643": list(map(float, A)),
            "A_outline_only": list(map(float, A)),
            "m_per_px": fits[best]["m_per_px"],
            "uncertainty_m": max(FLOOR_M, fits[best].get("outline_median_m", FLOOR_M)),
            "classes": "not extracted: MAP002 is coloured by taluk, not land use",
        }
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    oxy, odeg = osm_junctions(major_elements, tr)
    x0, y0, x1, y1 = shapely.make_valid(affine_transform(ext_px, A)).buffer(2000).bounds
    k = (oxy[:, 0] > x0) & (oxy[:, 0] < x1) & (oxy[:, 1] > y0) & (oxy[:, 1] < y1)
    oxy, odeg = oxy[k], odeg[k]
    tree = shapely.STRtree(shapely.points(oxy))
    rm = road_mask(a)
    fx0, fy0, fx1, fy1 = frame
    rm[:fy0], rm[fy1:], rm[:, :fx0], rm[:, fx1:] = False, False, False, False
    m_px = fits[best]["m_per_px"]
    sj, sdeg = raster_junctions(rm, m_px, lambda xy: xy)  # junctions in px
    sj = np.asarray(sj, float)
    A2, res = junction_refine(sj, np.asarray(sdeg), A, oxy, odeg, tree)
    held_pass = (
        res.get("held_out", 0) >= HELD_MIN
        and res.get("held_rmse_m", 1e9) <= HELD_RMSE_MAX
    )
    # a failed refine is not used: the prior is then the outline fit alone
    A_used = A2 if held_pass else A
    g2 = shapely.make_valid(affine_transform(ext_px, A_used))
    return {
        "sheet": f"{PLAN_ID}-MAP002",
        "scale": "1:70,000",
        "image_px": [W, H],
        "frame_px": frame,
        "outline_fits": fits,
        "outline_target": best,
        "iou_bar": IOU_MIN,
        "iou_pass": fits[best]["iou"] >= IOU_MIN,
        "sheet_junctions": len(sj),
        "osm_junctions": len(oxy),
        "junction_refine": res,
        "after_refine_vs_target": outline_stats(g2, targets[best]),
        "held_bar": {"rmse_m": HELD_RMSE_MAX, "min_points": HELD_MIN},
        "held_pass": held_pass,
        "A_px_to_32643": list(map(float, A_used)),
        "A_outline_only": list(map(float, A)),
        "A_refined": list(map(float, A2)),
        "m_per_px": float(np.sqrt(abs(A_used[0] * A_used[3] - A_used[1] * A_used[2]))),
        "uncertainty_m": max(FLOOR_M, res.get("held_rmse_m", float("nan")))
        if held_pass
        else max(FLOOR_M, fits[best].get("outline_median_m", FLOOR_M)),
        "classes": "not extracted: MAP002 is coloured by taluk, not land use",
    }


def stage_lpa(args, out_dir):
    zdir = os.path.join(args.data_root, "planning", "zones")
    a = render(os.path.join(args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-MAP002.pdf"))
    L = pq.read_table(os.path.join(zdir, "BMRDA-LPA-MAP_lpas.parquet")).to_pylist()

    def lpa(e):
        return shapely.make_valid(
            shapely.from_wkb(
                next(
                    r["geometry"]
                    for r in L
                    if r["authority"] == "BMRDA-NLM" and r["extent"] == e
                )
            )
        )

    mad, n_mad = madhure_villages(args.data_root, args.cadastral_dir)
    print(f"Madhure villages {n_mad} ({mad.area / 1e6:.1f} km2)", flush=True)
    out = fit_map002(
        a,
        lpa_targets(lpa("current"), lpa("pre_strr"), mad),
        read_json(os.path.join(args.data_root, "osm", "major.json"))["elements"],
        log=lambda m: print(m, flush=True),
    )
    with open(os.path.join(out_dir, "map002_georef.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "outline_fits"}, indent=1))


def compose(A2, s42, t42, s1, t1):
    """grid px -> MAP004 px (s1, t1) -> MAP002 px (s42, t42) -> EPSG:32643 (A2)."""
    L2 = np.array([[A2[0], A2[1]], [A2[2], A2[3]]])
    L = L2 * (s42 * s1)
    off = L2 @ (s42 * np.asarray(t1, float) + np.asarray(t42, float)) + np.array(
        A2[4:6]
    )
    return [
        float(L[0, 0]),
        float(L[0, 1]),
        float(L[1, 0]),
        float(L[1, 1]),
        *map(float, off),
    ]


def sheet_path(raw, doc):
    for ext in (".pdf", ".jpg"):
        p = os.path.join(raw, f"{PLAN_ID}-{doc}{ext}")
        if os.path.exists(p):
            return p
    return None


def osm_fine(sheet_roads, m_px, A, osm_all, tr):
    """Translation within +-PRIOR_WIN_M of the prior (road correlation on 2 m cells)."""
    from georef_osm import FINE_M, _xcorr, downsample, rasterise_lines

    Hs, Ws = sheet_roads.shape
    e0, n0 = A[4], A[5]
    fb = (
        e0 - PRIOR_WIN_M,
        n0 - Hs * m_px - PRIOR_WIN_M,
        e0 + Ws * m_px + PRIOR_WIN_M,
        n0 + PRIOR_WIN_M,
    )
    O = rasterise_lines(osm_all, tr, fb, FINE_M)
    g = max(1, round(FINE_M / m_px))
    C = _xcorr(O, downsample(sheet_roads, g))
    k = int(2 * PRIOR_WIN_M / FINE_M) + 1
    C = C[:k, :k]  # sheet origin within +-PRIOR_WIN_M of the prior
    iy, ix = np.unravel_index(int(np.argmax(C)), C.shape)
    C2 = C.copy()
    ex = int(40 / FINE_M)
    C2[max(0, iy - ex) : iy + ex, max(0, ix - ex) : ix + ex] = C.min()
    B = list(A)
    B[4] = fb[0] + ix * FINE_M
    B[5] = fb[3] - iy * FINE_M
    return B, float(C[iy, ix] / max(float(C2.max()), 1e-9))


def prior_maps(a2, a4, log=print):
    """MAP004 placed on MAP002 by their tanks; the layer stacks the grid priors match to."""
    w2 = onehot(a2, ["water"])
    r42 = register(onehot(a4, ["water"]), w2, np.arange(0.295, 0.3061, 0.001))
    log(f"MAP004 on MAP002: {r42}")
    o4h = resample(onehot(a4, MATCH_CLASSES), 0.5)  # MAP004 at half resolution
    return {"r42": r42, "w2": w2, "o4h": o4h}


def grid_prior(a, A2, pm, w_ref=3447):
    """A grid sheet's prior from the plan's own maps: its land-use colours on MAP004 (then
    MAP004 -> MAP002 -> ground), or its tanks straight on MAP002 when it is not on the
    conurbation map. w_ref: px width of grid A1 (MAP005), whose scale on MAP004 is 0.239."""
    r42 = pm["r42"]
    s0 = 0.239 * w_ref / a.shape[1]
    oh = onehot(a, MATCH_CLASSES)
    r = register(oh, pm["o4h"], s0 / 2 * np.arange(0.96, 1.041, 0.008), excl=8)
    via = "MAP004"
    s1, t1 = 2 * r["s"], (2 * r["tx"], 2 * r["ty"])
    s42, t42 = r42["s"], (r42["tx"], r42["ty"])
    if r["peak_ratio"] < 1.15:  # not on the conurbation map: tanks to MAP002
        r = register(
            oh[[MATCH_CLASSES.index("water")]],
            pm["w2"],
            s0 * r42["s"] * np.arange(0.94, 1.061, 0.01),
            excl=4,
        )
        via = "MAP002"
        s1, t1, s42, t42 = r["s"], (r["tx"], r["ty"]), 1.0, (0.0, 0.0)
    A = compose(A2, s42, t42, s1, t1)
    return {
        "image_px": [a.shape[1], a.shape[0]],
        "prior_via": via,
        "prior_match": r,
        "A_prior": A,
        "m_per_px_prior": float(np.sqrt(abs(A[0] * A[3] - A[1] * A[2]))),
    }


def prior_box(rec):
    return affine_transform(shapely.box(0, 0, *rec["image_px"]), rec["A_prior"])


def grid_refine(a, rec, osm_all, oxy, odeg, otree, tr):
    """OSM refine within +-PRIOR_WIN_M of the prior: road correlation, junction affine with a
    held-out half and the null baseline. Returns the fields to add to the sheet's record;
    `brief_bars` (held-out RMSE <= 10 m, shift <= 300 m, >= 6 matches) and `null_pass`
    (matches >= 3x the null baseline) are reported apart."""
    from georef_osm import refine
    from georef_osm import road_mask as sheet_road_mask

    A, m_px = rec["A_prior"], rec["m_per_px_prior"]
    tm = onehot(a, ["transport"])[0]
    roads = sheet_road_mask(a, tm)
    x0f, y0f, x1f, y1f = map_frame(a)
    roads[:y0f], roads[y1f:], roads[:, :x0f], roads[:, x1f:] = (False,) * 4
    Af, fine_ratio = osm_fine(roads, m_px, A, osm_all, tr)
    Ar, chk = refine(roads, m_px, Af, oxy, odeg, otree)
    c = np.array([[a.shape[1] / 2, a.shape[0] / 2]])
    shift = float(np.hypot(*(apply(np.array(Ar), c) - apply(np.array(A), c))[0]))
    n = chk.get("matched_total", 0)
    rmse = chk.get("rmse_m")
    brief = (
        rmse is not None
        and rmse <= GRID_HELD_MAX_M
        and shift <= SHIFT_MAX_M
        and n >= GRID_MIN_MATCH
    )
    null_pass = n >= 3 * max(chk.get("null_matches_mean") or 0.0, 1.0)
    ok_core = chk.get("accepted") and shift <= SHIFT_MAX_M
    status = (
        "accepted"
        if ok_core and n >= GRID_MIN_MATCH
        else "accepted_few_checks"
        if ok_core and n >= 3
        else "rejected"
    )
    return {
        "fine_peak_ratio": fine_ratio,
        "A": Ar,
        "osm_check": chk,
        "shift_from_prior_m": shift,
        "brief_bars": bool(brief),
        "null_pass": bool(null_pass),
        "status": status,
    }


def stage_grids(args, out_dir):
    from raster_plan import osm_roads

    raw = os.path.join(args.data_root, "raw", PLAN_ID)
    g2 = read_json(os.path.join(out_dir, "map002_georef.json"))
    A2 = g2["A_px_to_32643"]
    pm = prior_maps(
        render(sheet_path(raw, "MAP002")),
        render(sheet_path(raw, "MAP004")),
        log=lambda m: print(m, flush=True),
    )
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    lo = Transformer.from_crs(32643, 4326, always_xy=True).transform
    gpath = os.path.join(out_dir, "grids_georef.json")
    prev = read_json(gpath)["sheets"] if os.path.exists(gpath) else {}
    out = {"map004_on_map002": pm["r42"], "sheets": {}}
    keep = ("image_px", "prior_via", "prior_match", "A_prior", "m_per_px_prior")
    # 1. priors from the plan's own maps (kept from an earlier run)
    for doc, (grid, town) in GRIDS.items():
        if doc in prev and not args.redo_priors:
            rec = {k: prev[doc][k] for k in keep}
        else:
            rec = grid_prior(render(sheet_path(raw, doc)), A2, pm)
        out["sheets"][doc] = {"grid": grid, "town": town, **rec}
        print(
            f"  {doc} {grid:3s} prior via {rec['prior_via']} peak "
            f"{rec['prior_match']['peak_ratio']:.2f}",
            flush=True,
        )
    with open(gpath, "w") as f:
        json.dump(out, f, indent=1)
    if args.priors_only:
        return
    # 2. OSM roads per town (box of the priors + 1 km), then the refine
    for town in sorted({v[1] for v in GRIDS.values()}):
        docs = [d for d, (_g, tw) in GRIDS.items() if tw == town]
        boxes = [prior_box(out["sheets"][d]) for d in docs]
        x0, y0, x1, y1 = shapely.union_all(boxes).buffer(1000).bounds
        osm_all = osm_roads(
            args.data_root, f"nlm_{town.lower()}", (*lo(x0, y0), *lo(x1, y1)), 0.02
        )
        oxy, odeg = osm_junctions(osm_all, tr)
        otree = shapely.STRtree(shapely.points(oxy))
        for doc in docs:
            rec = out["sheets"][doc]
            upd = grid_refine(
                render(sheet_path(raw, doc)), rec, osm_all, oxy, odeg, otree, tr
            )
            flags = ["low-res source (1,024 px JPG)"] if doc == "MAP012" else []
            if upd["status"] == "accepted_few_checks":
                flags.append("few ground checks")
            rec.update({**upd, "flags": flags})
            chk = upd["osm_check"]
            print(
                f"  {doc} {rec['grid']:3s} via {rec['prior_via']} peak "
                f"{rec['prior_match']['peak_ratio']:.2f} fine {upd['fine_peak_ratio']:.2f} "
                f"shift {upd['shift_from_prior_m']:.0f} m matches {chk.get('matched_total')} "
                f"held {chk.get('matched')} rmse {chk.get('rmse_m')} "
                f"null {chk.get('null_matches_mean')} -> {upd['status']}",
                flush=True,
            )
            with open(gpath, "w") as f:
                json.dump(out, f, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--cadastral-dir", default=os.getenv("CADASTRAL_DATA_DIR"))
    ap.add_argument("--stage", default="lpa", choices=["lpa", "grids"])
    ap.add_argument("--priors-only", action="store_true", help="grids: no OSM refine")
    ap.add_argument("--redo-priors", action="store_true")
    args = ap.parse_args()
    out_dir = os.path.join(args.data_root, "planning", "zones", "nlm_sheets")
    os.makedirs(out_dir, exist_ok=True)
    if args.stage == "lpa":
        stage_lpa(args, out_dir)
    else:
        stage_grids(args, out_dir)


if __name__ == "__main__":
    main()
