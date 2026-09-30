#!/usr/bin/env python3
"""Extract BDA RMP 2031 proposed zones from the PLUCOMP composite into GeoParquet.

Usage:
    python extract_plucomp.py --data-root <dir>      (or set PLANNING_DATA_ROOT)

Needs georef_plucomp.py to have run (reads <data-root>/georef/BDA-RMP2031-PLUCOMP.json).

Steps:
  1. Classify the lossless raster by exact palette (infra/planning/legend_map.csv);
     other colours go to the nearest main colour (RGB distance).
  2. Symbols that hide the zone underneath become overlays, and the zone under them is
     filled from surrounding land pixels (lakes never act as a source):
     - NGT Buffer hatch: extent = hatch closed by HATCH_CLOSE_PX; zone parts flagged
       "zone inferred under hatch".
     - Stream symbol (teal core + thin light-blue casing): Zhang-Suen centreline overlay;
       zone parts flagged "zone inferred under stream symbol".
  3. Forest glyphs: "forest symbol area" overlay (closed FOREST_CLOSE_PX, dilated
     FOREST_DILATE_PX); the zone is the ground colour under the glyphs (white).
  4. White inside the LPA boundary -> "uncoloured" (never guessed); outside -> dropped.
  5. Polygonise (pixel runs -> per-tile coverage union -> per-class union), drop slivers
     under SLIVER_PX wide, transform to EPSG:32643, clip to the LPA boundary.
  6. Write <data-root>/planning/zones/BDA-RMP2031.parquet (+ _overlays.parquet: NGT,
     forest symbol area, stream centrelines) and
     <data-root>/planning/zones/BDA-RMP2031_qa.json.
"""

import argparse
import csv
import json
import os
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import shapely
from pyproj import CRS
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LEGEND_CSV = os.path.join(REPO, "infra", "planning", "legend_map.csv")
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
PLAN_ID = "BDA-RMP2031"
DOC_ID = "BDA-RMP2031-PLUCOMP"
STRIP_W = 9598
ROW_PT = 0.24  # page points per raster row (strips are 300 dpi)
HATCH_CLOSE_PX = 10
FOREST_CLOSE_PX = 30  # tree glyphs are ~60 px apart
FOREST_DILATE_PX = 2
SLIVER_PX = 2.0
STREAM_CASING_PX = 4  # light-blue stream casing is thinner than 2*this; lakes are wider
STREAM_REACH_PX = 6  # casing must touch the teal stream core within this distance
TILE = 256
LPA_STYLE = ("#000000", 1.92)

BACKGROUND, UNCOLOURED, HATCH, GLYPH = 0, 1, 2, 3  # special codes; zones start at 10
STREAM_BIT, HATCH_BIT = 32, 64  # flag bits added to class codes before polygonising


def hx(c):
    return None if c is None else "#" + "".join(f"{round(v * 255):02x}" for v in c[:3])


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(obj, path, **kw):
    with open(path, "w") as f:
        json.dump(obj, f, **kw)


def rgb_int(h):
    return int(h[1:], 16)


# ---------------------------------------------------------------- morphology (no scipy)
def dilate(m, r):
    m = m.copy()
    for _ in range(r):
        n = m.copy()
        n[1:] |= m[:-1]
        n[:-1] |= m[1:]
        n[:, 1:] |= m[:, :-1]
        n[:, :-1] |= m[:, 1:]
        m = n
    return m


def close(m, r):
    return ~dilate(~dilate(m, r), r)


def fill_from_neighbours(cls, unknown, not_source=None):
    """Assign unknown pixels the class of an adjacent known pixel, growing inwards.

    Pixels in `not_source` are never copied from (first pass); a second pass without
    that restriction fills any pocket that touches nothing else.
    """
    unknown = unknown.copy()
    blocked = not_source if not_source is not None else np.zeros_like(unknown)
    while unknown.any():
        before = unknown.sum()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_y = slice(max(-dy, 0), cls.shape[0] - max(dy, 0))
            dst_y = slice(max(dy, 0), cls.shape[0] - max(-dy, 0))
            src_x = slice(max(-dx, 0), cls.shape[1] - max(dx, 0))
            dst_x = slice(max(dx, 0), cls.shape[1] - max(-dx, 0))
            take = (
                unknown[dst_y, dst_x] & ~unknown[src_y, src_x] & ~blocked[src_y, src_x]
            )
            cls[dst_y, dst_x][take] = cls[src_y, src_x][take]
            unknown[dst_y, dst_x][take] = False
        if unknown.sum() == before:
            break
    if unknown.any() and not_source is not None:
        return fill_from_neighbours(cls, unknown)
    return cls


def erode(m, r):
    return ~dilate(~m, r)


def opening(m, r):
    return dilate(erode(m, r), r)


def zhang_suen(img):
    """Thin a binary image to a 1-pixel skeleton (Zhang-Suen)."""
    img = img.astype(np.uint8)
    while True:
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1)
            p2, p3, p4 = P[:-2, 1:-1], P[:-2, 2:], P[1:-1, 2:]
            p5, p6, p7 = P[2:, 2:], P[2:, 1:-1], P[2:, :-2]
            p8, p9 = P[1:-1, :-2], P[:-2, :-2]
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            B = sum(x.astype(np.uint8) for x in seq[:8])
            A = sum(
                ((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8)
            )
            if step == 0:
                c = ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                c = ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)
            m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & c
            if m.any():
                img[m] = 0
                changed = True
        if not changed:
            return img.astype(bool)


def skeleton_tiled(mask, tile=1024, margin=48):
    out = np.zeros_like(mask)
    H, W = mask.shape
    for ty in range(0, H, tile):
        for tx in range(0, W, tile):
            y0, x0 = max(ty - margin, 0), max(tx - margin, 0)
            y1, x1 = min(ty + tile + margin, H), min(tx + tile + margin, W)
            win = mask[y0:y1, x0:x1]
            if not win.any():
                continue
            sk = zhang_suen(win)
            h, w = min(tile, H - ty), min(tile, W - tx)
            out[ty : ty + h, tx : tx + w] = sk[
                ty - y0 : ty - y0 + h, tx - x0 : tx - x0 + w
            ]
    return out


def skeleton_lines(sk):
    """8-connected skeleton pixels -> merged LineStrings (pixel-centre coordinates)."""
    ys, xs = np.nonzero(sk)
    on = set(zip(ys.tolist(), xs.tolist()))
    segs = []
    for y, x in on:
        for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
            if (y + dy, x + dx) not in on:
                continue
            # diagonal only where no orthogonal step already joins the two pixels
            if dy and dx and ((y, x + dx) in on or (y + dy, x) in on):
                continue
            segs.append(((x + 0.5, y + 0.5), (x + dx + 0.5, y + dy + 0.5)))
    lines = shapely.linestrings(np.array(segs, dtype=float))
    return shapely.get_parts(shapely.line_merge(shapely.union_all(lines)))


# ---------------------------------------------------------------- inputs
def load_legend():
    rows = read_csv(LEGEND_CSV)
    zones, colour_code, info = {}, {}, {}
    for r in rows:
        h = r["colour_hex"]
        if not h.startswith("#") or r["role"] not in ("zone", "pattern", "overlay"):
            continue
        if r["role"] == "overlay" and h != "#38a800":
            continue
        if h == "#ffffff":
            continue
        if (
            h in colour_code
        ):  # shared swatch colour: first legend row wins (see legend notes)
            continue
        if h == "#38a800":
            colour_code[h] = HATCH
        elif h == "#55ff00":
            colour_code[h] = GLYPH
            zones.setdefault("Forest", 10 + len(zones))
            info[zones["Forest"]] = r
        else:
            code = zones.setdefault(r["zone_label_native"], 10 + len(zones))
            colour_code[h] = code
            info[code] = r
        if h == "#97dbf2":
            colour_code["#4065eb"] = colour_code[h]  # water outline
    white = next(r for r in rows if r["class_norm"] == "uncoloured")
    info[UNCOLOURED] = white
    return colour_code, zones, info


def load_mosaic(page, doc):
    strips = sorted(
        [x for x in page.get_image_info(xrefs=True) if x["width"] == STRIP_W],
        key=lambda x: x["bbox"][1],
    )
    x0, y0 = strips[0]["bbox"][0], strips[0]["bbox"][1]
    col_pt = (strips[0]["bbox"][2] - x0) / STRIP_W
    H = round((strips[-1]["bbox"][3] - y0) / ROW_PT)
    img = np.zeros((H, STRIP_W), np.uint32)
    for s in strips:
        pix = pymupdf.Pixmap(doc, s["xref"])
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        if s["transform"][3] < 0:  # strips are stored bottom-up (negative y scale)
            a = a[::-1]
        v = (
            (a[..., 0].astype(np.uint32) << 16)
            | (a[..., 1].astype(np.uint32) << 8)
            | a[..., 2]
        )
        r0 = round((s["bbox"][1] - y0) / ROW_PT)
        img[r0 : r0 + pix.height] = v[: H - r0]
    return img, (x0, y0, col_pt, ROW_PT)


def lpa_polygon_page(page):
    segs = []
    for x in page.get_drawings():
        if (hx(x.get("color")), round(x.get("width") or 0, 2)) != LPA_STYLE:
            continue
        for it in x["items"]:
            if it[0] == "l":
                segs.append(LineString([(it[1].x, it[1].y), (it[2].x, it[2].y)]))
            elif it[0] == "c":
                segs.append(LineString([(it[1].x, it[1].y), (it[4].x, it[4].y)]))
    return max(polygonize(unary_union(segs)), key=lambda g: g.area)


def render_mask(page, geom_page, grid, shape):
    x0, y0, cpt, rpt = grid
    d2 = pymupdf.open()
    pg = d2.new_page(width=page.rect.width, height=page.rect.height)
    sh = pg.new_shape()
    sh.draw_polyline([pymupdf.Point(*c) for c in geom_page.exterior.coords])
    sh.finish(color=(0, 0, 0), fill=(0, 0, 0))
    sh.commit()
    clip = pymupdf.Rect(x0, y0, x0 + shape[1] * cpt, y0 + shape[0] * rpt)
    pix = pg.get_pixmap(
        clip=clip,
        matrix=pymupdf.Matrix(1 / cpt, 1 / rpt),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    m = np.zeros(shape, bool)
    h, w = min(shape[0], a.shape[0]), min(shape[1], a.shape[1])
    m[:h, :w] = a[:h, :w] < 128
    return m


# ---------------------------------------------------------------- polygonise
def runs_to_rects(tile):
    """Pixel runs merged vertically -> (x0, y0, x1, y1, value) rectangles in tile pixels."""
    rects, open_ = [], {}
    h, w = tile.shape
    for y in range(h):
        row = tile[y]
        cut = np.flatnonzero(row[1:] != row[:-1]) + 1
        starts = np.concatenate([[0], cut])
        ends = np.concatenate([cut, [w]])
        cur = {}
        for a, b in zip(starts.tolist(), ends.tolist(), strict=True):
            v = int(row[a])
            if v == BACKGROUND:
                continue
            k = (a, b, v)
            cur[k] = open_.pop(k, y)
        for (a, b, v), ys in open_.items():
            rects.append((a, ys, b, y, v))
        open_ = cur
    for (a, b, v), ys in open_.items():
        rects.append((a, ys, b, h, v))
    return rects


def polygonise(cls):
    H, W = cls.shape
    pieces = {}
    for ty in range(0, H, TILE):
        for tx in range(0, W, TILE):
            t = cls[ty : ty + TILE, tx : tx + TILE]
            if not t.any():
                continue
            rects = runs_to_rects(t)
            if not rects:
                continue
            R = np.array(rects, dtype=np.float64)
            boxes = shapely.box(R[:, 0] + tx, R[:, 1] + ty, R[:, 2] + tx, R[:, 3] + ty)
            vals = R[:, 4].astype(int)
            for v in np.unique(vals):
                pieces.setdefault(int(v), []).append(
                    shapely.coverage_union_all(boxes[vals == v])
                )
    out = {}
    for v, geoms in pieces.items():
        out[v] = shapely.union_all(np.array(geoms, dtype=object), grid_size=1.0)
    return out


def geoparquet(path, table, geoms, crs):
    wkb = shapely.to_wkb(geoms)
    table = table.append_column("geometry", pa.array(wkb, pa.binary()))
    b = shapely.total_bounds(geoms)
    types = sorted({shapely.get_type_id(g) for g in geoms})
    names = {1: "LineString", 3: "Polygon", 6: "MultiPolygon"}
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": [names[t] for t in types],
                "crs": crs.to_json_dict(),
                "bbox": [float(v) for v in b],
            }
        },
    }
    table = table.replace_schema_metadata(
        {**(table.schema.metadata or {}), b"geo": json.dumps(geo).encode()}
    )
    pq.write_table(table, path, compression="zstd")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    args = ap.parse_args()
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")
    t0 = time.time()
    geo = read_json(os.path.join(args.data_root, "georef", f"{DOC_ID}.json"))
    A = np.array(geo["affine_page_to_32643"])
    plan = next(r for r in read_csv(PLANS_CSV) if r["plan_id"] == PLAN_ID)
    colour_code, zones, info = load_legend()
    doc = pymupdf.open(os.path.join(args.data_root, "raw", PLAN_ID, f"{DOC_ID}.pdf"))
    page = doc[0]

    img, grid = load_mosaic(page, doc)
    x0, y0, cpt, rpt = grid
    print(f"mosaic {img.shape}, {time.time() - t0:.0f}s")

    # 1. palette classification (+ nearest main colour for blends)
    main = {rgb_int(h): c for h, c in colour_code.items()}
    main[0xFFFFFF] = UNCOLOURED
    keys = np.array(sorted(main), dtype=np.uint32)
    codes = np.array([main[k] for k in keys], dtype=np.uint8)
    uniq, inv = np.unique(img, return_inverse=True)
    lut = np.zeros(len(uniq), np.uint8)
    krgb = np.stack([(keys >> 16) & 255, (keys >> 8) & 255, keys & 255], 1).astype(int)
    blend_px = 0
    counts = np.bincount(inv.ravel(), minlength=len(uniq))
    for i, u in enumerate(uniq.tolist()):
        j = np.searchsorted(keys, u)
        if j < len(keys) and keys[j] == u:
            lut[i] = codes[j]
        else:
            rgb = np.array([(u >> 16) & 255, (u >> 8) & 255, u & 255])
            lut[i] = codes[np.argmin(((krgb - rgb) ** 2).sum(1))]
            blend_px += int(counts[i])
    cls = lut[inv].reshape(img.shape)
    del img, inv
    print(
        f"classified: {len(uniq)} colours, {blend_px:,} blend px to nearest ({time.time() - t0:.0f}s)"
    )

    # 2. LPA mask
    lpa_page = lpa_polygon_page(page)
    inside = render_mask(page, lpa_page, grid, cls.shape)

    # 3. symbols that hide the zone underneath: NGT hatch, stream symbol, forest glyphs
    hatch = cls == HATCH
    ngt = close(hatch, HATCH_CLOSE_PX) & inside
    teal = cls == zones["Streams"]
    light = cls == zones["Water Bodies"]
    casing = light & ~opening(light, STREAM_CASING_PX) & dilate(teal, STREAM_REACH_PX)
    stream_sym = (teal | casing) & inside
    lakes = light & ~casing
    glyph = cls == GLYPH
    forest_area = dilate(close(glyph, FOREST_CLOSE_PX), FOREST_DILATE_PX) & inside
    skel = skeleton_tiled(stream_sym)
    print(
        f"stream symbol {stream_sym.sum():,} px, skeleton {skel.sum():,} px ({time.time() - t0:.0f}s)"
    )
    # zones under the symbols are land: fill from land pixels; lakes are never a source
    cls = fill_from_neighbours(cls, hatch | stream_sym | glyph, not_source=lakes)
    inf_hatch = (
        ngt & ~lakes & ~stream_sym
    )  # visible lakes inside the buffer are not hidden
    inf_stream = stream_sym.copy()
    print(f"symbols filled ({time.time() - t0:.0f}s)")

    # 5. clip to LPA; the two "inferred" flags go into the raster as bits so that one
    #    polygonise yields pieces already split by flag (no vector splitting)
    cls[~inside] = BACKGROUND
    px_area = abs(np.linalg.det(A[:2])) * cpt * rpt
    inside_px = int(inside.sum())
    assert int(cls.max()) < STREAM_BIT
    zone_px = (cls >= 10) | (cls == UNCOLOURED)
    code_r = cls.copy()
    code_r[inf_stream & zone_px] |= STREAM_BIT
    code_r[inf_hatch & zone_px] |= HATCH_BIT

    # 6. polygonise
    polys = polygonise(code_r)
    del code_r
    ngt_geom = polygonise(ngt.astype(np.uint8)).get(1)
    forest_geom = polygonise(forest_area.astype(np.uint8)).get(1)
    stream_lines = skeleton_lines(skel)
    print(f"polygonised ({time.time() - t0:.0f}s)")

    # pixel -> ground: pixel (c, r) -> page (x0 + c*cpt, y0 + r*rpt) -> affine
    M = np.array([[cpt, 0], [0, rpt]]) @ A[:2]
    off = np.array([x0, y0]) @ A[:2] + A[2]

    def to_ground(g):
        return shapely.transform(g, lambda xy: xy @ M + off)

    lpa_g = to_ground(
        shapely.transform(lpa_page, lambda xy: (xy - [x0, y0]) / [cpt, rpt])
    )
    ngt_g = to_ground(ngt_geom) if ngt_geom is not None else None
    ngt_g = shapely.make_valid(ngt_g).buffer(0) if ngt_g is not None else None

    forest_g = (
        shapely.make_valid(to_ground(forest_geom)).buffer(0)
        if forest_geom is not None
        else None
    )
    shapely.prepare(lpa_g)
    stream_g = [
        q
        for q in shapely.get_parts(shapely.intersection(to_ground(stream_lines), lpa_g))
        if q.geom_type == "LineString"
    ]

    status = {"status": plan["status"], "status_label": plan["status_label"]}
    qa_struct = {
        "doc_id": DOC_ID,
        "status": plan["status"],
        "extraction": "raster_palette",
        "georef_rmse_m": geo["georef_rmse_m"],
        "m_per_px": geo["m_per_px"],
        "georef_method": geo["georef_method"],
        "legend_check": "warn",
        "qa_failures": [],
        "sheet_scale": "1:57,340 (fitted; title block says 1:5,000)",
    }
    rows, geoms, sliver_n, sliver_area = [], [], 0, 0.0
    names = {v: k for k, v in zones.items()}
    names[UNCOLOURED] = info[UNCOLOURED]["zone_label_native"]
    for code_f, g in polys.items():
        code = code_f & (STREAM_BIT - 1)
        if code in (BACKGROUND, HATCH, GLYPH):
            continue
        flags = {
            f
            for f, bit in (("stream", STREAM_BIT), ("hatch", HATCH_BIT))
            if code_f & bit
        }
        parts = shapely.get_parts(g)
        thin = shapely.is_empty(
            shapely.buffer(parts, -SLIVER_PX / 2, join_style="mitre")
        )
        sliver_n += int(thin.sum())
        sliver_area += float(shapely.area(parts[thin]).sum()) * px_area
        for p in parts[~thin]:
            gp = to_ground(p)
            if not lpa_g.contains(gp):
                gp = shapely.intersection(gp, lpa_g)
            if gp.is_empty:
                continue
            for piece in (gp,):
                for q in shapely.get_parts(piece):
                    if q.geom_type != "Polygon" or q.area < px_area:
                        continue
                    r = info[code]
                    rows.append(
                        {
                            "plan_id": PLAN_ID,
                            "doc_id": DOC_ID,
                            "zone_label_native": names[code],
                            "zone_code_native": None,
                            "class_norm": r["class_norm"] or None,
                            **status,
                            "inferred_under_hatch": "hatch" in flags,
                            "inferred_under_stream": "stream" in flags,
                            "note": "; ".join(
                                n
                                for f, n in (
                                    ("stream", "zone inferred under stream symbol"),
                                    ("hatch", "zone inferred under hatch"),
                                )
                                if f in flags
                            )
                            or None,
                            "area_m2": float(q.area),
                        }
                    )
                    geoms.append(q)
    for i, r in enumerate(rows, 1):
        r["zone_uid"] = f"{PLAN_ID}-PLUCOMP-{i:06d}"
    print(
        f"{len(rows)} zone polygons; slivers dropped {sliver_n} ({sliver_area / 1e4:.1f} ha) ({time.time() - t0:.0f}s)"
    )

    # QA: areas
    by = {}
    for r in rows:
        k = r["class_norm"] or r["zone_label_native"]
        by[k] = by.get(k, 0.0) + r["area_m2"]
    ngt_ha = ngt_g.intersection(lpa_g).area / 1e4 if ngt_g is not None else 0.0
    under_ha = sum(r["area_m2"] for r in rows if r["inferred_under_hatch"]) / 1e4
    under_s_ha = sum(r["area_m2"] for r in rows if r["inferred_under_stream"]) / 1e4
    lpa_ha = lpa_g.area / 1e4
    qa = {
        "lpa_area_ha": lpa_ha,
        "inside_lpa_px": inside_px,
        "px_area_m2": px_area,
        "area_ha_by_class": {k: v / 1e4 for k, v in sorted(by.items())},
        "ngt_overlay_ha": ngt_ha,
        "ngt_overlay_excl_visible_water_ha": float(inf_hatch.sum()) * px_area / 1e4,
        "zone_area_inferred_under_stream_ha": under_s_ha,
        "stream_symbol_ha": float(inf_stream.sum()) * px_area / 1e4,
        "stream_centreline_km": sum(q.length for q in stream_g) / 1000,
        "forest_symbol_area_ha": forest_g.intersection(lpa_g).area / 1e4
        if forest_g is not None
        else 0.0,
        "forest_ground_colour": "#ffffff (white, 86% of ground pixels) -> zone 'uncoloured'",
        "zone_area_inferred_under_hatch_ha": under_ha,
        "uncoloured_share_of_lpa": by.get("uncoloured", 0.0) / 1e4 / lpa_ha,
        "slivers_dropped": sliver_n,
        "sliver_area_ha": sliver_area / 1e4,
        "blend_px": blend_px,
        "params": {
            "HATCH_CLOSE_PX": HATCH_CLOSE_PX,
            "FOREST_CLOSE_PX": FOREST_CLOSE_PX,
            "FOREST_DILATE_PX": FOREST_DILATE_PX,
            "SLIVER_PX": SLIVER_PX,
            "STREAM_CASING_PX": STREAM_CASING_PX,
            "STREAM_REACH_PX": STREAM_REACH_PX,
            "forest_method": (
                f"overlay: glyph pixels closed {FOREST_CLOSE_PX}px then dilated "
                f"{FOREST_DILATE_PX}px; zone = ground colour"
            ),
            "stream_method": (
                "overlay: teal core + thin light-blue casing, Zhang-Suen skeleton; "
                "zone under symbol filled from land"
            ),
        },
        "sheet_qa": qa_struct,
    }

    outdir = os.path.join(args.data_root, "planning", "zones")
    os.makedirs(outdir, exist_ok=True)
    crs = CRS.from_epsg(32643)
    cols = [
        "zone_uid",
        "plan_id",
        "doc_id",
        "zone_label_native",
        "zone_code_native",
        "class_norm",
        "status",
        "status_label",
        "inferred_under_hatch",
        "inferred_under_stream",
        "note",
        "area_m2",
    ]
    table = pa.table({c: [r[c] for r in rows] for c in cols})
    table = table.append_column("qa", pa.array([qa_struct] * len(rows)))
    geoparquet(
        os.path.join(outdir, f"{PLAN_ID}.parquet"),
        table,
        np.array(geoms, dtype=object),
        crs,
    )
    ov_rows, ov_geoms = [], []

    def add_overlays(geoms_, label, cls_norm, kind, method):
        for q in geoms_:
            ov_rows.append(
                {
                    "overlay_uid": f"{PLAN_ID}-{cls_norm.upper()}-{len(ov_rows) + 1:06d}",
                    "plan_id": PLAN_ID,
                    "doc_id": DOC_ID,
                    "overlay_label_native": label,
                    "class_norm": cls_norm,
                    "overlay_type": kind,
                    "status": plan["status"],
                    "status_label": plan["status_label"],
                    "method": method,
                    "size": q.area if kind == "area" else q.length,
                }
            )
            ov_geoms.append(q)

    def polys_in_lpa(g):
        if g is None:
            return []
        return [
            q
            for q in shapely.get_parts(g.intersection(lpa_g))
            if q.geom_type == "Polygon"
        ]

    add_overlays(
        polys_in_lpa(ngt_g),
        "NGT Buffer",
        "ngt_buffer",
        "area",
        f"hatch pixels closed {HATCH_CLOSE_PX}px",
    )
    add_overlays(
        polys_in_lpa(forest_g),
        "Forest",
        "forest_symbol_area",
        "area",
        qa["params"]["forest_method"],
    )
    add_overlays(
        stream_g, "Streams", "stream_centreline", "line", qa["params"]["stream_method"]
    )
    ot = pa.table({c: [r[c] for r in ov_rows] for c in ov_rows[0]})
    ot = ot.append_column("qa", pa.array([qa_struct] * len(ov_rows)))
    geoparquet(
        os.path.join(outdir, f"{PLAN_ID}_overlays.parquet"),
        ot,
        np.array(ov_geoms, dtype=object),
        crs,
    )
    np.save(os.path.join(outdir, f"{PLAN_ID}_classes.npy"), cls)
    write_json(
        {
            "codes": {str(k): v for k, v in names.items()},
            "grid": grid,
            "affine_page_to_32643": A.tolist(),
        },
        os.path.join(outdir, f"{PLAN_ID}_classes.json"),
    )
    write_json(qa, os.path.join(outdir, f"{PLAN_ID}_qa.json"), indent=1)
    print(f"wrote {outdir} ({time.time() - t0:.0f}s)")
    print(
        json.dumps(
            {
                k: qa[k]
                for k in (
                    "lpa_area_ha",
                    "area_ha_by_class",
                    "ngt_overlay_ha",
                    "zone_area_inferred_under_hatch_ha",
                    "zone_area_inferred_under_stream_ha",
                    "stream_symbol_ha",
                    "stream_centreline_km",
                    "forest_symbol_area_ha",
                    "uncoloured_share_of_lpa",
                    "slivers_dropped",
                    "sliver_area_ha",
                )
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
