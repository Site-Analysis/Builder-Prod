# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan roads from one sheet of the Anekal Mobility Plan (Proposed Circulation Plan, 1:10,000
grid sheets; open-decisions #72). Runs as a capped worker: job JSON on stdin, result JSON on
stdout, everything else in RAM.

What a sheet carries (probed 3 Oct 2026):
  - a 300 dpi raster base under vector overlays. Roads to be widened are grey with dashed
    dark-red (#a80000) ROW edge lines; proposed roads are pale (#e1e1e1) with the same edges.
    The edges are drawn to scale: next to '18m' labels they are 18.1 m apart (IQR 17.5-18.3),
    next to '24m' labels 24.1 m.
  - width labels as text, 7.9 pt: 'NNm' on red-edged roads, bold bare numbers on grey
    existing main roads (the plan's ROW for them); road names in magenta ('SH 87').
  - ring roads as vector strokes (STRR #005ce6, ITRR #e69800, IRR #a87000 at >= 3 pt, radial
    roads #e64c00), their ROW in the sheet legend (90 / 90 / 90 / 60 m).

Method: render the map box at 300 dpi -> colour masks (red edges, grey, pale) with text boxes
and ring-road bands masked out -> road mask = grey | pale | pixels between two red edges
5-36 m apart -> thinning -> edge graph -> each width label claims edges outward along straight
continuations (<= 30 deg, <= 600 m path, nearest label wins; on red-edged edges the measured
spacing must agree within max(3 m, 15 %)) -> per edge: label, measured spacing, grey / pale
share. Output coordinates are page points; the builder georeferences them.
"""

from __future__ import annotations

import heapq
import itertools
import json
import os
import re
import sys
import time

# numpy's OpenBLAS reserves a buffer per thread (~800 MB committed on 32 CPUs); nothing here
# needs threaded BLAS, so one thread keeps the 2 GB worker cap honest
for _v in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")

import numpy as np
import pymupdf
import shapely
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import LineString, Point

DPI = 300
MPX = 10000 * 0.0254 / DPI  # metres per raster px at 1:10,000
DS = 2  # pooling for the road mask / skeleton
WMAX_M, WMIN_M = (
    36.0,
    5.0,
)  # red-edge spacing accepted as a road (wider ROWs are vectors)
PROP_MAX_M = 600.0
DEFLECT_DEG = 30.0
GAP_JUNCTION_M = 15.0
GAP_LABEL_M = 60.0  # a label's circle cuts the road (~50 m across)
# drawn band width a label may run onto, vs where it started: a much narrower band is another
# road (a layout street off a main road); wider bands occur at junctions and merges
GW_MIN, GW_MAX = 0.65, 2.0
LATERAL_M = (
    6.0  # a bridged gap lands at most 6 m (+10 % of the gap) beside the line ahead
)
SEED_RADIUS_M = (
    40.0  # edges this close to a label start its claim (circle ~25 m radius)
)
SEED_MIN_M = 40.0  # an edge this long sets the band width a label continues on
STRAIGHT_DEG = 12.0
SMALL_MIN_M = 40.0  # unlabelled existing roads shorter than this are not kept  # continuations this straight are all followed
RED_EDGE = "#a80000"
NAME_COLOUR = "#c500ff"
RING = {
    "#005ce6": ("STRR", 90),
    "#e69800": ("ITRR", 90),
    "#a87000": ("IRR", 90),
    "#e64c00": ("Radial road", 60),
}
# per-plan drawing style (job["style"] overrides; defaults = Anekal Mobility Plan sheets)
ANEKAL_STYLE = {
    "scale": 10000,  # sheet scale (metres per raster px follow from it)
    "grey": 176,  # grey road band level (neutral RGB)
    "grey_tol": 9,
    "pale": 225,  # proposed-road fill level
    "red": "raster",  # ROW edges: dark red raster dashes; or {"vector": [[colour, width], ...]}
    "label": "anekal",  # 7.9 pt 'NNm' / bold bare numbers; or {"bold_min_size": pt}
    "rings": True,  # ring / radial roads from their vector strokes (sheet legend ROW)
    "wmax": WMAX_M,
}

NB8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def hx(c) -> str:
    return "-" if c is None else "#" + "".join(f"{round(v * 255):02x}" for v in c[:3])


# --- raster -------------------------------------------------------------------------------


def colour_masks(page, box, bands=8, style=None):
    """The map box rendered at 300 dpi in horizontal bands (one display list; no full RGB
    raster in memory) -> masks of red ROW edges, grey roads, pale proposed roads."""
    z = DPI / 72
    w = round((box[2] - box[0]) * z)
    h = round((box[3] - box[1]) * z)
    red, grey, pale = (np.zeros((h, w), bool) for _ in range(3))
    dl = page.get_displaylist()
    edges = np.linspace(0, h, bands + 1).astype(int)
    for y0, y1 in itertools.pairwise(edges):
        clip = pymupdf.Rect(box[0], box[1] + y0 / z, box[2], box[1] + y1 / z)
        pm = dl.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
        hh, ww = min(pm.height, y1 - y0), min(pm.width, w)
        r, g, b = (a[:hh, :ww, k].astype(np.int16) for k in range(3))
        st = style or ANEKAL_STYLE
        if st["red"] == "raster":
            red[y0 : y0 + hh, :ww] = (np.abs(r - 168) < 36) & (g < 60) & (b < 60)
        neutral = (np.abs(r - g) < 7) & (np.abs(g - b) < 7)
        grey[y0 : y0 + hh, :ww] = neutral & (np.abs(r - st["grey"]) <= st["grey_tol"])
        pale[y0 : y0 + hh, :ww] = neutral & (np.abs(r - st["pale"]) <= 5)
        del pm, a, r, g, b, neutral
    del dl
    return red, grey, pale, w / (box[2] - box[0]), h / (box[3] - box[1])


def pool(m, k):
    h, w = (m.shape[0] // k) * k, (m.shape[1] // k) * k
    return m[:h, :w].reshape(h // k, k, w // k, k).max(axis=(1, 3))


def morph(m, k, op):
    im = Image.fromarray(m.astype(np.uint8) * 255)
    im = im.filter(
        ImageFilter.MaxFilter(k) if op == "max" else ImageFilter.MinFilter(k)
    )
    return np.asarray(im) > 127


def _scan(m):
    h, w = m.shape
    idx = np.broadcast_to(np.arange(w, dtype=np.int32), (h, w))
    big = np.int32(10**6)
    left = np.where(m, idx, -big)
    np.maximum.accumulate(left, axis=1, out=left)
    right = np.where(m, idx, big)[:, ::-1]
    right = np.minimum.accumulate(right, axis=1)[:, ::-1]
    out = (right - left).astype(np.float32)
    out[(left == -big) | (right == big)] = np.inf
    return out


def _shear(m, sg):
    h, w = m.shape
    s = np.zeros((h, w + h), m.dtype)
    for i in range(h):
        o = i if sg > 0 else h - 1 - i
        s[i, o : o + w] = m[i]
    return s


def _unshear(s, w, sg):
    h = s.shape[0]
    out = np.empty((h, w), s.dtype)
    for i in range(h):
        o = i if sg > 0 else h - 1 - i
        out[i] = s[i, o : o + w]
    return out


def between(red, mpx):
    """Spacing (m) of the red edges enclosing each pixel, the minimum over 4 directions."""
    w = red.shape[1]
    best = _scan(red)
    best *= mpx
    np.minimum(best, _scan(np.ascontiguousarray(red.T)).T * mpx, out=best)
    for sg in (1, -1):
        d = _scan(np.ascontiguousarray(_shear(red, sg).T)).T
        d *= mpx * np.sqrt(2)
        np.minimum(best, _unshear(d, w, sg), out=best)
        del d
    return best


def thin(m):
    """Zhang-Suen thinning."""
    m = m.astype(np.uint8).copy()
    m[0, :] = m[-1, :] = m[:, 0] = m[:, -1] = 0
    while True:
        changed = 0
        for step in (0, 1):
            p = np.pad(m, 1)
            p2, p3, p4 = p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:]
            p5, p6, p7 = p[2:, 2:], p[2:, 1:-1], p[2:, :-2]
            p8, p9 = p[1:-1, :-2], p[:-2, :-2]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9]
            bsum = sum(n.astype(np.int8) for n in nb)
            seq = nb + [p2]
            trans = sum(
                ((seq[k] == 0) & (seq[k + 1] == 1)).astype(np.int8) for k in range(8)
            )
            if step == 0:
                c = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                c = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            rm = (m == 1) & (bsum >= 2) & (bsum <= 6) & (trans == 1) & c
            n = int(rm.sum())
            if n:
                m[rm] = 0
                changed += n
        if not changed:
            return m.astype(bool)


# --- graph --------------------------------------------------------------------------------


def trace(sk):
    ys, xs = np.nonzero(sk)
    on = set(zip(ys.tolist(), xs.tolist(), strict=True))

    def nb(p):
        return [(p[0] + a, p[1] + b) for a, b in NB8 if (p[0] + a, p[1] + b) in on]

    deg = {p: len(nb(p)) for p in on}
    nodes = {p for p, d in deg.items() if d != 2}
    used, paths = set(), []
    for n0 in nodes:
        for nx in nb(n0):
            if (n0, nx) in used:
                continue
            path, prev, cur = [n0, nx], n0, nx
            used.add((n0, nx))
            while cur not in nodes:
                nxt = [q for q in nb(cur) if q != prev and q not in path[-3:]]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                path.append(cur)
            used.add((path[-1], path[-2]))
            paths.append(path)
    return paths, deg


def _node_ids(deg):
    """Skeleton pixels that are not mid-line (ends, junctions) grouped into 8-connected
    blobs: one node id per blob."""
    node_pix = {p for p, d in deg.items() if d != 2}
    nid, lab = {}, 0
    for p in node_pix:
        if p in nid:
            continue
        stack, nid[p] = [p], lab
        while stack:
            q = stack.pop()
            for a, b in NB8:
                r = (q[0] + a, q[1] + b)
                if r in node_pix and r not in nid:
                    nid[r] = lab
                    stack.append(r)
        lab += 1
    return nid


def simplify_graph(edges, spur_m=20.0, rounds=6):
    """Drop dead-end spurs shorter than spur_m (repeatedly: thinning leaves whiskers at every
    dot, letter and junction; small loops too), then join chains through nodes of degree 2
    into one edge."""
    import collections

    for _ in range(rounds):
        deg = collections.Counter()
        for e in edges:
            deg[e["a"]] += 1
            deg[e["b"]] += 1
        keep = [
            e
            for e in edges
            if not (
                (deg[e["a"]] == 1 or deg[e["b"]] == 1 or e["a"] == e["b"])
                and e["line"].length * MPX < spur_m
            )
        ]
        if len(keep) == len(edges):
            break
        edges = keep
    ed = dict(enumerate(edges))
    inc = collections.defaultdict(set)
    for k, e in ed.items():
        inc[e["a"]].add(k)
        inc[e["b"]].add(k)
    nxt = len(ed)
    for node in list(inc):
        ks = inc.get(node)
        if not ks or len(ks) != 2:
            continue
        k1, k2 = ks
        e1, e2 = ed[k1], ed[k2]
        c1, a1 = list(e1["line"].coords), e1["a"]
        if e1["a"] == node:  # orient e1 to end at the node
            c1, a1 = c1[::-1], e1["b"]
        c2, b2 = list(e2["line"].coords), e2["b"]
        if e2["b"] == node:  # orient e2 to start at the node
            c2, b2 = c2[::-1], e2["a"]
        if a1 == node or b2 == node:
            continue  # a loop through this node
        del ed[k1], ed[k2]
        for n_, k_ in ((e1["a"], k1), (e1["b"], k1), (e2["a"], k2), (e2["b"], k2)):
            inc[n_].discard(k_)
        ed[nxt] = {"line": LineString(c1 + c2), "a": a1, "b": b2}
        inc[a1].add(nxt)
        inc[b2].add(nxt)
        nxt += 1
    return list(ed.values())


def build_edges(mask):
    """Road mask (pooled by DS) -> cleaned edge graph, lines in full-resolution px."""
    paths, deg = trace(thin(mask))
    nid = _node_ids(deg)
    edges, seen = [], set()
    for p in paths:
        if len(p) < 3:
            continue
        key = (p[0], p[-1]) if p[0] <= p[-1] else (p[-1], p[0])
        if key in seen and len(p) < 6:
            continue
        seen.add(key)
        rr = np.array([q[0] for q in p], float) * DS + DS / 2
        cc = np.array([q[1] for q in p], float) * DS + DS / 2
        edges.append(
            {
                "line": LineString(np.column_stack([cc, rr])),
                "a": nid[p[0]],
                "b": nid[p[-1]],
            }
        )
    edges = simplify_graph(edges)
    for e in edges:
        e["line"] = e["line"].simplify(1.5)
    return edges


def line_len_m(edge):
    return edge["line"].length * MPX


def end_dir(line, at_start, span_px=18.0):
    """Unit direction pointing out of the line at that end."""
    s = min(span_px, line.length)
    if at_start:
        p0, p1 = line.interpolate(s), line.interpolate(0)
    else:
        p0, p1 = line.interpolate(line.length - s), line.interpolate(line.length)
    v = np.array([p1.x - p0.x, p1.y - p0.y])
    n = np.linalg.norm(v)
    return v / n if n else v


def propagate(edges, labels_px, accept):
    """Each label claims the edges reached from it along straight continuations; the nearest
    label (path length) wins. labels_px: [(x, y, value, label_id)]; accept(edge, label_id).
    Returns
    {edge index: (value, label_id, path_px)}."""
    gap_j, gap_l = GAP_JUNCTION_M / MPX, GAP_LABEL_M / MPX
    max_px = PROP_MAX_M / MPX
    ends = []
    for i, e in enumerate(edges):
        c = e["line"].coords
        ends.append((c[0][0], c[0][1], i, True))
        ends.append((c[-1][0], c[-1][1], i, False))
    pts = np.array([[x, y] for x, y, _, _ in ends]) if ends else np.zeros((0, 2))
    tree = shapely.STRtree([e["line"] for e in edges])
    cosmax = np.cos(np.radians(DEFLECT_DEG))
    cos_straight = np.cos(np.radians(STRAIGHT_DEG))
    claim, pq = {}, []
    for x, y, v, lid in labels_px:
        p = Point(x, y)
        for i in tree.query(p, predicate="dwithin", distance=SEED_RADIUS_M / MPX):
            i = int(i)
            if accept(i, lid):
                heapq.heappush(pq, (edges[i]["line"].distance(p), i, v, lid, None))
    while pq:
        d, i, v, lid, ref = heapq.heappop(pq)
        if i in claim or d > max_px:
            continue
        claim[i] = (v, lid, d)
        e = edges[i]
        if (
            ref is None
            and e.get("gw")
            and line_len_m(e) >= SEED_MIN_M
            and e.get("ring_band", 0) < 0.3
        ):
            # the band width this chain continues on: short edges by the label circle are
            # cut by it, and a ring-road stroke hides the band, so neither sets it
            ref = e["gw"]
        line = e["line"]
        for at_start in (True, False):
            x, y = line.coords[0] if at_start else line.coords[-1]
            out = end_dir(line, at_start)
            dist = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
            cands = []
            for k in np.nonzero(dist <= gap_l)[0]:
                _, _, j, js = ends[k]
                if j == i or j in claim or not accept(j, lid):
                    continue
                j_gw = edges[j].get("gw")
                if (
                    ref
                    and j_gw
                    and edges[j].get("ring_band", 0) < 0.3
                    and not (GW_MIN * ref <= j_gw <= GW_MAX * ref)
                ):
                    continue  # the drawn band changes width: another road (e.g. a layout street)
                align = float(out @ -end_dir(edges[j]["line"], js))
                if align < cosmax:
                    continue
                gap = float(dist[k])
                if gap > gap_j:  # a longer gap must lie straight ahead, not beside
                    vec = pts[k] - np.array([x, y])
                    if (
                        float(out @ vec) <= 0
                        or abs(out[0] * vec[1] - out[1] * vec[0])
                        > LATERAL_M / MPX + 0.1 * gap
                    ):
                        continue
                cands.append((align - gap / gap_l * 0.1, j, gap, align))
            if not cands:
                continue
            # the straightest continuation, plus any other near-straight one (a short stub
            # at a junction must not end the chain)
            cands.sort(reverse=True)
            for n, (_key, j, gap, align) in enumerate(cands):
                if n == 0 or align >= cos_straight:
                    heapq.heappush(pq, (d + line.length + gap, j, v, lid, ref))
    return claim


def ray_width(red, x, y, nx, ny, rmax_m=26.0, fan_px=6):
    """Centre-to-centre spacing (m) of the red ROW edges either side of (x, y) along the
    normal. The edges are dashed: parallel rays offset up to +-fan_px along the road take the
    nearest hit per side."""
    h, w = red.shape
    steps = np.arange(1.5 / MPX, rmax_m / MPX)
    tx, ty = ny, -nx
    out = []
    for s in (1, -1):
        best = None
        for o in range(-fan_px, fan_px + 1, 2):
            cx = (x + o * tx + s * nx * steps).astype(int)
            cy = (y + o * ty + s * ny * steps).astype(int)
            ok = (cx >= 0) & (cy >= 0) & (cx < w) & (cy < h)
            hit = np.nonzero(red[cy[ok], cx[ok]])[0]
            if len(hit):
                d = steps[ok][hit[0]] + 0.5  # +0.5 px: centre of a ~1 px edge line
                best = d if best is None else min(best, d)
        if best is None:
            return None
        out.append(best)
    return (out[0] + out[1]) * MPX


def band_width(road, x, y, nx, ny, rmax_m=40.0, gap_px=8):
    """Width (m) of the drawn road band (grey / pale) across (x, y) along the normal; black
    overlay lines and boundary dots up to gap_px wide do not end the band."""
    h, w = road.shape
    steps = np.arange(0, rmax_m / MPX)
    out = []
    for s in (1, -1):
        cx = (x + s * nx * steps).astype(int)
        cy = (y + s * ny * steps).astype(int)
        ok = (cx >= 0) & (cy >= 0) & (cx < w) & (cy < h)
        v = road[cy[ok], cx[ok]]
        miss, last = 0, 0
        for k, on in enumerate(v):
            if on:
                miss, last = 0, k
            else:
                miss += 1
                if miss > gap_px:
                    break
        out.append(last + 0.5)
    return (out[0] + out[1]) * MPX


def measure(edge, red, grey, pale, roadband, band, station_m=8.0):
    line = edge["line"]
    n = max(2, int(line.length * MPX / station_m))
    ws, gws, g, pl, bd = [], [], 0, 0, 0
    for k in range(1, n):
        a = line.interpolate((k - 0.3) / n, normalized=True)
        b = line.interpolate((k + 0.3) / n, normalized=True)
        m = line.interpolate(k / n, normalized=True)
        tx, ty = b.x - a.x, b.y - a.y
        tl = float(np.hypot(tx, ty))
        if tl:
            wd = ray_width(red, m.x, m.y, -ty / tl, tx / tl)
            if wd is not None:
                ws.append(wd)
        yi, xi = int(m.y), int(m.x)
        in_ring = bool(
            band[min(max(0, yi), band.shape[0] - 1), min(max(0, xi), band.shape[1] - 1)]
        )
        if tl and not in_ring:
            # a ring-road stroke hides the band; label circles read under 2 m and drop out
            gw = band_width(roadband, m.x, m.y, -ty / tl, tx / tl)
            if gw >= 2.0:
                gws.append(gw)
        g += bool(grey[max(0, yi - 3) : yi + 4, max(0, xi - 3) : xi + 4].any())
        pl += bool(pale[max(0, yi - 3) : yi + 4, max(0, xi - 3) : xi + 4].any())
        bd += bool(
            band[min(max(0, yi), band.shape[0] - 1), min(max(0, xi), band.shape[1] - 1)]
        )
    st = n - 1
    ok = len(ws) >= max(2, 0.4 * st)
    edge.update(
        {
            "w": float(np.median(ws)) if ok else None,
            "w_iqr": [round(float(np.percentile(ws, q)), 2) for q in (25, 75)]
            if ok
            else None,
            "stations": st,
            "stations_red": len(ws),
            "grey": round(g / st, 2),
            "pale": round(pl / st, 2),
            "ring_band": round(bd / st, 2),
            # drawn road band (grey / pale) width: continuity check for label claims
            "gw": float(np.median(gws)) if len(gws) >= max(1, 0.25 * st) else None,
            "gw_iqr": [round(float(np.percentile(gws, q)), 2) for q in (25, 75)]
            if gws
            else None,
            "gw_n": len(gws),
        }
    )


# --- vectors and text ---------------------------------------------------------------------


def text_items(page, box, label="anekal"):
    """Width labels, road names, and every text box (to mask coloured lettering)."""
    labels, names, boxes = [], [], []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                t = s["text"].strip()
                x0, y0, x1, y1 = s["bbox"]
                if not t or not (box[0] < x0 < box[2] and box[1] < y0 < box[3]):
                    continue
                boxes.append((x0 - 1, y0 - 1, x1 + 1, y1 + 1))
                col = f"#{s['color']:06x}"
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if col == NAME_COLOUR and re.fullmatch(
                    r"(NH|SH|MDR|ODR)\s*\d+[A-Z]?", t
                ):
                    names.append({"x": cx, "y": cy, "name": re.sub(r"\s+", " ", t)})
                    continue
                if label != "anekal":
                    # bold numbers in circles (e.g. Hoskote atlas); survey numbers are regular
                    m = re.fullmatch(r"(\d{1,2})", t)
                    if (
                        m
                        and "Bold" in s["font"]
                        and s["size"] >= label["bold_min_size"]
                        and int(m.group(1)) in label.get("values", range(6, 100))
                    ):
                        labels.append(
                            {
                                "x": cx,
                                "y": cy,
                                "value": int(m.group(1)),
                                "kind": "circle",
                            }
                        )
                    continue
                if round(s["size"], 1) != 7.9 or s["color"] != 0:
                    continue
                m = re.fullmatch(r"(\d{1,2})(m?)", t)
                if m and (m.group(2) or "Bold" in s["font"]):
                    labels.append(
                        {
                            "x": cx,
                            "y": cy,
                            "value": int(m.group(1)),
                            "kind": "m" if m.group(2) else "bare",
                        }
                    )
    return labels, names, boxes


def ring_strokes(page, box):
    out = []
    for d in page.get_drawings():
        c = hx(d.get("color"))
        wd = d.get("width") or 0
        if d["type"] != "s" or c not in RING or wd < 3:
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
            elif it[0] == "c":
                a, b = it[1], it[4]
            else:
                continue
            if box[0] < a.x < box[2] and box[1] < a.y < box[3]:
                out.append((c, wd, [(a.x, a.y), (b.x, b.y)]))
    return out


def burn(shape_hw, sx, sy, box, rects=(), lines=()):
    """A full-resolution mask of page-point rectangles and thick lines."""
    im = Image.new("L", (shape_hw[1], shape_hw[0]), 0)
    dr = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in rects:
        dr.rectangle(
            [
                (x0 - box[0]) * sx,
                (y0 - box[1]) * sy,
                (x1 - box[0]) * sx,
                (y1 - box[1]) * sy,
            ],
            fill=1,
        )
    for (x0, y0), (x1, y1), wpt in lines:
        dr.line(
            [
                ((x0 - box[0]) * sx, (y0 - box[1]) * sy),
                ((x1 - box[0]) * sx, (y1 - box[1]) * sy),
            ],
            fill=1,
            width=max(1, int(wpt * sx)),
        )
    return np.asarray(im, dtype=bool)


# --- sheet --------------------------------------------------------------------------------


def vector_strokes(page, box, styles):
    """Page-point segments of strokes in the given [colour, width] styles (ROW edges drawn as
    vectors, e.g. the Hoskote atlas): burnt into the red mask instead of reading raster red,
    which land-use fills share there."""
    want = {(c, round(w, 2)) for c, w in styles}
    out = []
    for d in page.get_drawings():
        if (
            d["type"] not in ("s", "fs")
            or (hx(d.get("color")), round(d.get("width") or 0, 2)) not in want
        ):
            continue
        for it in d["items"]:
            if it[0] == "l":
                pts = [it[1], it[2]]
            elif it[0] == "c":
                pts = [it[1], it[4]]
            elif it[0] == "re":
                r_ = it[1]
                pts = [r_.tl, r_.tr, r_.br, r_.bl, r_.tl]
            else:
                continue
            for a, b in itertools.pairwise(pts):
                if box[0] - 5 < a.x < box[2] + 5 and box[1] - 5 < a.y < box[3] + 5:
                    out.append(
                        ((a.x, a.y), (b.x, b.y), max(d.get("width") or 0.5, 0.5))
                    )
    return out


def extract(page, box, log=print, style=None) -> dict:
    global MPX
    t0 = time.time()
    st = {**ANEKAL_STYLE, **(style or {})}
    MPX = st["scale"] * 0.0254 / DPI  # metres per raster px at this sheet's scale
    global PROP_MAX_M
    PROP_MAX_M = float(st.get("prop_max_m", PROP_MAX_M))  # label carry along a road
    red, grey, pale, sx, sy = colour_masks(page, box, style=st)
    labels, names, boxes = text_items(page, box, st["label"])
    rings = ring_strokes(page, box) if st["rings"] else []
    if st["red"] != "raster":
        red |= burn(
            red.shape, sx, sy, box, lines=vector_strokes(page, box, st["red"]["vector"])
        )
    # coloured lettering is not a ROW edge; ring-road bands are drawn from their vectors
    red &= ~burn(red.shape, sx, sy, box, rects=boxes)
    band = burn(
        red.shape, sx, sy, box, lines=[(p[0], p[1], wd + 6.0) for _c, wd, p in rings]
    )
    red &= ~band
    mpx = MPX * DS
    r2 = morph(pool(red, DS), 3, "max")  # bridge dash gaps (mask membership only)
    sp = between(r2, mpx)
    corridor = ((sp <= WMAX_M) & (sp >= WMIN_M)) | r2
    if st["wmax"] > WMAX_M:
        # wide ROWs (45-90 m, drawn to scale): spacing that wide also spans small blocks
        # between two roads, so only where the grey / pale road band is dense (wide roads
        # are grey inside, blocks are land-use colour)
        band_pool = pool(grey | pale, DS)
        k = 2 * round(8.0 / mpx) + 1
        dense = (
            np.asarray(
                Image.fromarray(band_pool.astype(np.uint8) * 255).filter(
                    ImageFilter.BoxBlur(k // 2)
                )
            )
            >= 64
        )
        corridor |= (sp > WMAX_M) & (sp <= st["wmax"]) & dense
        del band_pool, dense
    del sp, r2
    road = morph(morph(pool(grey | pale, DS), 3, "max"), 3, "min") | morph(
        morph(corridor, 3, "max"), 3, "min"
    )
    del corridor
    edges = build_edges(road)
    del road
    roadband = grey | pale
    for e in edges:
        measure(e, red, grey, pale, roadband, band)
        if not st.get("edges_to_scale", True):
            # small-scale sheets draw narrow roads as fixed symbols: the edge spacing is not
            # a width there (labels only); red presence still tells widening / proposed
            e["w"] = None
    del roadband
    log(f"  {len(edges)} edges, {time.time() - t0:.0f}s")

    def tol(v):
        return max(3.0, 0.15 * v)

    lab_px = [
        ((lb["x"] - box[0]) * sx, (lb["y"] - box[1]) * sy, lb["value"], k)
        for k, lb in enumerate(labels)
    ]

    def accept(i, lid):
        e, lb = edges[i], labels[lid]
        if e["w"] is not None:
            return abs(e["w"] - lb["value"]) <= tol(lb["value"])
        # an 'NNm' label marks a red-edged road: it does not run onto a ring-road band
        return not (lb["kind"] == "m" and e["ring_band"] > 0.5)

    claim = propagate(edges, lab_px, accept)

    def to_pt(line):
        c = np.asarray(line.coords)
        return (
            np.column_stack([box[0] + c[:, 0] / sx, box[1] + c[:, 1] / sy])
            .round(2)
            .tolist()
        )

    out_edges = []
    for i, e in enumerate(edges):
        cl = claim.get(i)
        # an unlabelled existing road is kept with its drawn band width (the sheets draw the
        # grey band to scale; open-decisions #78), else dropped
        small_ok = (
            st.get("keep_small", True)
            and bool(e["gw"])
            and e["grey"] >= 0.5
            and e["line"].length * MPX >= SMALL_MIN_M
        )
        if cl is None and e["w"] is None and not small_ok:
            continue
        out_edges.append(
            {
                "pt": to_pt(e["line"]),
                "len_m": round(e["line"].length * MPX, 1),
                "label": cl[0] if cl else None,
                "label_id": cl[1] if cl else None,
                "label_path_m": round(cl[2] * MPX, 1) if cl else None,
                **{
                    k: e[k]
                    for k in (
                        "w",
                        "w_iqr",
                        "stations",
                        "stations_red",
                        "grey",
                        "pale",
                        "ring_band",
                        "gw",
                        "gw_iqr",
                        "gw_n",
                    )
                },
            }
        )
    ring_out = {}
    for c, wd, p in rings:
        ring_out.setdefault(c, []).append(p)
    return {
        "edges": out_edges,
        "labels": labels,
        "names": names,
        "rings": [
            {
                "colour": c,
                "class": RING[c][0],
                "row_m": RING[c][1],
                "segments": [[[round(v, 2) for v in q] for q in sg] for sg in segs],
            }
            for c, segs in ring_out.items()
        ],
        "secs": round(time.time() - t0, 1),
    }


def main() -> None:
    job = json.loads(sys.stdin.read())
    doc = pymupdf.open(job["source_path"])
    res = extract(
        doc[job["page"]],
        tuple(job["box"]),
        log=lambda m: print(m, file=sys.stderr),
        style=job.get("style"),
    )
    doc.close()
    sys.stdout.write(json.dumps(res))


if __name__ == "__main__":
    main()
