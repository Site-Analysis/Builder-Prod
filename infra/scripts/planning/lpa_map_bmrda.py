#!/usr/bin/env python3
"""LPA polygons from BMRDA's "Local Planning Areas in Bengaluru Metropolitan Region" map.

Usage:
    python lpa_map_bmrda.py --data-root <dir>

Source: BMRDA-LPA-MAP (strrpa.karnataka.gov.in "Local Planning Area Map", vector PDF,
1:125,000, legend with each LPA's fill colour and area). It has no coordinate grid, so it
is georeferenced by fitting an affine (iterative closest point on the outlines) to two LPA
polygons we already hold in EPSG:32643: Hoskote (Map No. 19 of the Hoskote atlas) and BDA
(RMP 2031 LPA). The fit is reported per LPA as outline distance and IoU where we have our
own polygon. These polygons are for village-to-authority assignment only (no zones).

Writes <data-root>/planning/zones/BMRDA-LPA-MAP_lpas.parquet and _qa.json.
"""

import argparse
import json
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import shapely
from extract_hoskote import CRS_M
from extract_plucomp import geoparquet

DOC = "raw/STRR/STRR-LPA-MAP.pdf"
# legend fill -> (authority code, LPA name, legend area km2)
FILLS = {
    "#ffffbe": ("STRR", "Satellite Town Ring Road LPA", 1016.92),
    "#fcd6b6": ("BMRDA-HSK", "Hoskote LPA", 475.46),
    "#e9c7fc": ("BMRDA-ANK", "Anekal LPA", 264.09),
    "#bee8ff": ("KANAKAPURA", "Kanakapura LPA", 1498.12),
    "#ffbebe": ("CHANNAPATNA", "Channapatna LPA", 439.11),
    "#b3fcb7": ("RAMANAGARA", "Ramanagara LPA", 172.52),
    "#00c5ff": ("GBBSC", "Greater Bengaluru-Bidadi Smart City", 141.54),
    "#fcb3ea": ("MAGADI", "Magadi LPA", 690.94),
    "#bed2ff": ("BMRDA-NLM", "Nelamangala LPA", 681.67),
    "#bbfce9": ("BIAAPA", "BIAAPA LPA", 1127.97),
    "#cccccc": ("BMICAPA", "Bangalore-Mysore Infrastructure Corridor", 426.24),
}
BDA_KM2 = 1219.50


def hx(c):
    return "#" + "".join(f"{round(v * 255):02x}" for v in c)


def rings(path):
    """Closed rings of one drawing (subpaths split where the pen jumps)."""
    out, cur = [], []
    for it in path["items"]:
        if it[0] == "re":
            r = it[1]
            out.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
            continue
        pts = [it[1], it[-1]] if it[0] in ("l", "c") else list(it[1])
        a, b = (pts[0].x, pts[0].y), (pts[-1].x, pts[-1].y)
        if cur and (abs(cur[-1][0] - a[0]) > 0.01 or abs(cur[-1][1] - a[1]) > 0.01):
            out.append(cur)
            cur = []
        if not cur:
            cur.append(a)
        cur.append(b)
    if cur:
        out.append(cur)
    return [r for r in out if len(r) >= 3]


def even_odd(rs):
    g = shapely.Polygon()
    for r in rs:
        p = shapely.make_valid(shapely.Polygon(r))
        g = shapely.symmetric_difference(g, p)
    return shapely.make_valid(g)


def fills(page):
    """{fill: (full, visible)}. Fills are painted in order and the STRR band (constituted
    2021) is painted last over the older LPAs: `full` is the LPA as drawn (its extent
    before STRR), `visible` what later fills leave uncovered (= the legend areas)."""
    seq = []
    for d in page.get_drawings():
        if d.get("fill") is None or d["rect"].width * d["rect"].height < 2000:
            continue
        h = hx(d["fill"])
        if h in FILLS:
            seq.append((h, even_odd(rings(d))))
    by = {}
    for k, (h, g) in enumerate(seq):
        later = [x[1] for x in seq[k + 1 :]]
        vis = shapely.difference(g, shapely.union_all(later)) if later else g
        f, v = by.get(h, (shapely.Polygon(), shapely.Polygon()))
        by[h] = (shapely.union(f, g), shapely.union(v, vis))
    return by


def affine_apply(A, g):
    from shapely.affinity import affine_transform

    return affine_transform(g, A)


def fit_icp(pairs, A0, iters=60):
    """pairs: [(map polygon in page coords, our polygon in EPSG:32643)]. Affine refined by
    nearest points between the outlines (map -> ours), least squares."""
    A = np.array(A0, float)
    samples = []
    for mp, ours in pairs:
        b = mp.boundary
        n = 600
        samples.append(
            (
                np.array(
                    [b.interpolate(i / n, normalized=True).coords[0] for i in range(n)]
                ),
                ours.boundary,
            )
        )
    for _ in range(iters):
        src, dst = [], []
        for P, ob in samples:
            T = np.column_stack(
                [
                    A[0] * P[:, 0] + A[1] * P[:, 1] + A[4],
                    A[2] * P[:, 0] + A[3] * P[:, 1] + A[5],
                ]
            )
            near = shapely.get_coordinates(
                shapely.shortest_line(shapely.points(T), ob)
            )[1::2]
            d = np.hypot(*(near - T).T)
            keep = d <= np.percentile(
                d, 80
            )  # trim outliers (boundaries that really differ)
            src.append(P[keep])
            dst.append(near[keep])
        S, D = np.vstack(src), np.vstack(dst)
        M = np.column_stack([S, np.ones(len(S))])
        cx, *_ = np.linalg.lstsq(M, D[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(M, D[:, 1], rcond=None)
        A = np.array([cx[0], cx[1], cy[0], cy[1], cx[2], cy[2]])
    return A


def outline_stats(mp_g, ours):
    b = mp_g.boundary
    P = np.array(
        [b.interpolate(i / 800, normalized=True).coords[0] for i in range(800)]
    )
    d = shapely.distance(shapely.points(P), ours.boundary)
    inter = shapely.intersection(mp_g, ours).area
    return {
        "outline_median_m": round(float(np.median(d)), 1),
        "outline_p90_m": round(float(np.percentile(d, 90)), 1),
        "iou": round(inter / shapely.union(mp_g, ours).area, 4),
    }


def read_lpa(path):
    t = pq.read_table(path)
    return shapely.union_all(
        shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    page = pymupdf.open(os.path.join(args.data_root, DOC))[0]
    polys = fills(page)
    # BDA on the map: the unfilled hole of the union of all LPA fills that is largest
    allu = shapely.union_all([f for f, _v in polys.values()])
    holes = [shapely.Polygon(r) for p in shapely.get_parts(allu) for r in p.interiors]
    bda_map = max(holes, key=lambda h: h.area)
    ours = {
        "BMRDA-HSK": read_lpa(os.path.join(zdir, "BMRDA-HSK-MP2031_lpa.parquet")),
        "BDA": read_lpa(os.path.join(zdir, "BDA-RMP2031_lpa.parquet")),
    }
    hsk_map = polys["#fcd6b6"][1]
    # initial similarity from the two centroids
    p1, p2 = np.array(hsk_map.centroid.coords[0]), np.array(bda_map.centroid.coords[0])
    q1, q2 = (
        np.array(ours["BMRDA-HSK"].centroid.coords[0]),
        np.array(ours["BDA"].centroid.coords[0]),
    )
    s = np.linalg.norm(q2 - q1) / np.linalg.norm(p2 - p1)
    A0 = [s, 0.0, 0.0, -s, *(q1 - s * np.array([p1[0], -p1[1]]))]
    A = fit_icp([(hsk_map, ours["BMRDA-HSK"]), (bda_map, ours["BDA"])], A0)
    m_per_pt = float(np.sqrt(abs(A[0] * A[3] - A[1] * A[2])))
    out_rows, geoms, qa_lpas = [], [], {}
    for code, f in (
        ("BMRDA-ANK", "BMRDA-ANK-MP2031_lpa.parquet"),
        ("BMRDA-NLM", "BMRDA-NLM-MP2031_lpa.parquet"),
    ):
        if os.path.exists(os.path.join(zdir, f)):
            ours[code] = read_lpa(os.path.join(zdir, f))
    for h, (code, name, km2) in FILLS.items():
        if h not in polys:
            continue
        full, vis = (shapely.make_valid(affine_apply(A, x)) for x in polys[h])
        q = {
            "legend_km2": km2,
            "visible_km2": round(vis.area / 1e6, 2),
            "pre_strr_km2": round(full.area / 1e6, 2),
        }
        if code in ours:
            q["vs_ours_visible"] = outline_stats(vis, ours[code])
            q["vs_ours_pre_strr"] = outline_stats(full, ours[code])
        qa_lpas[code] = q
        for kind, g in (("current", vis), ("pre_strr", full)):
            if kind == "pre_strr" and code == "STRR":
                continue
            out_rows.append(
                {"authority": code, "name": name, "extent": kind, "legend_km2": km2}
            )
            geoms.append(g)
    g = shapely.make_valid(affine_apply(A, bda_map))
    qa_lpas["BDA"] = {
        "legend_km2": BDA_KM2,
        "visible_km2": round(g.area / 1e6, 2),
        "vs_ours_visible": outline_stats(g, ours["BDA"]),
    }
    out_rows.append(
        {
            "authority": "BDA",
            "name": "BDA LPA (unfilled on the map)",
            "extent": "current",
            "legend_km2": BDA_KM2,
        }
    )
    geoms.append(g)
    geoparquet(
        os.path.join(zdir, "BMRDA-LPA-MAP_lpas.parquet"),
        pa.table(
            {
                k: [r[k] for r in out_rows]
                for k in ("authority", "name", "extent", "legend_km2")
            }
        ),
        np.array(geoms, dtype=object),
        CRS_M,
    )
    qa = {
        "affine_page_to_32643": list(map(float, A)),
        "m_per_pt": m_per_pt,
        "scale_implied": round(m_per_pt / 0.0254 * 72),
        "fit_on": ["BMRDA-HSK", "BDA"],
        "lpas": qa_lpas,
    }
    with open(os.path.join(zdir, "BMRDA-LPA-MAP_qa.json"), "w") as f:
        json.dump(qa, f, indent=1)
    print(json.dumps(qa, indent=1))


if __name__ == "__main__":
    main()
