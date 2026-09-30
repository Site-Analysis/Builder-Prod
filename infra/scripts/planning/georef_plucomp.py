#!/usr/bin/env python3
"""Georeference the BDA RMP 2031 Proposed Land Use composite (PLUCOMP) to EPSG:32643.

Usage:
    python georef_plucomp.py --data-root <dir>      (or set PLANNING_DATA_ROOT)

Method (no KGIS; OpenStreetMap only):
  1. Coarse: raster water blobs <-> OSM water polygons. Seeded by the lakes labelled on
     the sheet (name match), then mutual-nearest + area check + RANSAC affine.
  2. Check points, chosen before the fine fit and never used in it: road junctions
     (degree >= 3) on PLUCOMP's vector road network paired with OSM major/secondary/
     tertiary junctions, isolated by ISOLATION_M on both sides.
  3. Fine: trimmed ICP (affine) of PLUCOMP's vector road network (grey #b2b2b2) onto OSM
     motorway..tertiary lines, excluding samples within EXCLUDE_M of any check point.
  4. Report check-point RMSE overall and per quadrant; 2nd-order polynomial for comparison.

OSM extracts are cached in <data-root>/osm/. Output: <data-root>/georef/BDA-RMP2031-PLUCOMP.json
Needs pymupdf, numpy, shapely, pyproj (see requirements.txt).
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
import pymupdf
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

DOC_ID = "BDA-RMP2031-PLUCOMP"
PLAN_ID = "BDA-RMP2031"
BBOX = "12.60,77.20,13.35,77.95"  # S,W,N,E around the BDA LPA
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_QUERIES = {
    "water": f'(way["natural"="water"]({BBOX});relation["natural"="water"]({BBOX});way["landuse"="reservoir"]({BBOX}););',
    "major": f'way["highway"~"^(motorway|trunk|primary|motorway_link|trunk_link|primary_link)$"]({BBOX});',
    "sec": f'way["highway"~"^(secondary|tertiary)$"]({BBOX});',
}
ROAD_STYLE = (
    "#b2b2b2",
    0.96,
)  # PLUCOMP vector road network (matches OSM secondary/tertiary)
WATER = ("#97dbf2", "#4065eb")
SAMPLE_PT = 2.8  # ~1 mm between road samples
ISOLATION_M = 250.0
CHECK_TOL_M = 120.0
EXCLUDE_M = 200.0
TRIM_SCHEDULE_M = [60, 45, 35, 30, 25, 25, 25, 25, 25, 25]
SEED = 2031


def hx(c):
    return None if c is None else "#" + "".join(f"{round(v * 255):02x}" for v in c[:3])


def osm(data_root, name):
    path = os.path.join(data_root, "osm", f"{name}.json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = urllib.parse.urlencode(
            {"data": f"[out:json][timeout:280];{OSM_QUERIES[name]}out geom;"}
        ).encode()
        for ep in OVERPASS:
            try:
                req = urllib.request.Request(
                    ep, data=body, headers={"User-Agent": "builder-prod-planning/1.0"}
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    raw = r.read()
                json.loads(raw)
                with open(path, "wb") as f:
                    f.write(raw)
                break
            except Exception as e:  # noqa: BLE001 - try the next mirror
                print(f"  overpass {ep} failed for {name}: {e}", file=sys.stderr)
        else:
            sys.exit(f"error: could not fetch OSM {name}")
    return json.load(open(path, encoding="utf-8"))["elements"]


# ---------------------------------------------------------------- affine helpers
def fit_affine(P, G):
    coef, *_ = np.linalg.lstsq(np.column_stack([P, np.ones(len(P))]), G, rcond=None)
    return coef


def apply_affine(c, P):
    return np.column_stack([P, np.ones(len(P))]) @ c


def poly2_terms(P):
    x, y = P[:, 0], P[:, 1]
    return np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])


def fit_poly2(P, G):
    coef, *_ = np.linalg.lstsq(poly2_terms(P), G, rcond=None)
    return coef


def ransac_affine(P, G, thr, rng, n=2000):
    best = None
    for _ in range(n):
        s = rng.choice(len(P), 3, replace=False)
        inl = np.hypot(*(apply_affine(fit_affine(P[s], G[s]), P) - G).T) < thr
        if best is None or inl.sum() > best.sum():
            best = inl
    return best


# ---------------------------------------------------------------- 1. coarse from lakes
def raster_water_blobs(doc, page):
    strips = sorted(
        [x for x in page.get_image_info(xrefs=True) if x["width"] == 9598],
        key=lambda x: x["bbox"][1],
    )
    rows, row_pt = [], []
    for s in strips:
        pix = pymupdf.Pixmap(doc, s["xref"])
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[
            ..., :3
        ]
        v = (
            (a[..., 0].astype(np.uint32) << 16)
            | (a[..., 1].astype(np.uint32) << 8)
            | a[..., 2]
        )
        rows.append(np.isin(v, [int(c[1:], 16) for c in WATER]))
        b = s["bbox"]
        row_pt.append(b[1] + (np.arange(pix.height) + 0.5) * (b[3] - b[1]) / pix.height)
    water = np.vstack(rows)
    row_pt = np.concatenate(row_pt)
    b0 = strips[0]["bbox"]
    W = water.shape[1]
    col_pt = b0[0] + (np.arange(W) + 0.5) * (b0[2] - b0[0]) / W
    pt_per_px = (b0[2] - b0[0]) / W
    F = 4
    Hc, Wc = water.shape[0] // F, W // F
    blk = (
        water[: Hc * F, : Wc * F]
        .reshape(Hc, F, Wc, F)
        .sum(axis=(1, 3))
        .astype(np.int32)
    )
    mask = blk > 0
    big = np.int64(Hc * Wc + 1)
    lab = np.where(mask, np.arange(Hc * Wc, dtype=np.int64).reshape(Hc, Wc), big)
    while True:
        m = lab.copy()
        for dy, dx in (
            (0, 1),
            (0, -1),
            (1, 0),
            (-1, 0),
            (1, 1),
            (-1, -1),
            (1, -1),
            (-1, 1),
        ):
            sh = np.full_like(lab, big)
            sh[max(-dy, 0) : Hc + min(-dy, 0), max(-dx, 0) : Wc + min(-dx, 0)] = lab[
                max(dy, 0) : Hc + min(dy, 0), max(dx, 0) : Wc + min(dx, 0)
            ]
            m = np.minimum(m, sh)
        m = np.where(mask, m, big)
        flat = m.ravel()
        ok = flat < big
        flat[ok] = flat[flat[ok]]
        m = flat.reshape(Hc, Wc)
        if np.array_equal(m, lab):
            break
        lab = m
    ys, xs = np.nonzero(mask)
    w = blk[ys, xs].astype(np.float64)
    _, inv = np.unique(lab[ys, xs], return_inverse=True)
    area = np.bincount(inv, weights=w)
    cy = np.bincount(inv, weights=w * (ys * F + F / 2)) / area
    cx = np.bincount(inv, weights=w * (xs * F + F / 2)) / area
    keep = area >= 300
    blobs = np.column_stack(
        [
            np.interp(cx[keep], np.arange(W), col_pt),
            np.interp(cy[keep], np.arange(len(row_pt)), row_pt),
            area[keep],
        ]
    )
    return blobs, pt_per_px


def osm_water(elements, tr):
    out, names = [], []
    for e in elements:
        tags = e.get("tags", {})
        if tags.get("water") in ("drain", "river", "canal", "stream", "wastewater"):
            continue
        try:
            if e["type"] == "way" and len(e.get("geometry", [])) >= 4:
                g = Polygon([tr.transform(q["lon"], q["lat"]) for q in e["geometry"]])
            elif e["type"] == "relation":
                outers = [
                    LineString(
                        [tr.transform(q["lon"], q["lat"]) for q in m["geometry"]]
                    )
                    for m in e.get("members", [])
                    if m.get("role") == "outer" and m.get("geometry")
                ]
                g = unary_union(list(polygonize(unary_union(outers))))
            else:
                continue
            g = g.buffer(0)
        except Exception:  # noqa: BLE001, S112 - skip broken OSM geometry
            continue
        if g.is_empty or g.area < 15000:
            continue
        out.append((g.centroid.x, g.centroid.y, g.area))
        names.append(tags.get("name", ""))
    return np.array(out), names


def norm_name(s):
    return re.sub(r"\b(lake|kere|tank|keri)\b|[^a-z]", "", s.lower())


def coarse_from_lakes(doc, page, tr, data_root, rng):
    blobs, pt_per_px = raster_water_blobs(doc, page)
    O, names = osm_water(osm(data_root, "water"), tr)
    labels = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = " ".join(s["text"] for s in ln["spans"]).strip()
            if re.search(r"\b(lake|kere|tank|keri)\b", t, re.IGNORECASE):
                labels.append((t, ln["bbox"]))
    sp, sg = [], []
    for t, bb in labels:
        c = np.array([(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2])
        near = np.where(np.hypot(*(blobs[:, :2] - c).T) < 45)[0]
        hits = [i for i, n in enumerate(names) if n and norm_name(n) == norm_name(t)]
        if len(near) and hits:
            sp.append(blobs[near[np.argmax(blobs[near, 2])], :2])
            sg.append(O[max(hits, key=lambda i: O[i, 2]), :2])
    coef = fit_affine(np.array(sp), np.array(sg))
    for tol in (400, 150, 80):
        Gb = apply_affine(coef, blobs[:, :2])
        scale2 = abs(np.linalg.det(coef[:2]))
        pairs = []
        for i, g in enumerate(Gb):
            d = np.hypot(*(O[:, :2] - g).T)
            j = int(np.argmin(d))
            mutual = int(np.argmin(np.hypot(*(Gb - O[j, :2]).T))) == i
            ratio = blobs[i, 2] * pt_per_px**2 * scale2 / O[j, 2]
            if d[j] < tol and mutual and 0.4 < ratio < 2.5:
                pairs.append((i, j))
        P = blobs[[i for i, _ in pairs], :2]
        G = O[[j for _, j in pairs], :2]
        inl = ransac_affine(P, G, 40, rng)
        coef = fit_affine(P[inl], G[inl])
    return coef, int(inl.sum()), len(sp), pt_per_px


# ---------------------------------------------------------------- 2. roads and junctions
def plucomp_roads(page):
    segs = []
    for x in page.get_drawings():
        if (hx(x.get("color")), round(x.get("width") or 0, 2)) != ROAD_STYLE:
            continue
        for it in x["items"]:
            if it[0] == "l":
                segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
            elif it[0] == "c":
                segs.append((it[1].x, it[1].y, it[4].x, it[4].y))
    return np.array(segs)


def sample_segments(S):
    pts = []
    for x0, y0, x1, y1 in S:
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / SAMPLE_PT))
        t = np.arange(n) / n
        pts.append(np.column_stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]))
    return np.vstack(pts)


def junctions_from_segments(S, snap):
    """Nodes where >= 3 distinct directions meet (endpoints snapped to `snap`)."""
    nodes = {}
    for x0, y0, x1, y1 in S:
        for (ax, ay), (bx, by) in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            k = (round(ax / snap), round(ay / snap))
            ang = int((math.degrees(math.atan2(by - ay, bx - ax)) % 360) // 30)
            nodes.setdefault(k, set()).add(ang)
    return np.array(
        [(k[0] * snap, k[1] * snap) for k, a in nodes.items() if len(a) >= 3]
    )


def osm_lines_and_junctions(elements, tr):
    lines, inc = [], {}
    for e in elements:
        if e["type"] != "way" or len(e.get("geometry", [])) < 2:
            continue
        xy = [tr.transform(q["lon"], q["lat"]) for q in e["geometry"]]
        lines.append(LineString(xy))
        ids = e.get("nodes", [])
        for i, nid in enumerate(ids):
            nb = [j for j in (i - 1, i + 1) if 0 <= j < len(ids)]
            for j in nb:
                ang = int(
                    (
                        math.degrees(
                            math.atan2(xy[j][1] - xy[i][1], xy[j][0] - xy[i][0])
                        )
                        % 360
                    )
                    // 30
                )
                inc.setdefault(nid, [xy[i], set()])[1].add(ang)
    J = np.array([v[0] for v in inc.values() if len(v[1]) >= 3])
    return lines, J


def isolated(Q, radius):
    tree = STRtree(shapely.points(Q))
    return np.array([len(tree.query(shapely.points(q).buffer(radius))) == 1 for q in Q])


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    args = ap.parse_args()
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")
    rng = np.random.default_rng(SEED)
    t0 = time.time()
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    doc = pymupdf.open(os.path.join(args.data_root, "raw", PLAN_ID, f"{DOC_ID}.pdf"))
    page = doc[0]

    A0, n_lake, n_seed, pt_per_px = coarse_from_lakes(
        doc, page, tr, args.data_root, rng
    )
    print(
        f"1. coarse: {n_seed} labelled seed lakes -> {n_lake} lake control points ({time.time() - t0:.0f}s)"
    )

    S = plucomp_roads(page)
    samples = sample_segments(S)
    Jp = junctions_from_segments(S, snap=0.5)
    lines, Jo = osm_lines_and_junctions(
        osm(args.data_root, "major") + osm(args.data_root, "sec"), tr
    )
    tree = STRtree(lines)
    print(
        f"2. PLUCOMP road segments {len(S)}, samples {len(samples)}, junctions {len(Jp)}; OSM lines {len(lines)}, junctions {len(Jo)}"
    )

    # check points: chosen with the coarse fit, fixed before the fine fit
    Jpg = apply_affine(A0, Jp)
    iso_p = isolated(Jpg, ISOLATION_M)
    iso_o = isolated(Jo, ISOLATION_M)
    ot = STRtree(shapely.points(Jo))
    idx, d = ot.query_nearest(
        shapely.points(Jpg), return_distance=True, all_matches=False
    )
    pi, oj = idx
    keep = (d < CHECK_TOL_M) & iso_p[pi] & iso_o[oj]
    CP, CG = Jp[pi[keep]], Jo[oj[keep]]
    print(
        f"   check points (isolated {ISOLATION_M:.0f} m, paired < {CHECK_TOL_M:.0f} m under coarse fit): {len(CP)}"
    )

    # exclude road samples near check points from the fit
    sg = apply_affine(A0, samples)
    near = STRtree(shapely.buffer(shapely.points(CG), EXCLUDE_M)).query(
        shapely.points(sg), predicate="intersects"
    )[0]
    use = np.ones(len(samples), bool)
    use[np.unique(near)] = False
    P = samples[use]

    # 3. trimmed ICP (affine) onto OSM road lines
    A = A0.copy()
    for it, trim in enumerate(TRIM_SCHEDULE_M):
        G = apply_affine(A, P)
        gp = shapely.points(G)
        li, dist = tree.query_nearest(gp, return_distance=True, all_matches=False)
        tgt_lines = np.array(lines, dtype=object)[li[1]]
        proj = shapely.line_interpolate_point(
            tgt_lines, shapely.line_locate_point(tgt_lines, gp[li[0]])
        )
        Q = shapely.get_coordinates(proj)
        ok = dist < trim
        A_new = fit_affine(P[li[0]][ok], Q[ok])
        shift = np.abs(
            apply_affine(A_new, P[:: max(1, len(P) // 2000)])
            - apply_affine(A, P[:: max(1, len(P) // 2000)])
        ).max()
        A = A_new
        print(
            f"   ICP {it}: trim {trim} m, pairs {ok.sum()}, pair RMSE {np.sqrt(np.mean(dist[ok] ** 2)):.1f} m, max change {shift:.2f} m"
        )
    n_icp = int(ok.sum())

    # 4. check-point residuals
    R = apply_affine(A, CP) - CG
    r = np.hypot(*R.T)
    R0 = np.hypot(*(apply_affine(A0, CP) - CG).T)
    lin = A[:2]
    sx, sy = np.hypot(*lin[0]), np.hypot(*lin[1])
    m_px = math.sqrt(abs(np.linalg.det(lin))) * pt_per_px
    print(
        f"\n3. FIT affine (ICP on roads): {sx:.3f} x {sy:.3f} m/pt, rotation {math.degrees(math.atan2(lin[0, 1], lin[0, 0])):.2f} deg, "
        f"aniso {sx / sy:.4f}; scale 1:{sx / (0.0254 / 72):,.0f}; m_per_px {m_px:.2f}"
    )
    print(
        f"   CHECK POINTS n={len(r)}: RMSE {np.sqrt(np.mean(r**2)):.1f} m, median {np.median(r):.1f} m, p90 {np.percentile(r, 90):.1f} m, max {r.max():.0f} m"
        f"   (coarse lake fit on same points: RMSE {np.sqrt(np.mean(R0**2)):.1f} m)"
    )
    mx, my = np.median(CP[:, 0]), np.median(CP[:, 1])
    quad = []
    print("   quadrant | n | RMSE m | mean dE m | mean dN m")
    for name, s in (
        ("NW", (CP[:, 0] <= mx) & (CP[:, 1] <= my)),
        ("NE", (CP[:, 0] > mx) & (CP[:, 1] <= my)),
        ("SW", (CP[:, 0] <= mx) & (CP[:, 1] > my)),
        ("SE", (CP[:, 0] > mx) & (CP[:, 1] > my)),
    ):
        q = {
            "quadrant": name,
            "n": int(s.sum()),
            "rmse_m": float(np.sqrt(np.mean(r[s] ** 2))),
            "mean_de_m": float(R[s, 0].mean()),
            "mean_dn_m": float(R[s, 1].mean()),
        }
        quad.append(q)
        print(
            f"   {name} | {q['n']} | {q['rmse_m']:.1f} | {q['mean_de_m']:+.1f} | {q['mean_dn_m']:+.1f}"
        )
    # poly2 for comparison, same ICP pairs
    G = apply_affine(A, P)
    li, dist = tree.query_nearest(
        shapely.points(G), return_distance=True, all_matches=False
    )
    tl = np.array(lines, dtype=object)[li[1]]
    Q = shapely.get_coordinates(
        shapely.line_interpolate_point(
            tl, shapely.line_locate_point(tl, shapely.points(G)[li[0]])
        )
    )
    ok = dist < TRIM_SCHEDULE_M[-1]
    C2 = fit_poly2(P[li[0]][ok], Q[ok])
    r2 = np.hypot(*(poly2_terms(CP) @ C2 - CG).T)
    print(
        f"   poly2 (comparison only): check RMSE {np.sqrt(np.mean(r2**2)):.1f} m, median {np.median(r2):.1f} m"
    )

    out = os.path.join(args.data_root, "georef", f"{DOC_ID}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(
            {
                "doc_id": DOC_ID,
                "crs": "EPSG:32643",
                "input": "PDF page points (72/in), origin top-left",
                "georef_method": "affine_icp_osm_roads",
                "affine_page_to_32643": A.tolist(),
                "m_per_px": m_px,
                "lake_control_points": n_lake,
                "icp_pairs": n_icp,
                "check_points": {
                    "n": len(r),
                    "rmse_m": float(np.sqrt(np.mean(r**2))),
                    "median_m": float(np.median(r)),
                    "p90_m": float(np.percentile(r, 90)),
                    "max_m": float(r.max()),
                    "quadrants": quad,
                    "page_pt": CP.tolist(),
                    "osm_32643": CG.tolist(),
                    "residual_m": R.tolist(),
                },
                "poly2_check_rmse_m": float(np.sqrt(np.mean(r2**2))),
                "sources": "OpenStreetMap (ODbL) via Overpass; no KGIS",
            },
            f,
            indent=1,
        )
    print(f"wrote {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
