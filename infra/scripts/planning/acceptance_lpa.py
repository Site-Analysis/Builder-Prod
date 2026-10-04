#!/usr/bin/env python3
"""Acceptance pages for the LPA 2031 plans (Hoskote, Anekal): 5 parcels per plan, one PNG
each, source sheet crop next to our zones, plus the live /zones/at and /authority answers.

Usage:
    python acceptance_lpa.py --data-root <dir> --cadastral-dir <dir> [--plans BMRDA-HSK-MP2031 ...]
        [--planning-url http://localhost:8012]

Needs the planning service running with the plans' flags on (cadastral is not needed:
parcels are read from the cadastral parquets with the (Northing, Easting) swap).
Cases (seeded, first parcel that qualifies):
    deep            one zone >= 99.5 % of the parcel, no other zone within 60 m
    straddle        two zones (not "Not coloured"), each >= 30 %
    water           a water body zone covers 10-90 % of the parcel
    partial_village village only partly in the LPA; parcel inside, 30-300 m from the edge
    outside         parcel 20-150 m outside the LPA
Left: the source sheet around the parcel (Hoskote: the sheet's own UTM grid fit;
Anekal: primeocr's affine), parcel in magenta. Right: our zones, same window. Bottom: the
service's answers; the raw JSON is saved next to each PNG.
"""

import argparse
import json
import os
import random
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pyarrow.parquet as pq
import pymupdf
import shapely

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CASES = ["deep", "straddle", "water", "partial_village", "outside"]
TITLES = {
    "deep": "Deep inside one zone",
    "straddle": "Across two zones",
    "water": "On a water body edge",
    "partial_village": "Village partly inside the LPA",
    "outside": "Just outside the LPA",
}
CLASS_COLOUR = {
    "residential": "#fff34d",
    "commercial": "#2f7fe0",
    "industrial": "#a020c0",
    "public_semi_public": "#f03020",
    "open_space": "#7acb4a",
    "public_utility": "#ffa500",
    "transport": "#a0a0a0",
    "unclassified": "#e1e1e1",
    "agriculture": "#d4fcc0",
    "water": "#98dcf0",
    "forest": "#3e8e2e",
    "hillock": "#959899",
    "uncoloured": "#ffffff",
}
WIN_M = 260.0
SURVEY_RE = r"^\d+(/[*\d]+)*$"


def rgbf(h):
    return tuple(int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"http_status": e.code, "body": e.read().decode(errors="replace")[:400]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def fmt(o, ind=0, skip=("qa", "sheets_qa", "doc_ids", "sources_checked", "zone_uids")):
    pad = "  " * ind
    lines = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in skip:
                continue
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines += fmt(v, ind + 1, skip)
            else:
                lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
    elif isinstance(o, list):
        for v in o:
            sub = fmt(v, ind + 1, skip)
            if sub:
                lines.append(pad + "- " + sub[0].strip())
                lines += sub[1:]
    else:
        lines.append(pad + json.dumps(o, ensure_ascii=False))
    return lines


def parcels_of(cad_dir, key):
    p = os.path.join(
        cad_dir,
        f"dist_{key[0]}",
        f"taluk_{key[1]}",
        f"hobli_{key[2]}",
        f"vlg_{key[3]}.parquet",
    )
    if not os.path.exists(p) or "geometry" not in pq.read_schema(p).names:
        return []
    t = pq.read_table(p, columns=["survey_no", "geometry"])
    sv = [str(s) if s else "" for s in t.column("survey_no").to_pylist()]
    g = shapely.make_valid(
        shapely.transform(
            shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False)),
            lambda xy: xy[:, ::-1],
        )
    )
    cnt = {}
    for s in sv:
        cnt[s] = cnt.get(s, 0) + 1

    def largest(q):
        parts = [x for x in shapely.get_parts(q) if x.geom_type == "Polygon"]
        return max(parts, key=lambda x: x.area) if parts else None

    out = []
    for s, q in zip(sv, g, strict=True):
        if not s or cnt[s] != 1 or not re.match(SURVEY_RE, s):
            continue
        q = largest(q)
        if q is not None and q.area > 200:
            out.append((s, q))
    return out


class Plan:
    def __init__(self, data_root, plan_id):
        z = os.path.join(data_root, "planning", "zones")
        t = pq.read_table(
            os.path.join(z, f"{plan_id}.parquet"),
            columns=["class_norm", "zone_label_native", "sheet", "geometry"],
        )
        self.cls = np.array(t.column("class_norm").to_pylist(), dtype=object)
        self.label = t.column("zone_label_native").to_pylist()
        self.sheet = t.column("sheet").to_pylist()
        self.g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
        self.tree = shapely.STRtree(self.g)
        lp = pq.read_table(
            os.path.join(z, f"{plan_id}_lpa.parquet"), columns=["geometry"]
        )
        self.lpa = shapely.union_all(
            shapely.from_wkb(lp.column("geometry").to_numpy(zero_copy_only=False))
        )
        shapely.prepare(self.lpa)

    def shares(self, parcel):
        idx = self.tree.query(parcel, predicate="intersects")
        out = {}
        for i in idx:
            a = shapely.intersection(self.g[i], parcel).area
            if a > 0:
                out[self.cls[i]] = out.get(self.cls[i], 0.0) + a
        return {k: 100 * v / parcel.area for k, v in out.items()}, idx


def classify(plan, parcel, village_partial):
    inside = plan.lpa.contains(parcel)
    d_edge = parcel.distance(plan.lpa.boundary)
    if not plan.lpa.intersects(parcel):
        d = parcel.distance(plan.lpa)
        return "outside" if 20 <= d <= 150 else None
    if not inside:
        return None
    sh, _ = plan.shares(parcel)
    zones = {k: v for k, v in sh.items() if k not in ("uncoloured", "road_space")}
    if village_partial and 30 <= d_edge <= 300:
        return "partial_village"
    if 10 <= sh.get("water", 0) <= 90:
        return "water"
    big = [k for k, v in zones.items() if v >= 30]
    if len(big) >= 2:
        return "straddle"
    if (
        len(sh) == 1
        and next(iter(sh.values())) >= 99.5
        and next(iter(sh)) not in ("uncoloured",)
    ):
        near = plan.tree.query(parcel.buffer(60), predicate="intersects")
        if len({plan.cls[i] for i in near}) == 1:
            return "deep"
    return None


def pick(plan, plan_id, villages, cad_dir, seed=2031):
    rnd = random.Random(seed)
    order = list(villages.items())
    rnd.shuffle(order)
    found = {}
    for key, partial in order:
        ps = parcels_of(cad_dir, key)
        rnd.shuffle(ps)
        for s, q in ps[:60]:
            c = classify(plan, q, partial)
            if c and c not in found:
                found[c] = {
                    "dist": key[0],
                    "taluk": key[1],
                    "hobli": key[2],
                    "vlg": key[3],
                    "survey": s,
                    "case": c,
                }
        if len(found) == len(CASES):
            break
    return found


def source_crop(data_root, plan_id, sheet_name, win):
    """(png bytes, inverse transform ground->crop px) for the sheet around win."""
    if plan_id == "BMRDA-HSK-MP2031":
        from extract_hoskote import grid_fit

        n = int(re.search(r"Map No\. (\d+)", sheet_name or "").group(1))
        m = read_json(
            os.path.join(
                data_root, "planning", "zones", "hsk_sheets", f"map_{n:02d}.json"
            )
        )
        doc = pymupdf.open(os.path.join(data_root, "raw", plan_id, f"{plan_id}-MP.pdf"))
        page = doc[m["page"] - 1]
        fit = grid_fit(page)
        aE, bE = fit["E"][:2]
        aN, bN = fit["N"][:2]

        def to_page(xy):
            return np.column_stack([(xy[:, 0] - bE) / aE, (xy[:, 1] - bN) / aN])

    else:
        key = re.search(r"Map No\. (\d+)", sheet_name or "")
        sheets = os.path.join(data_root, "planning", "zones", "ank_sheets")
        meta = next(
            (
                read_json(os.path.join(sheets, f))
                for f in os.listdir(sheets)
                if f.startswith("map_p")
                and f.endswith(".json")
                and key
                and f"Map No. {key.group(1)} "
                in read_json(os.path.join(sheets, f))["name"]
            ),
            None,
        )
        doc = pymupdf.open(os.path.join(data_root, "raw", plan_id, f"{plan_id}-MP.pdf"))
        page = doc[int(meta["key"][1:]) - 1]
        A = np.array(meta["A"])
        M = np.array([[A[0], A[1], A[4]], [A[2], A[3], A[5]], [0, 0, 1]])
        Mi = np.linalg.inv(M)
        zoom = 110 / 72  # primeocr's pixels are the page rendered at 110 ppi

        def to_page(xy):
            px = (np.column_stack([xy, np.ones(len(xy))]) @ Mi.T)[:, :2]
            return px / zoom

    x0, y0, x1, y1 = win
    corners = to_page(np.array([[x0, y0], [x1, y1]]))
    r = pymupdf.Rect(
        min(corners[:, 0]), min(corners[:, 1]), max(corners[:, 0]), max(corners[:, 1])
    )
    pix = page.get_pixmap(clip=r, dpi=200)
    return pix.tobytes("png"), r, to_page


def render(out_png, title, plan_id, parcel, plan, data_root, za, au):
    W, H = 1400, 1500
    doc = pymupdf.open()
    pg = doc.new_page(width=W, height=H)
    pg.insert_text((30, 40), f"{plan_id}: {title}", fontsize=18)
    c = parcel.centroid
    win = (c.x - WIN_M, c.y - WIN_M, c.x + WIN_M, c.y + WIN_M)
    panel = 640
    L = pymupdf.Rect(30, 70, 30 + panel, 70 + panel)
    R = pymupdf.Rect(60 + panel, 70, 60 + 2 * panel, 70 + panel)
    # left: source sheet
    sheet_name = None
    _sh, idx = plan.shares(parcel)
    if len(idx):
        sheet_name = max(
            (plan.sheet[i] for i in idx if plan.sheet[i]),
            key=lambda s: s or "",
            default=None,
        )
    if sheet_name:
        png, r, to_page = source_crop(data_root, plan_id, sheet_name, win)
        pg.insert_image(L, stream=png)
        pts = to_page(np.asarray(parcel.exterior.coords))
        sx, sy = L.width / r.width, L.height / r.height
        sh = pg.new_shape()
        sh.draw_polyline(
            [
                pymupdf.Point(L.x0 + (x - r.x0) * sx, L.y0 + (y - r.y0) * sy)
                for x, y in pts
            ]
        )
        sh.finish(color=(1, 0, 1), width=2, closePath=True)
        sh.commit()
        pg.insert_text((L.x0, L.y1 + 16), f"Source: {sheet_name}", fontsize=10)

    # right: our zones
    def to_r(xy):
        return [
            pymupdf.Point(
                R.x0 + (x - win[0]) / (2 * WIN_M) * panel,
                R.y0 + (win[3] - y) / (2 * WIN_M) * panel,
            )
            for x, y in xy
        ]

    box = shapely.box(*win)
    sh = pg.new_shape()
    for i in plan.tree.query(box, predicate="intersects"):
        g = shapely.intersection(plan.g[i], box)
        for poly in shapely.get_parts(g):
            if poly.geom_type != "Polygon" or len(poly.exterior.coords) < 3:
                continue
            sh.draw_polyline(to_r(poly.exterior.coords))
            sh.finish(
                color=(0.5, 0.5, 0.5),
                fill=rgbf(CLASS_COLOUR.get(plan.cls[i], "#999999")),
                width=0.3,
                closePath=True,
            )
    lb = shapely.intersection(plan.lpa.boundary, box)
    for ln in shapely.get_parts(lb):
        if ln.geom_type == "LineString" and len(ln.coords) >= 2:
            sh.draw_polyline(to_r(ln.coords))
            sh.finish(color=(0.66, 0, 0), width=1.5, dashes="[6 3] 0", closePath=False)
    sh.draw_polyline(to_r(parcel.exterior.coords))
    sh.finish(color=(1, 0, 1), width=2, closePath=True)
    sh.commit()
    pg.insert_text(
        (R.x0, R.y1 + 16),
        "Our zones (class colours), LPA boundary dashed red",
        fontsize=10,
    )
    # bottom: answers
    lines = (
        ["/zones/at:"]
        + fmt({k: za.get(k) for k in ("zones", "trace_hits", "note")})
        + ["", "/authority:"]
        + fmt(
            {
                k: au.get(k)
                for k in (
                    "authority",
                    "coverage",
                    "plan_coverage",
                    "authorities",
                    "note",
                )
            }
        )
    )
    # insert_textbox draws nothing when the text overflows: keep it to what fits
    text = "\n".join(
        textwrap.shorten(x, 160) if len(x) > 160 else x for x in lines[:78]
    )
    pg.insert_textbox(
        pymupdf.Rect(30, 70 + panel + 40, W - 30, H - 20),
        text,
        fontsize=7,
        fontname="cour",
    )
    pg.get_pixmap(dpi=110).save(out_png)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    ap.add_argument(
        "--plans", nargs="+", default=["BMRDA-HSK-MP2031", "BMRDA-ANK-MP2031"]
    )
    ap.add_argument("--planning-url", default="http://localhost:8012")
    args = ap.parse_args()
    import csv

    with open(
        os.path.join(REPO, "infra", "planning", "authority_villages.csv"),
        encoding="utf-8",
    ) as f:
        auth = list(csv.DictReader(f))
    for plan_id in args.plans:
        if not os.path.exists(
            os.path.join(args.data_root, "planning", "zones", f"{plan_id}.parquet")
        ):
            print(f"{plan_id}: not loaded, skipped")
            continue
        plan = Plan(args.data_root, plan_id)
        authority = {"BMRDA-HSK-MP2031": "BMRDA-HSK", "BMRDA-ANK-MP2031": "BMRDA-ANK"}[
            plan_id
        ]
        villages = {}
        for r in auth:
            ents = (
                json.loads(r["authorities_json"]) if r.get("authorities_json") else []
            )
            for e in ents:
                if e["authority"] == authority:
                    villages[(r["dist"], r["taluk"], r["hobli"], r["vlg"])] = (
                        e["coverage"] == "partial"
                    )
        out = os.path.join(args.data_root, "planning", "acceptance", plan_id)
        os.makedirs(out, exist_ok=True)
        picks = pick(plan, plan_id, villages, args.cadastral_dir)
        with open(os.path.join(out, "picks.json"), "w") as f:
            json.dump(picks, f, indent=1)
        for case in CASES:
            p = picks.get(case)
            if not p:
                print(f"  {plan_id} {case}: no parcel found")
                continue
            q = {k: p[k] for k in ("dist", "taluk", "hobli", "vlg")}
            za = get(
                f"{args.planning_url}/zones/at?"
                + urllib.parse.urlencode({**q, "survey": p["survey"]})
            )
            au = get(f"{args.planning_url}/authority?" + urllib.parse.urlencode(q))
            with open(os.path.join(out, f"{case}.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {"pick": p, "zones_at": za, "authority": au},
                    f,
                    indent=1,
                    ensure_ascii=False,
                )
            parcel = next(
                g
                for s, g in parcels_of(args.cadastral_dir, tuple(q.values()))
                if s == p["survey"]
            )
            render(
                os.path.join(out, f"{case}.png"),
                TITLES[case],
                plan_id,
                parcel,
                plan,
                args.data_root,
                za,
                au,
            )
            print(
                f"  {plan_id} {case}: {p['dist']}/{p['taluk']}/{p['hobli']}/{p['vlg']} survey {p['survey']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
