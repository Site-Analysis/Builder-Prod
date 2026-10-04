#!/usr/bin/env python3
"""Step 1.8 acceptance pack: one PNG page per picked parcel, for the SME.

Usage:
    python acceptance_pack.py --data-root <dir> [--picks <picks.json>] [--out <dir>]
        [--cadastral-url http://localhost:8011] [--planning-url http://localhost:8012]

Needs both services running (DEV_BYPASS_AUTH=1 locally). Picks come from acceptance_pick.py.
Top: source map crop (PLUCOMP) | our zones + overlays + LPA line, parcel outline on both.
Bottom: /zones/at and /authority responses as plain text; raw JSON saved next to each PNG.
"""

import argparse
import csv
import json
import os
import textwrap
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pyarrow.parquet as pq
import pymupdf
import shapely
from pyproj import Transformer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ap = argparse.ArgumentParser()
ap.add_argument("--data-root", required=True)
ap.add_argument("--picks", default=None)
ap.add_argument("--out", default=None)
ap.add_argument("--cadastral-url", default="http://localhost:8011")
ap.add_argument("--planning-url", default="http://localhost:8012")
args = ap.parse_args()
D = args.data_root
OUT = args.out or os.path.join(D, "planning", "acceptance")
PICKS = args.picks or os.path.join(OUT, "picks.json")
CAD, PLAN = args.cadastral_url.rstrip("/"), args.planning_url.rstrip("/")
os.makedirs(OUT, exist_ok=True)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


g = read_json(f"{D}/georef/BDA-RMP2031-PLUCOMP.json")
A = np.array(g["affine_page_to_32643"])
Ai = np.linalg.inv(np.vstack([A.T, [0, 0, 1]]))
to_m = Transformer.from_crs(4326, 32643, always_xy=True)
col = {}
with open(f"{REPO}/infra/planning/legend_map.csv", encoding="utf-8") as fh:
    legend_rows = list(csv.DictReader(fh))
for r in legend_rows:
    if r["zone_label_native"] and r["colour_hex"].startswith("#"):
        col.setdefault(r["zone_label_native"], r["colour_hex"])
col["Not coloured on the plan"] = "#ffffff"


def load(name, cols):
    t = pq.read_table(f"{D}/planning/zones/{name}", columns=cols + ["geometry"])
    return {c: t.column(c).to_pylist() for c in cols}, shapely.from_wkb(
        t.column("geometry").to_numpy(zero_copy_only=False)
    )


Z, G = load("BDA-RMP2031.parquet", ["zone_label_native", "note"])
O, OG = load("BDA-RMP2031_overlays.parquet", ["class_norm"])
LPA = load("BDA-RMP2031_lpa.parquet", [])[1][0]
tree, otree = shapely.STRtree(G), shapely.STRtree(OG)
src = pymupdf.open(f"{D}/raw/BDA-RMP2031/BDA-RMP2031-PLUCOMP.pdf")
PARCEL = (1, 0, 1)
OV_STYLE = {
    "ngt_buffer": ((0, 0.65, 0.65), 0.08),
    "forest_symbol": ((0, 0.45, 0), 0.08),
    "stream_centreline": ((0, 0.25, 1), 0.12),
}
TITLES = {
    "deep": "Deep inside one zone",
    "straddle": "Straddles two zones",
    "stream_ngt": "On a stream / NGT buffer edge",
    "partial_village": "Village partly inside BDA LPA",
    "outside_bda": "Just outside the BDA LPA",
}


def rgbf(h):
    return tuple(int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))


def to_page(xy):
    xy = np.asarray(xy)[:, :2]
    return (np.column_stack([xy, np.ones(len(xy))]) @ Ai.T)[:, :2]


def draw(sh, coords, colour, width, fill=None, close=True, dashes=None):
    if len(coords) < 2:
        return
    sh.draw_polyline([pymupdf.Point(x, y) for x, y in to_page(coords)])
    sh.finish(color=colour, fill=fill, width=width, closePath=close, dashes=dashes)


def get(url):
    try:
        return json.load(urllib.request.urlopen(url, timeout=300))
    except urllib.error.HTTPError as e:
        return {"http_status": e.code, "body": e.read().decode()[:400]}


def fmt(o, ind=0, skip=("qa", "sheets_qa")):
    pad = "  " * ind
    lines = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in skip:
                lines.append(f"{pad}{k}: (sheet QA block omitted, see raw JSON)")
            elif isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines += fmt(v, ind + 1, skip)
            else:
                lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
    elif isinstance(o, list):
        for v in o:
            sub = fmt(v, ind + 1, skip)
            lines.append(pad + "- " + sub[0].strip()) if sub else None
            lines += sub[1:]
    else:
        lines.append(pad + json.dumps(o, ensure_ascii=False))
    return lines


picks = read_json(PICKS)
for name, p in picks.items():
    q = {k: p[k] for k in ("dist", "taluk", "hobli", "vlg")}
    fc = get(f"{CAD}/data?" + urllib.parse.urlencode({**q, "survey": p["survey"]}))
    parts = [
        shapely.make_valid(shapely.from_geojson(json.dumps(f["geometry"])))
        for f in fc["features"]
        if str(f["properties"].get("survey_no")) == p["survey"]
    ]
    parcel = shapely.union_all(
        [x for pt in parts for x in shapely.get_parts(pt) if x.geom_type == "Polygon"]
    )
    parcel = shapely.transform(
        parcel, lambda xy: np.column_stack(to_m.transform(xy[:, 0], xy[:, 1]))
    )
    za = get(f"{PLAN}/zones/at?" + urllib.parse.urlencode({**q, "survey": p["survey"]}))
    au = get(f"{PLAN}/authority?" + urllib.parse.urlencode(q))
    with open(f"{OUT}/{name}.json", "w") as fh:
        json.dump({"zones_at": za, "authority": au}, fh, indent=1)

    # crop around the parcel: at least ~500 m across, bigger parcels get more
    c = to_page([parcel.centroid.coords[0]])[0]
    bb = to_page(np.array(parcel.envelope.exterior.coords))
    half = max(13.0, 0.9 * float(np.abs(bb - c).max()))
    clip = pymupdf.Rect(c[0] - half, c[1] - half, c[0] + half, c[1] + half)
    corners = np.array([[clip.x0, clip.y0, 1], [clip.x1, clip.y1, 1]]) @ A
    box = shapely.box(
        corners[:, 0].min(),
        corners[:, 1].min(),
        corners[:, 0].max(),
        corners[:, 1].max(),
    )
    span_m = corners[:, 0].max() - corners[:, 0].min()

    tmp = pymupdf.open()
    w, h = src[0].rect.width, src[0].rect.height
    tmp.new_page(width=w, height=h).show_pdf_page(pymupdf.Rect(0, 0, w, h), src, 0)
    tmp.new_page(width=w, height=h)
    left, right = tmp[0], tmp[1]
    sh = right.new_shape()
    labels = set()
    for i in tree.query(box):
        labels.add(Z["zone_label_native"][i])
        for poly in shapely.get_parts(G[i]):
            draw(
                sh,
                poly.exterior.coords,
                (0.35, 0.35, 0.35),
                0.03,
                rgbf(col.get(Z["zone_label_native"][i], "#ff00ff")),
            )
            if Z["note"][i]:
                draw(
                    sh,
                    poly.exterior.coords,
                    (0.2, 0.2, 0.2),
                    0.06,
                    dashes="[0.3 0.3] 0",
                )
    kinds = set()
    for i in otree.query(box):
        k = O["class_norm"][i]
        kinds.add(k)
        colour, wd = OV_STYLE.get(k, ((0.5, 0.5, 0.5), 0.08))
        for part in shapely.get_parts(OG[i]):
            if part.geom_type == "LineString":
                draw(sh, part.coords, colour, wd, close=False)
            else:
                draw(sh, part.exterior.coords, colour, wd)
    for part in shapely.get_parts(shapely.intersection(LPA.boundary, box.buffer(50))):
        if part.geom_type == "LineString":
            draw(sh, part.coords, (0, 0, 0), 0.15, close=False, dashes="[0.6 0.3] 0")
    sh.commit()
    for page in (left, right):
        s2 = page.new_shape()
        for part in shapely.get_parts(parcel):
            draw(s2, part.exterior.coords, PARCEL, 0.25)
        s2.commit()
    a = left.get_pixmap(clip=clip, dpi=int(72 * 540 / (2 * half)))
    b = right.get_pixmap(clip=clip, dpi=int(72 * 540 / (2 * half)))

    # the page
    W, H = 1200, 1700
    doc = pymupdf.open()
    pg = doc.new_page(width=W, height=H)
    title = f"{TITLES.get(name, name)}: {p['village']} survey {p['survey']}"
    pg.insert_text((30, 40), title, fontsize=18, fontname="hebo")
    pg.insert_text(
        (30, 62),
        f"dist {p['dist']} / taluk {p['taluk']} / hobli {p['hobli']} / vlg {p['vlg']}   "
        "BDA RMP 2031 is a DRAFT plan, never approved.",
        fontsize=10.5,
        fontname="helv",
    )
    y0 = 80
    pg.insert_image(pymupdf.Rect(30, y0, 30 + 560, y0 + 560), pixmap=a)
    pg.insert_image(pymupdf.Rect(610, y0, 610 + 560, y0 + 560), pixmap=b)
    pg.draw_rect(pymupdf.Rect(30, y0, 590, y0 + 560), color=(0, 0, 0), width=0.5)
    pg.draw_rect(pymupdf.Rect(610, y0, 1170, y0 + 560), color=(0, 0, 0), width=0.5)
    pg.insert_text(
        (30, y0 + 575),
        f"Source: BDA-RMP2031-PLUCOMP (Proposed Land Use composite), crop ~{span_m:.0f} m across",
        fontsize=9,
        fontname="helv",
    )
    pg.insert_text(
        (610, y0 + 575),
        "Ours: extracted zones; dashed black = LPA boundary; grey dashed outline = inferred zone",
        fontsize=9,
        fontname="helv",
    )
    # legend
    ly = y0 + 595
    pg.draw_line((30, ly - 3), (60, ly - 3), color=PARCEL, width=2.5)
    pg.insert_text(
        (66, ly), "parcel outline (both panels)", fontsize=9, fontname="helv"
    )
    lx = 250
    for k in sorted(kinds):
        colour, _ = OV_STYLE.get(k, ((0.5, 0.5, 0.5), 0))
        pg.draw_line((lx, ly - 3), (lx + 25, ly - 3), color=colour, width=2)
        pg.insert_text((lx + 30, ly), k.replace("_", " "), fontsize=9, fontname="helv")
        lx += 150
    ly += 16
    lx = 30
    for lab in sorted(labels):
        pg.draw_rect(
            pymupdf.Rect(lx, ly - 9, lx + 14, ly + 1),
            color=(0.3, 0.3, 0.3),
            fill=rgbf(col.get(lab, "#ff00ff")),
            width=0.4,
        )
        pg.insert_text((lx + 18, ly), lab, fontsize=8.5, fontname="helv")
        lx += 20 + pymupdf.get_text_length(lab, "helv", 8.5) + 14
        if lx > 1050:
            lx, ly = 30, ly + 14
    ty = ly + 22
    wrap = lambda ls: [
        s
        for ln in ls
        for s in (textwrap.wrap(ln, 92, subsequent_indent="    ") or [""])
    ]
    za_txt = [
        "GET /zones/at?" + urllib.parse.urlencode({**q, "survey": p["survey"]}),
        "",
    ] + fmt(za)
    au_txt = ["GET /authority?" + urllib.parse.urlencode(q), ""] + fmt(au)
    left_lines, right_lines = wrap(za_txt), wrap(au_txt)
    fs = 7.6
    room = int((H - ty - 20) / (fs * 1.18))
    if len(left_lines) > room:
        left_lines = left_lines[: room - 1] + [
            f"... ({len(left_lines) - room + 1} more lines in {name}.json)"
        ]
    pg.insert_textbox(
        pymupdf.Rect(30, ty, 640, H - 10),
        "\n".join(left_lines),
        fontsize=fs,
        fontname="cour",
    )
    pg.insert_textbox(
        pymupdf.Rect(660, ty, 1170, H - 10),
        "\n".join(right_lines),
        fontsize=fs,
        fontname="cour",
    )
    pg.get_pixmap(dpi=110).save(f"{OUT}/{name}.png")
    print(
        name,
        "saved",
        len(fmt(za)),
        "zones_at lines",
        len(fmt(au)),
        "authority lines",
        f"crop {span_m:.0f} m",
    )
