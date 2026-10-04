# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""BDA RMP 2031 plan roads from the PLUCOMP composite (the sheet the BDA zones come from;
open-decisions #80).

  python build_roads_bda.py [--geojson DIR] [--publish] [--spotcheck N]

The composite (A0, about 1:57,000; draft plan) draws the road network as vector centrelines
(grey #b2b2b2, 0.96 pt, one fixed width: not to scale) and prints the plan's proposed width
along them as text ("18 m"; ArialMT 6 pt, #4e4e4e, turned to follow the road). Each label is
snapped to the centreline beside it (nearest within 3 pt whose direction agrees within 25
deg); its width is carried along straight continuations of that road (roads_mob.propagate:
<= 600 m path, <= 30 deg, nearest label wins). Unlabelled pieces get no width. Placement: the
PLUCOMP affine of the zones (OSM roads ICP, RMSE about 10 m) plus the drawing's
generalisation at this scale (up to about 20 m): good for which road and its width, not for
measuring a parcel's widening area.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time

import build_roads as br
import numpy as np
import pymupdf
import qnit_fetch as qf
import roads_mob as rm
import shapely
import shapely.ops
from shapely.geometry import LineString, Point

PLAN_ID = "BDA-RMP2031"
DOC_ID = "BDA-RMP2031-PLUCOMP"
ROAD_STYLE = ("#b2b2b2", 0.96)
LABEL_STYLE = ("ArialMT", 6.0, 0x4E4E4E)
SNAP_PT = 3.0
ANGLE_DEG = 25.0
PLACEMENT_M = 20.0
PIECE_PT = 10.0


def log(m: str) -> None:
    print(m, flush=True)


def affine():
    with open(br.INDEX, encoding="utf-8") as f:
        ix = json.load(f)
    row = next(
        r for r in ix["rows"] if r["plan_id"] == PLAN_ID and r["kind"] == "zones"
    )
    return np.array(row["georef"]["affine_page_to_32643"]), row["georef"].get("rmse_m")


def to_utm(a, xy):
    xy = np.asarray(xy, float)
    return np.column_stack([xy, np.ones(len(xy))]) @ a


def network(page):
    segs = []
    for d in page.get_drawings():
        if (
            d["type"] != "s"
            or (rm.hx(d.get("color")), round(d.get("width") or 0, 2)) != ROAD_STYLE
        ):
            continue
        for it in d["items"]:
            if it[0] == "l":
                pts = [it[1], it[2]]
            elif it[0] == "c":
                pts = [it[1], it[2], it[3], it[4]]
            else:
                continue
            c = [(round(p.x, 2), round(p.y, 2)) for p in pts]
            if len(set(c)) > 1:
                segs.append(LineString(c))
    log(f"  road network: {len(segs)} segments")
    g = shapely.set_precision(
        shapely.union_all(shapely.set_precision(np.array(segs, dtype=object), 0.05)),
        0.05,
    )
    merged = shapely.line_merge(g)
    lines = []
    for ln in getattr(merged, "geoms", [merged]):
        if ln.length <= 0.2:
            continue
        # pieces of <= 10 pt (~200 m), so a label's carry is limited by distance, not by
        # how long the network edge between two junctions happens to be
        n = max(1, math.ceil(ln.length / PIECE_PT))
        lines.extend(
            shapely.ops.substring(ln, k * ln.length / n, (k + 1) * ln.length / n)
            for k in range(n)
        )
    log(f"  network edges: {len(lines)}, {sum(ln.length for ln in lines):.0f} pt")
    return lines


def labels(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                t = s["text"].strip()
                m = re.fullmatch(r"(\d{1,3})\s?m", t)
                if (
                    not m
                    or s["font"] != LABEL_STYLE[0]
                    or round(s["size"], 1) != LABEL_STYLE[1]
                ):
                    continue
                if s["color"] != LABEL_STYLE[2]:
                    continue
                x0, y0, x1, y1 = s["bbox"]
                dx, dy = ln["dir"]
                out.append(
                    {
                        "x": (x0 + x1) / 2,
                        "y": (y0 + y1) / 2,
                        "value": int(m.group(1)),
                        "dir": (dx, dy),
                    }
                )
    return out


def local_dir(line, p):
    d = line.project(p)
    a, b = (
        line.interpolate(max(0.0, d - 1.5)),
        line.interpolate(min(line.length, d + 1.5)),
    )
    v = np.array([b.x - a.x, b.y - a.y])
    n = np.linalg.norm(v)
    return v / n if n else v


def snap(lines, labs):
    """Each label to the centreline beside it: nearest within SNAP_PT whose direction agrees."""
    tree = shapely.STRtree(lines)
    cos_max = math.cos(math.radians(ANGLE_DEG))
    snapped, dist, ang = [], [], []
    for k, lb in enumerate(labs):
        p = Point(lb["x"], lb["y"])
        best = None
        for i in tree.query(p, predicate="dwithin", distance=SNAP_PT):
            ln = lines[int(i)]
            v = local_dir(ln, p)
            c = abs(v[0] * lb["dir"][0] + v[1] * lb["dir"][1])
            if c < cos_max:
                continue
            d = ln.distance(p)
            if best is None or d < best[0]:
                best = (d, int(i), c)
        if best is None:
            continue
        q = lines[best[1]].interpolate(lines[best[1]].project(p))
        snapped.append((q.x, q.y, lb["value"], k))
        dist.append(best[0])
        ang.append(math.degrees(math.acos(min(1.0, best[2]))))
    return snapped, dist, ang


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", default=None)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--spotcheck", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()
    src = br.doc_row(DOC_ID)
    a, rmse = affine()
    m_per_pt = float(np.hypot(a[0, 0], a[0, 1]))
    with qf.TempArea("build/roads_bda") as area:
        path, _ = qf.download(src["source_url"], area, "plucomp.pdf", src["sha256"])
        doc = pymupdf.open(path)
        page = doc[0]
        lines = network(page)
        labs = labels(page)
        snapped, dist, ang = snap(lines, labs)
        log(f"  labels: {len(labs)}, snapped {len(snapped)}")
        # propagation in page points: the module's metres-per-unit is this sheet's
        rm.MPX = m_per_pt
        rm.SEED_RADIUS_M = 0.6 * m_per_pt  # the seed is the snapped point on the line
        edges = [{"line": ln, "gw": None, "w": None, "ring_band": 0.0} for ln in lines]
        claim = rm.propagate(edges, snapped, lambda i, lid: True)
        feats = []
        for i, (v, lid, d_px) in claim.items():
            xy = to_utm(a, np.asarray(lines[i].coords))
            path_m = d_px * m_per_pt
            feats.append(
                {
                    "geom": LineString(xy),
                    "row_m": float(v),
                    "status": "plan_row_stated",
                    "width_source": "label",
                    "confidence": "HIGH"
                    if path_m <= br.LABEL_PATH_HIGH_M
                    else "MEDIUM",
                    "drawn_row_m": None,
                    "drawn_band_m": None,
                    "label_m": v,
                    "label_path_m": path_m,
                }
            )
        merged = br.merge_features(feats)
        total_pt = sum(ln.length for ln in lines)
        claimed_pt = sum(lines[i].length for i in claim)
        by_row = {}
        for f_ in merged:
            by_row[f"{f_['row_m']:g}"] = (
                by_row.get(f"{f_['row_m']:g}", 0.0) + f_["geom"].length / 1000
            )
        lab_utm = []
        for x, y, v, _k in snapped:
            e, n = to_utm(a, [(x, y)])[0]
            lab_utm.append({"e": float(e), "n": float(n), "value": v})
        qa = {
            "labels": len(labs),
            "labels_snapped": len(snapped),
            "label_to_line_pt": {
                "median": round(float(np.median(dist)), 2),
                "p90": round(float(np.percentile(dist, 90)), 2),
                "p99": round(float(np.percentile(dist, 99)), 2),
            },
            "label_angle_deg": {
                "median": round(float(np.median(ang)), 1),
                "p90": round(float(np.percentile(ang, 90)), 1),
            },
            "network_km": round(total_pt * m_per_pt / 1000, 1),
            "network_km_with_width": round(claimed_pt * m_per_pt / 1000, 1),
            "km_by_row_m": {
                k: round(v, 1)
                for k, v in sorted(by_row.items(), key=lambda kv: float(kv[0]))
            },
            "km_by_confidence": {
                c: round(
                    sum(f_["geom"].length for f_ in merged if f_["confidence"] == c)
                    / 1000,
                    1,
                )
                for c in ("HIGH", "MEDIUM")
            },
            "placement": f"PLUCOMP affine RMSE {rmse:.1f} m + drawing generalisation up to ~{PLACEMENT_M:g} m",
        }
        log(json.dumps(qa, indent=1))
        published = None
        if args.geojson:
            os.makedirs(args.geojson, exist_ok=True)
            out = os.path.join(args.geojson, f"{PLAN_ID}.roads.geojson")
            fc = br.geojson_features(merged, [], lab_utm)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(fc, fh, separators=(",", ":"))
            log(f"  geojson: {len(fc['features'])} features")
            if args.spotcheck:
                spot(
                    page,
                    lines,
                    claim,
                    snapped,
                    args.spotcheck,
                    os.path.join(args.geojson, f"{PLAN_ID}_spotcheck.png"),
                )
            if args.publish:
                published = br.publish(
                    out, len(fc["features"]), src, plan_id=PLAN_ID, doc_id=DOC_ID
                )
                published["placement_uncertainty_m"] = round(
                    (rmse or 10.0) + PLACEMENT_M, 1
                )
                republish_meta(published)
        doc.close()
    with open(br.INDEX, encoding="utf-8") as fh:
        ix = json.load(fh)
    ix.setdefault("roads", {})[PLAN_ID] = {
        "doc_id": DOC_ID,
        "source_url": src["source_url"],
        "sha256": src["sha256"],
        "status": src["status"],
        "status_label": src["status_label"],
        "georef": {
            "method": "PLUCOMP affine of the zone row (affine_icp_osm_roads)",
            "rmse_m": rmse,
        },
        "extraction": {
            "method": "vector_network_labels",
            "road_style": list(ROAD_STYLE),
            "label_style": [LABEL_STYLE[0], LABEL_STYLE[1], f"#{LABEL_STYLE[2]:06x}"],
            "snap_pt": SNAP_PT,
            "angle_deg": ANGLE_DEG,
            "label_propagation_m": rm.PROP_MAX_M,
        },
        "published": published,
        "qa": qa,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    with open(br.INDEX, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ix, fh, indent=1, sort_keys=False)
        fh.write("\n")
    log(f"done in {time.time() - t0:.0f} s")


def republish_meta(entry: dict) -> None:
    """Add the placement uncertainty to this plan's manifest entry (the service warns with it)."""
    import urllib.request

    import build_tiles as bt

    st = bt.Storage()
    with urllib.request.urlopen(
        f"{st.public('manifest.json')}?v={time.time()}", timeout=60
    ) as r:
        man = json.load(r)
    man.setdefault("roads", {})[PLAN_ID] = entry
    st.put(
        "manifest.json",
        json.dumps(man, indent=1).encode(),
        "application/json",
        "no-cache",
    )


def spot(page, lines, claim, snapped, n, out_png):
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(2031)
    pick = [
        snapped[k]
        for k in rng.choice(len(snapped), min(n, len(snapped)), replace=False)
    ]
    tree = shapely.STRtree(lines)
    tiles = []
    for x, y, v, _k in pick:
        r = 25.0
        clip = pymupdf.Rect(x - r, y - r, x + r, y + r)
        pm = page.get_pixmap(dpi=300, clip=clip, alpha=False)
        im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
        dr = ImageDraw.Draw(im)
        k = pm.width / (2 * r)
        for i in tree.query(shapely.box(*clip)):
            i = int(i)
            if i in claim:
                xy = [
                    ((px - clip.x0) * k, (py - clip.y0) * k)
                    for px, py in lines[i].coords
                ]
                col = (0, 90, 255) if claim[i][0] == v else (255, 0, 200)
                dr.line(xy, fill=col, width=3)
        dr.text((3, 3), f"{v} m", fill=(255, 0, 0))
        tiles.append(im.resize((300, 300)))
    sheet = Image.new("RGB", (5 * 300, ((len(tiles) + 4) // 5) * 300), "white")
    for j, t in enumerate(tiles):
        sheet.paste(t, ((j % 5) * 300, (j // 5) * 300))
    sheet.save(out_png)


if __name__ == "__main__":
    main()
