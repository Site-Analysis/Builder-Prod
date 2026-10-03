#!/usr/bin/env python3
"""Pre-drawn 2031 zone layers (round of 3 Oct 2026, open-decisions #65): one raster PMTiles file
per plan, uploaded once to Supabase Storage, so the map's plan switch shows the zones at once
(no sheet download, no extraction, no planning-service memory).

    python build_tiles.py --plans BDA-RMP2031,BMRDA-HSK-MP2031,BMRDA-ANK-MP2031 [--zmax 15]

Zones come from the running planning service (/zones, simplify 8, 0.05 deg boxes), which
extracts them from the indexed sheets as usual; they are drawn in the web map's colours (BDA:
its legend; LPA plans: one colour per class; unconfirmed sheets with a dashed outline) into
256 px PNG tiles, zoom 10-15 (the map over-zooms 15 to 16+). Files are written to
%TEMP%\\qnit_planning\\tiles\\, uploaded to the public bucket `planning-tiles` (one object per
plan and zoom range, each under 45 MB, plus manifest.json) and deleted. The service-role key is
read from apps/web/.env.local and never printed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import shapely
from PIL import Image, ImageDraw
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from pyproj import Transformer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itertools

import qnit_fetch as qf

BUCKET = "planning-tiles"
MAX_OBJECT = 45 * 1024 * 1024  # Supabase free plan: 50 MB per object
SS = 2  # supersampling
TO_3857 = Transformer.from_crs(4326, 3857, always_xy=True)
R = 6378137.0
WORLD = 2 * math.pi * R
CLASS_COLOUR = {  # apps/web/components/map/PlanningLayers.tsx CLASS_COLOUR
    "residential": "#FFF34D",
    "commercial": "#2F7FE0",
    "industrial": "#A020C0",
    "public_semi_public": "#F03020",
    "open_space": "#7ACB4A",
    "public_utility": "#FFA500",
    "transport": "#A0A0A0",
    "unclassified": "#E1E1E1",
    "agriculture": "#D4FCC0",
    "water": "#98DCF0",
    "forest": "#3E8E2E",
    "hillock": "#959899",
    "uncoloured": "#FFFFFF",
}


def get_json(url: str, params: dict | None = None, timeout: float = 1800) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def env_local() -> dict:
    out = {}
    with open(os.path.join(REPO, "apps", "web", ".env.local"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def bda_colours() -> dict:
    with open(
        os.path.join(REPO, "infra", "planning", "legend_map.csv"), encoding="utf-8"
    ) as f:
        return {
            r["zone_label_native"]: r["colour_hex"]
            for r in csv.DictReader(f)
            if r["plan_id"] == "BDA-RMP2031"
        }


def style(plan_id: str, p: dict, bda: dict) -> tuple:
    """(fill rgb, fill alpha 0-1, outline rgb or None, dashed) as the web map draws a zone."""
    cn = p.get("class_norm") or ""
    if cn == "road_space":
        return (rgb("#E0E0E0"), 0.35, None, False)
    col = (
        bda.get(p["zone_label_native"], "#999999")
        if plan_id == "BDA-RMP2031"
        else CLASS_COLOUR.get(cn, "#999999")
    )
    # no per-zone outlines (a dashed line round every zone of an unconfirmed sheet buried the
    # map, 3 Oct): the web draws one dashed rectangle per unconfirmed sheet and the legend
    # card explains it
    if cn == "uncoloured":
        return (rgb("#FFFFFF"), 0.2, None, False)
    return (rgb(col), 0.45, None, False)


def fetch_plan(
    base: str, plan_id: str, ext: list[float], simplify: int = 8
) -> list[tuple]:
    """Every zone of the plan (deduplicated), as (geometry in EPSG:3857, properties)."""
    feats: dict[str, tuple] = {}
    step = 0.05
    xs = np.arange(math.floor(ext[0] / step) * step, ext[2], step)
    ys = np.arange(math.floor(ext[1] / step) * step, ext[3], step)
    if True:
        for n, x in enumerate(xs):
            for y in ys:
                bb = f"{x:.6f},{y:.6f},{x + step:.6f},{y + step:.6f}"
                while True:
                    j = get_json(
                        f"{base}/zones",
                        {"plan_id": plan_id, "bbox": bb, "simplify_m": simplify},
                    )
                    if not j.get("pending_sheets"):
                        break
                    time.sleep(15)
                for f in j["features"]:
                    uid = f["properties"]["zone_uid"]
                    if uid in feats:
                        continue
                    g = shapely.geometry.shape(f["geometry"])
                    g = shapely.transform(
                        g,
                        lambda xy: np.column_stack(
                            TO_3857.transform(xy[:, 0], xy[:, 1])
                        ),
                    )
                    feats[uid] = (g, f["properties"])
            log(f"  {plan_id}: column {n + 1}/{len(xs)}, {len(feats)} zones")
    return list(feats.values())


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = WORLD / 2**z
    x0 = -WORLD / 2 + x * size
    y1 = WORLD / 2 - y * size
    return (x0, y1 - size, x0 + size, y1)


def tiles_for(ext3857, z: int):
    size = WORLD / 2**z
    x0 = int((ext3857[0] + WORLD / 2) // size)
    x1 = int((ext3857[2] + WORLD / 2) // size)
    y0 = int((WORLD / 2 - ext3857[3]) // size)
    y1 = int((WORLD / 2 - ext3857[1]) // size)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def dashed(draw, pts, fill, width, on=8, off=6):
    for (ax, ay), (bx, by) in itertools.pairwise(pts):
        L = math.hypot(bx - ax, by - ay)
        if L == 0:
            continue
        t = 0.0
        while t < L:
            t2 = min(L, t + on)
            draw.line(
                [
                    (ax + (bx - ax) * t / L, ay + (by - ay) * t / L),
                    (ax + (bx - ax) * t2 / L, ay + (by - ay) * t2 / L),
                ],
                fill=fill,
                width=width,
            )
            t = t2 + off


def render(z, x, y, geoms, props, styles, tree) -> bytes | None:
    b = tile_bounds(z, x, y)
    hit = tree.query(shapely.box(*b))
    if not len(hit):
        return None
    px = 256 * SS
    sc = px / (b[2] - b[0])

    def to_px(cs):
        return [((cx - b[0]) * sc, (b[3] - cy) * sc) for cx, cy in cs]

    # one alpha mask per style (zones of a plan do not overlap)
    groups: dict[tuple, list[int]] = {}
    for i in hit.tolist():
        groups.setdefault(styles[i], []).append(i)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    lines = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    min_px = 0.3 if z >= 14 else 0.6
    for (fill, alpha, outline, dash), idx in groups.items():
        mask = Image.new("L", (px, px), 0)
        md = ImageDraw.Draw(mask)
        for i in idx:
            g = geoms[i]
            for poly in shapely.get_parts(g):
                if poly.geom_type != "Polygon" or poly.is_empty:
                    continue
                pb = poly.bounds
                if (pb[2] - pb[0]) * sc < min_px and (pb[3] - pb[1]) * sc < min_px:
                    continue
                ext = to_px(poly.exterior.coords)
                if len(ext) >= 3:
                    md.polygon(ext, fill=255)
                for h in poly.interiors:
                    hp = to_px(h.coords)
                    if len(hp) >= 3:
                        md.polygon(hp, fill=0)
                if outline is not None and z >= 13:
                    w = 2 * SS if dash else SS
                    if dash:
                        dashed(ld, ext, (*outline, 230), w, on=7 * SS, off=5 * SS)
                    else:
                        ld.line(ext, fill=(*outline, 200), width=w)
        layer = Image.new("RGBA", (px, px), (*fill, 0))
        layer.putalpha(mask.point(lambda v, a=alpha: int(v * a)))
        img = Image.alpha_composite(img, layer)
    img = Image.alpha_composite(img, lines)
    if img.getbbox() is None:
        return None
    img = img.resize((256, 256), Image.LANCZOS)
    q = img.quantize(colors=96, method=Image.Quantize.FASTOCTREE)
    buf = io.BytesIO()
    q.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_pmtiles(
    path, tiles: dict[int, bytes], zmin, zmax, ext_wgs, plan_id, meta
) -> int:
    with open(path, "wb") as f:
        w = Writer(f)
        for tid in sorted(tiles):
            w.write_tile(tid, tiles[tid])
        w.finalize(
            {
                "tile_type": TileType.PNG,
                "tile_compression": Compression.NONE,
                "min_zoom": zmin,
                "max_zoom": zmax,
                "min_lon_e7": int(ext_wgs[0] * 1e7),
                "min_lat_e7": int(ext_wgs[1] * 1e7),
                "max_lon_e7": int(ext_wgs[2] * 1e7),
                "max_lat_e7": int(ext_wgs[3] * 1e7),
                "center_zoom": zmin,
                "center_lon_e7": int((ext_wgs[0] + ext_wgs[2]) / 2 * 1e7),
                "center_lat_e7": int((ext_wgs[1] + ext_wgs[3]) / 2 * 1e7),
            },
            {"name": plan_id, **meta},
        )
    return os.path.getsize(path)


class Storage:
    def __init__(self):
        e = env_local()
        self.url = e["NEXT_PUBLIC_SUPABASE_URL"].rstrip("/")
        self._key = e["SUPABASE_SERVICE_ROLE_KEY"]
        self.sent = 0

    def _req(self, method, path, data=None, headers=None):
        h = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        h.update(headers or {})
        req = urllib.request.Request(
            f"{self.url}{path}", data=data, method=method, headers=h
        )
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, r.read()

    def ensure_bucket(self):
        body = json.dumps({"id": BUCKET, "name": BUCKET, "public": True}).encode()
        try:
            self._req(
                "POST", "/storage/v1/bucket", body, {"Content-Type": "application/json"}
            )
            log(f"  bucket {BUCKET} created (public)")
        except urllib.error.HTTPError as ex:
            if ex.code not in (400, 409):
                raise
            log(f"  bucket {BUCKET} exists")

    def put(self, obj: str, data: bytes, ctype: str, cache: str):
        for attempt in range(4):
            try:
                self._req(
                    "POST",
                    f"/storage/v1/object/{BUCKET}/{obj}",
                    data,
                    {"Content-Type": ctype, "x-upsert": "true", "cache-control": cache},
                )
                self.sent += len(data)
                return
            except urllib.error.HTTPError as ex:
                log(f"  upload {obj}: HTTP {ex.code} {ex.read()[:200]!r}")
                if ex.code < 500:
                    raise
                time.sleep(10 * 2**attempt)
        raise RuntimeError(f"upload failed: {obj}")

    def public(self, obj: str) -> str:
        return f"{self.url}/storage/v1/object/public/{BUCKET}/{obj}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plans", required=True)
    ap.add_argument("--service", default="http://127.0.0.1:8012")
    ap.add_argument("--zmin", type=int, default=10)
    ap.add_argument("--zmax", type=int, default=15)
    ap.add_argument(
        "--raw-plans",
        default="BMRDA-HSK-MP2031",
        help="plans drawn from the sheet worker output by priority, without service merges",
    )
    ap.add_argument(
        "--simplify",
        type=int,
        default=8,
        help="/zones simplify_m: 8 leaves out pieces under 100 m2 (about 2 px at zoom 15)",
    )
    args = ap.parse_args()
    job = os.getenv("QNIT_JOB_DIR")
    if job:
        with open(os.path.join(job, ".lock"), "w") as f:
            f.write(str(os.getpid()))
    raw_plans = {p for p in args.raw_plans.split(",") if p}
    st = Storage()
    st.ensure_bucket()
    plans = {p["plan_id"]: p for p in get_json(f"{args.service}/plans", timeout=60)}
    with open(
        os.path.join(REPO, "infra", "planning", "layer_index.json"), encoding="utf-8"
    ) as f:
        build_id = json.load(f)["build_id"]
    # the manifest keeps plans built earlier (one-time per plan; re-run a plan to replace it)
    try:
        with urllib.request.urlopen(st.public("manifest.json"), timeout=60) as r:
            manifest = json.load(r)
    except Exception:  # noqa: BLE001 - first build
        manifest = {"plans": {}}
    bda = bda_colours()
    t_all = time.time()
    rep = {}
    done: set[str] = set()
    with qf.PeakMeter() as pm, qf.TempArea(f"tiles_{os.getpid()}") as area:
        for plan_id in [p for p in args.plans.split(",") if p]:
            t0 = time.time()
            info = plans[plan_id]
            ext = info["extent"]
            e3857 = (
                *TO_3857.transform(ext[0], ext[1]),
                *TO_3857.transform(ext[2], ext[3]),
            )
            raw = plan_id in raw_plans
            if raw:
                # many overlapping sheets: paint the worker output by priority (#68)
                import tiles_raw as tr

                sheets, lpa3857, ucfg = tr.load_plan(plan_id, area, log)
                ids: dict[tuple, int] = {}
                palette = np.zeros((256, 4), np.uint8)

                def sid(st, ids=ids, palette=palette):
                    if st not in ids:
                        ids[st] = len(ids) + 1
                        palette[ids[st]] = (*st[0], int(255 * st[1]))
                    return ids[st]

                sheets_idx, props = [], []
                for k, gs, ps in sheets:
                    if not gs:
                        continue
                    sids = [sid(style(plan_id, pp, bda)) for pp in ps]
                    sheets_idx.append(
                        (k, shapely.STRtree(np.array(gs, dtype=object)), gs, sids)
                    )
                    props.extend(ps)
                uid = 0
                if ucfg:
                    up = {
                        "zone_label_native": ucfg["zone_label_native"],
                        "class_norm": "uncoloured",
                    }
                    uid = sid(style(plan_id, up, bda))
                    props.append(up)
                zones = props
            else:
                zones = fetch_plan(args.service, plan_id, ext, args.simplify)
                geoms = [g for g, _p in zones]
                props = [p for _g, p in zones]
                styles = [style(plan_id, p, bda) for p in props]
                tree = shapely.STRtree(np.array(geoms, dtype=object))
            by_zoom: dict[int, dict[int, bytes]] = {}
            for z in range(args.zmin, args.zmax + 1):
                tiles = {}
                for x, y in tiles_for(e3857, z):
                    if raw:
                        png = tr.render_tile(
                            z, x, y, sheets_idx, lpa3857, ids, palette, tile_bounds, uid
                        )
                    else:
                        png = render(z, x, y, geoms, props, styles, tree)
                    if png:
                        tiles[zxy_to_tileid(z, x, y)] = png
                by_zoom[z] = tiles
                log(
                    f"  {plan_id} z{z}: {len(tiles)} tiles, {sum(map(len, tiles.values())) / 1e6:.1f} MB"
                )
            # zoom ranges that each fit one object
            groups, cur, size = [], [], 0
            for z in range(args.zmin, args.zmax + 1):
                zs = sum(map(len, by_zoom[z].values()))
                if cur and size + zs > MAX_OBJECT:
                    groups.append(cur)
                    cur, size = [], 0
                cur.append(z)
                size += zs
            groups.append(cur)
            legend = {}
            if raw:
                styles = [style(plan_id, pp, bda) for pp in props]
            for p, s_ in zip(props, styles, strict=True):
                legend.setdefault(
                    p["zone_label_native"],
                    {
                        "label": p["zone_label_native"],
                        "cnorm": p.get("class_norm"),
                        "colour": "#{:02x}{:02x}{:02x}".format(*s_[0]),
                    },
                )
            warnings = sorted(
                {w for p in props for w in ((p.get("qa") or {}).get("warnings") or [])}
            )
            files = []
            for g in groups:
                tiles = {k: v for z in g for k, v in by_zoom[z].items()}
                path = os.path.join(area, f"{plan_id}_z{g[0]}-{g[-1]}.pmtiles")
                n = write_pmtiles(
                    path, tiles, g[0], g[-1], ext, plan_id, {"build_id": build_id}
                )
                with open(path, "rb") as f:
                    data = f.read()
                os.remove(path)
                digest = hashlib.sha256(data).hexdigest()[:12]
                obj = f"{plan_id}/{build_id}-{digest}/z{g[0]}-{g[-1]}.pmtiles"
                st.put(
                    obj,
                    data,
                    "application/octet-stream",
                    "public, max-age=31536000, immutable",
                )
                del data
                files.append(
                    {
                        "url": st.public(obj),
                        "minzoom": g[0],
                        "maxzoom": g[-1],
                        "bytes": n,
                    }
                )
                log(f"  uploaded {obj} ({n / 1e6:.1f} MB)")
            manifest["plans"][plan_id] = {
                "build_id": build_id,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "extent": ext,
                "files": files,
                "legend": sorted(legend.values(), key=lambda e: e["label"]),
                "warnings": warnings,
                "zones": len(zones),
                "placement_unconfirmed": sorted(
                    {
                        p.get("sheet") or ""
                        for p in props
                        if (p.get("qa") or {}).get("placement_confirmed") is False
                    }
                ),
            }
            done.add(plan_id)
            rep[plan_id] = {
                "zones": len(zones),
                "tiles": sum(len(t) for t in by_zoom.values()),
                "mb": round(sum(f["bytes"] for f in files) / 1e6, 1),
                "objects": len(files),
                "seconds": round(time.time() - t0),
            }
            del zones, props, styles, by_zoom
            # publish after every plan, so a plan shows on the map as soon as it is built;
            # re-read first so a parallel build's plans are kept (merge, not overwrite)
            try:
                with urllib.request.urlopen(
                    st.public("manifest.json"), timeout=60
                ) as r:
                    cur = json.load(r)
                cur.setdefault("plans", {}).update(
                    {k: v for k, v in manifest["plans"].items() if k in done}
                )
                manifest = cur
            except Exception as ex:  # noqa: BLE001 - first manifest
                log(f"  no manifest to merge yet ({str(ex)[:80]})")
            st.put(
                "manifest.json",
                json.dumps(manifest, indent=1).encode(),
                "application/json",
                "public, max-age=60",
            )
        st.put(
            "manifest.json",
            json.dumps(manifest, indent=1).encode(),
            "application/json",
            "public, max-age=60",
        )
    print(
        "REPORT "
        + json.dumps(
            {
                "plans": rep,
                "uploaded_mb": round(st.sent / 1e6, 1),
                "peak_temp_mb": round(pm.peak / 1e6, 1),
                "temp_left_bytes": qf.dir_bytes(
                    os.path.join(qf.TEMP_ROOT, f"tiles_{os.getpid()}")
                ),
                "manifest": st.public("manifest.json"),
                "seconds": round(time.time() - t_all),
            }
        ),
        flush=True,
    )
    shutil.rmtree(
        os.path.join(qf.TEMP_ROOT, f"tiles_{os.getpid()}"), ignore_errors=True
    )


if __name__ == "__main__":
    main()
