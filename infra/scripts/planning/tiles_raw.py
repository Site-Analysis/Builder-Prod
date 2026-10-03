"""Raw-sheet mode for build_tiles.py (open-decisions #68): a plan with many overlapping sheets
(Hoskote: 51 detail + hobli maps) is drawn straight from the sheet worker's output, without the
service's priority merge. Sheets are painted from the lowest priority to the highest, so the more
detailed sheet covers the coarser one where they overlap (the same "most detailed sheet wins"
rule as the merge); the result is clipped to the plan's LPA outline, and LPA area on no sheet is
drawn as "Not coloured on the plan" when the plan has that rule. Downloads go to the temp area
and are deleted; the zones live only in this process."""

from __future__ import annotations

import concurrent.futures as cf
import io
import json
import os

import build_layer_index as bli
import numpy as np
import qnit_fetch as qf
import shapely
from PIL import Image, ImageDraw
from pyproj import Transformer

U2M = Transformer.from_crs(32643, 3857, always_xy=True)


def to3857(g):
    return shapely.transform(
        g, lambda xy: np.column_stack(U2M.transform(xy[:, 0], xy[:, 1]))
    )


def load_plan(plan_id: str, area: str, log):
    """(sheets, lpa3857, uncovered style or None): sheets = [(order, [geom3857], [props])]
    from the lowest priority to the highest."""
    with open(bli.INDEX, encoding="utf-8") as f:
        ix = json.load(f)
    rows = [
        r
        for r in ix["rows"]
        if r["plan_id"] == plan_id and r.get("status") == "indexed"
    ]
    zrows = sorted(
        (r for r in rows if r["kind"] == "zones"),
        key=lambda r: (r["priority"]["rank"], r["priority"]["order"]),
    )
    orow = next(r for r in rows if r["row_id"] == ix["plans"][plan_id]["outline_row"])
    srcs = {}
    for r in [orow, *zrows]:
        key = (r["source_url"], r["sha256"])
        if key not in srcs:
            ext = os.path.splitext(r["source_url"])[1] or ".pdf"
            path, _ = qf.download(
                r["source_url"], area, f"src{len(srcs)}{ext}", r["sha256"]
            )
            srcs[key] = path
            log(f"  downloaded {os.path.getsize(path) / 1e6:.1f} MB")
    work = os.path.join(area, "work")
    os.makedirs(work, exist_ok=True)

    def run(r):
        job = {
            **r,
            "source_path": srcs[(r["source_url"], r["sha256"])],
            "chunk_m": 2000.0,
        }
        wd = os.path.join(work, r["row_id"].replace("#", "_"))
        os.makedirs(wd, exist_ok=True)
        job["work_dir"] = wd
        return bli.worker(job, r["row_id"])

    res = run(orow)
    lpa = res["outlines"][0]
    sheets = []
    with cf.ThreadPoolExecutor(
        4
    ) as ex:  # four capped workers (2 GB cap each; 32 GB here)
        for k, (r, res) in enumerate(zip(zrows, ex.map(run, zrows), strict=True)):
            g, cols = bli.table_geoms(res["zones"])
            labels = res["meta"].get("labels") or {}
            props = []
            for code in cols.get("code", []):
                lab = labels.get(str(int(code)), {})
                props.append(
                    {
                        "zone_label_native": lab.get("zone_label_native", "?"),
                        "class_norm": lab.get("class_norm"),
                        "qa": {
                            "placement_confirmed": r.get("placement_confirmed", True)
                        },
                    }
                )
            # clip to the LPA as the service does (pieces outside the outline are cut)
            inside = shapely.intersects(lpa, g) if len(g) else np.array([], bool)
            g, props = g[inside], [p for p, ok in zip(props, inside, strict=True) if ok]
            sheets.append((k, [to3857(x) for x in g], props))
            log(f"  {r['row_id']}: {len(g)} zones")
    for p in srcs.values():
        os.remove(p)
    cfg = (ix["plans"][plan_id] or {}).get("uncovered")
    return (
        sheets[::-1],
        shapely.make_valid(to3857(lpa)),
        cfg,
    )  # paint low -> high priority


def render_tile(
    z, x, y, sheets_idx, lpa, style_ids, palette, tile_bounds, uncovered_id
):
    """sheets_idx: list of (order, STRtree, geoms, style ids) low -> high priority."""
    b = tile_bounds(z, x, y)
    box = shapely.box(*b)
    if not lpa.intersects(box):
        return None
    ss = 2
    px = 256 * ss
    sc = px / (b[2] - b[0])

    def to_px(cs):
        return [((cx - b[0]) * sc, (b[3] - cy) * sc) for cx, cy in cs]

    canvas = np.zeros((px, px), np.uint8)
    for _k, tree, geoms, sids in sheets_idx:
        hit = tree.query(box)
        if not len(hit):
            continue
        # one mask per colour: a hole only cuts its own colour (zones of other colours that
        # sit in the hole stay; drawing holes on one shared canvas erased them, 3 Oct)
        groups: dict[int, list[int]] = {}
        for i in hit.tolist():
            groups.setdefault(int(sids[i]), []).append(i)
        for sid_, idx in groups.items():
            mk = Image.new("L", (px, px), 0)
            d = ImageDraw.Draw(mk)
            for i in idx:
                for poly in shapely.get_parts(geoms[i]):
                    if poly.geom_type != "Polygon" or poly.is_empty:
                        continue
                    e = to_px(poly.exterior.coords)
                    if len(e) >= 3:
                        d.polygon(e, fill=255)
                    for h in poly.interiors:
                        hp = to_px(h.coords)
                        if len(hp) >= 3:
                            d.polygon(hp, fill=0)
            canvas = np.where(np.asarray(mk) > 0, np.uint8(sid_), canvas)
    # LPA mask: clip, and LPA area on no sheet -> "Not coloured on the plan"
    m = Image.new("L", (px, px), 0)
    md = ImageDraw.Draw(m)
    for poly in shapely.get_parts(
        shapely.intersection(lpa, box.buffer((b[2] - b[0]) * 0.01))
    ):
        if poly.geom_type != "Polygon":
            continue
        md.polygon(to_px(poly.exterior.coords), fill=255)
        for h in poly.interiors:
            md.polygon(to_px(h.coords), fill=0)
    mask = np.asarray(m) > 0
    if uncovered_id:
        canvas = np.where(mask & (canvas == 0), uncovered_id, canvas)
    canvas = np.where(mask, canvas, 0)
    if not canvas.any():
        return None
    rgba = palette[canvas]
    img = Image.fromarray(rgba, "RGBA").resize((256, 256), Image.LANCZOS)
    q = img.quantize(colors=96, method=Image.Quantize.FASTOCTREE)
    buf = io.BytesIO()
    q.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
