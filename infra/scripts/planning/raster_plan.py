"""Shared pipeline for raster master-plan sheets (Anekal LPA, Nelamangala LPA).

The Hoskote method (extract_hoskote.py) generalised to sheets that come with their own
georeference (an affine from pixels to EPSG:32643) and their own legend palette:

  per sheet (resumable: <out_dir>/map_<key>.parquet + .json; the parquet is written last)
    1. classify each pixel by the nearest legend colour (chunked by rows: memory), white,
       or unknown; isolated grey pixels around black symbols are halos (-> unknown);
       unknown pixels inside the map frame are filled from their neighbours
    2. OSM check: junctions of the sheet's TRANSPORTATION class vs OSM road junctions,
       within 10 m and same degree (extract_hoskote.junction_check)
    3. polygonise, transform with the sheet affine, clip to the LPA
  merge
    georef floor = median OSM RMSE of the well-matched detail sheets (>= 3 matches); used
    for sheets with fewer matches ("few ground checks") and for every coarser layer;
    position uncertainty = sqrt(georef^2 + m_per_px^2). Priority merge sheet by sheet
    (finer layers first), touching pieces of a class dissolved, only footprint boxes kept
    in memory; LPA area on no sheet = "Not coloured on the plan".
"""

import json
import math
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from extract_hoskote import (
    CRS_M,
    MIN_CHECKS,
    OSM_HIGHWAYS,
    boxsum,
    junction_check,
    osm_junctions,
    raster_junctions,
    read_json,
)
from extract_plucomp import fill_from_neighbours, geoparquet, polygonise
from georef_plucomp import OVERPASS
from pyproj import Transformer
from shapely.affinity import affine_transform

UNKNOWN, WHITE = 0, 1
UNCOLOURED_LABEL = "Not coloured on the plan"
LAYER_RANK = {"detail": 0, "hobli": 1, "lpa_map": 2, "composite": 3}
HALO_MAX = {"unclassified": 12, "transport": 6}  # as extract_hoskote (isolated grey px)
# lz4.overpass-api.de answered when the main instance returned 504s (1 Oct 2026)
FIRST_OVERPASS = ["https://lz4.overpass-api.de/api/interpreter"]
EXTRA_OVERPASS = ["https://overpass.private.coffee/api/interpreter"]


# ---------------------------------------------------------------- OSM
def osm_roads(data_root, name, bbox_wgs, tile_deg=0.045):
    """All public roads in bbox (W, S, E, N), cached. Fetched in tiles of <= ~5 km with
    retries across mirrors; each finished tile is cached on its own, so a restart after an
    Overpass outage resumes instead of starting over."""
    path = os.path.join(data_root, "osm", f"{name}_roads.json")
    if os.path.exists(path):
        return read_json(path)["elements"]
    w0, s0, e0, n0 = bbox_wgs
    ny = max(1, math.ceil((n0 - s0) / tile_deg))
    nx = max(1, math.ceil((e0 - w0) / tile_deg))
    tdir = os.path.join(data_root, "osm", f"{name}_tiles")
    os.makedirs(tdir, exist_ok=True)
    eps = FIRST_OVERPASS + list(OVERPASS) + EXTRA_OVERPASS
    els, seen = [], set()
    for i in range(ny):
        for j in range(nx):
            tpath = os.path.join(tdir, f"t_{i}_{j}.json")
            if os.path.exists(tpath):
                d = read_json(tpath)
            else:
                s_ = s0 + (n0 - s0) * i / ny
                n_ = s0 + (n0 - s0) * (i + 1) / ny
                w_ = w0 + (e0 - w0) * j / nx
                e_ = w0 + (e0 - w0) * (j + 1) / nx
                q = f'way["highway"~"^({OSM_HIGHWAYS})$"]({s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f});'
                body = urllib.parse.urlencode(
                    {"data": f"[out:json][timeout:120];{q}out geom;"}
                ).encode()
                for attempt in range(12):
                    ep = eps[attempt % len(eps)]
                    try:
                        req = urllib.request.Request(
                            ep,
                            data=body,
                            headers={"User-Agent": "builder-prod-planning/1.0"},
                        )
                        with urllib.request.urlopen(req, timeout=180) as r:
                            raw = r.read()
                        d = json.loads(raw)
                        with open(tpath, "wb") as f:
                            f.write(raw)
                        break
                    except Exception as ex:  # noqa: BLE001 - next mirror
                        print(
                            f"  overpass {ep} tile {i},{j}/{ny}x{nx}: {ex}", flush=True
                        )
                        time.sleep(20 + 10 * attempt)
                else:
                    raise SystemExit(
                        f"error: OSM roads tile {i},{j} failed on every mirror (finished tiles are kept)"
                    )
            new = [x for x in d["elements"] if x["id"] not in seen]
            seen.update(x["id"] for x in new)
            els += new
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"elements": els}, f)
    return els


def osm_index(data_root, name, bbox_wgs, cache_dir):
    """(xy, degree, STRtree) of OSM road junctions, cached as npz."""
    p = os.path.join(cache_dir, "osm_junctions.npz")
    if not os.path.exists(p):
        tr = Transformer.from_crs(4326, 32643, always_xy=True)
        xy, deg = osm_junctions(osm_roads(data_root, name, bbox_wgs), tr)
        np.savez(p, xy=xy, deg=deg)
    z = np.load(p)
    return z["xy"], z["deg"], shapely.STRtree(shapely.points(z["xy"]))


# ---------------------------------------------------------------- classify
def hexrgb(h):
    h = h.lstrip("#")
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


def class_keys(classes):
    """classes: [{label, cnorm, colours: [hex, ...]}]; class i has code 10 + i."""
    keys, codes = [], []
    for i, c in enumerate(classes):
        for h in c["colours"]:
            keys.append(hexrgb(h))
            codes.append(10 + i)
    keys += [[255, 255, 255], [0, 0, 0]]
    codes += [WHITE, UNKNOWN]
    return np.array(keys, np.float32), np.array(codes, np.uint8)


def classify(a, keys, codes, max_dist, rows=128):
    out = np.empty(a.shape[:2], np.uint8)
    for r0 in range(0, a.shape[0], rows):
        flat = a[r0 : r0 + rows].reshape(-1, 3).astype(np.float32)
        d2 = ((flat[:, None, :] - keys[None]) ** 2).sum(-1)
        j = d2.argmin(1)
        ok = np.sqrt(d2[np.arange(len(j)), j]) <= max_dist
        out[r0 : r0 + rows] = np.where(ok, codes[j], UNKNOWN).reshape(-1, a.shape[1])
    return out


def drop_halos(cls, classes):
    n = 0
    for i, c in enumerate(classes):
        mx = HALO_MAX.get(c["cnorm"])
        if mx is None:
            continue
        m = cls == 10 + i
        iso = m & (boxsum(m, 3) <= mx)
        cls[iso] = UNKNOWN
        n += int(iso.sum())
    return n


def m_per_px(A):
    a, b, d, e = A[:4]
    return math.sqrt(abs(a * e - b * d))


# ---------------------------------------------------------------- one sheet
def process_sheet(s, plan, classes, lpa, osm, out_dir, max_dist):
    """s: key, name, layer, scale, doc_id, A (px -> EPSG:32643, shapely affine order),
    map_rect (x0, y0, x1, y1 px), load (-> HxWx3 uint8), georef_method, georef_res_m."""
    t0 = time.time()
    classes = s.get("classes", classes)  # per-sheet palette (same class order)
    a = s["load"]()
    x0, y0, x1, y1 = s["map_rect"]
    keys, codes = class_keys(classes)
    cls = classify(a, keys, codes, max_dist)
    del a
    frame = np.zeros(cls.shape, bool)
    frame[y0:y1, x0:x1] = True
    cls[~frame] = WHITE
    halo = drop_halos(cls, classes)
    unknown = (cls == UNKNOWN) & frame
    unknown_pct = 100 * float(unknown.sum()) / max(1, int(frame.sum()))
    cls = fill_from_neighbours(cls, unknown)
    del unknown, frame
    A = s["A"]
    m_px = m_per_px(A)

    def px_to_ground(xy):
        return np.column_stack(
            [
                A[0] * xy[:, 0] + A[1] * xy[:, 1] + A[4],
                A[2] * xy[:, 0] + A[3] * xy[:, 1] + A[5],
            ]
        )

    tcodes = [10 + i for i, c in enumerate(classes) if c["cnorm"] == "transport"]
    chk = {"sheet_junctions": 0, "matched": 0}
    if tcodes and osm is not None:
        sj, sdeg = raster_junctions(np.isin(cls, tcodes), m_px, px_to_ground)
        chk = junction_check(sj, sdeg, *osm)
    polys = polygonise(np.where(cls >= 10, cls, 0).astype(np.uint8))
    del cls
    rows, geoms = [], []
    for code, gp in polys.items():
        if code < 10:
            continue
        for part in shapely.get_parts(shapely.make_valid(affine_transform(gp, A))):
            if lpa is not None and not lpa.contains(part):
                part = shapely.intersection(part, lpa)
            for q in shapely.get_parts(part):
                if q.geom_type != "Polygon" or q.area < 0.5 * m_px * m_px:
                    continue
                rows.append(int(code))
                geoms.append(q)
    georef_m = chk.get("rmse_m") if chk.get("matched", 0) >= MIN_CHECKS else None
    meta = {
        k: s[k]
        for k in (
            "key",
            "name",
            "layer",
            "scale",
            "doc_id",
            "map_rect",
            "georef_method",
        )
    }
    meta.update(
        {
            "A": list(map(float, A)),
            "m_per_px": m_px,
            "georef_res_m": s.get("georef_res_m"),
            "unknown_px_pct": unknown_pct,
            "halo_px_dropped": halo,
            "osm_junctions": chk,
            "qa": {
                "doc_id": s["doc_id"],
                "status": plan["status"],
                "extraction": "raster_palette",
                "georef_rmse_m": georef_m,
                "m_per_px": m_px,
                "georef_method": s["georef_method"],
                "legend_check": "warn",
                "qa_failures": list(s.get("qa_failures", [])),
                "sheet_scale": f"1:{s['scale']:,}" if s.get("scale") else None,
                "source_layer": s["layer"],
                "sheet": s["name"],
                "warnings": list(s.get("qa_warnings", [])),
            },
            "zone_area_ha": sum(q.area for q in geoms) / 1e4,
            "class_area_ha": {
                classes[c - 10]["cnorm"]: round(
                    sum(q.area for cc, q in zip(rows, geoms, strict=True) if cc == c)
                    / 1e4,
                    2,
                )
                for c in sorted(set(rows))
            },
            "seconds": round(time.time() - t0, 1),
        }
    )
    base = os.path.join(out_dir, f"map_{s['key']}")
    geoparquet(
        base + ".parquet.part",
        pa.table({"code": rows}),
        np.array(geoms, dtype=object),
        CRS_M,
    )
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(base + ".parquet.part", base + ".parquet")
    return meta


# ---------------------------------------------------------------- merge
def sheet_floor(order, out_dir, fallback_floor=None, fallback_basis=None):
    metas = {
        s["key"]: read_json(os.path.join(out_dir, f"map_{s['key']}.json"))
        for s in order
    }
    good = [
        m["osm_junctions"]["rmse_m"]
        for m in metas.values()
        if m["layer"] == "detail" and m["osm_junctions"].get("matched", 0) >= MIN_CHECKS
    ]
    floor = float(np.median(good)) if good else None
    basis = f"floor: median OSM RMSE of {len(good)} well-matched detail sheets"
    if floor is None and fallback_floor is not None:
        floor, basis = float(fallback_floor), fallback_basis
    for m in metas.values():
        few = m["osm_junctions"].get("matched", 0) < MIN_CHECKS
        own = m["osm_junctions"].get("rmse_m")
        if (few or m["layer"] != "detail") and floor is not None:
            m["georef_used_m"] = floor
            m["georef_basis"] = basis
            if few:
                m["qa"]["qa_failures"] = sorted(
                    set(m["qa"]["qa_failures"]) | {"few ground checks"}
                )
        elif own is not None:
            m["georef_used_m"] = own
            m["georef_basis"] = "own OSM junction check"
        else:
            m["georef_used_m"] = m.get("georef_res_m") or 0.0
            m["georef_basis"] = "sheet fit residual only (no ground check, no floor)"
            m["qa"]["qa_failures"] = sorted(
                set(m["qa"]["qa_failures"]) | {"few ground checks"}
            )
        m["qa"]["georef_rmse_m"] = m["georef_used_m"]
        m["position_uncertainty_m"] = math.sqrt(
            m["georef_used_m"] ** 2 + m["m_per_px"] ** 2
        )
    return metas, floor


def load_foot(out_dir, key):
    with open(os.path.join(out_dir, f"foot_{key}.wkb"), "rb") as f:
        raw = f.read()
    return shapely.from_wkb(raw) if raw else None


def merge_sheet(s, meta, covs, out_dir):
    key = s["key"]
    t = pq.read_table(os.path.join(out_dir, f"map_{key}.parquet"))
    G = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    codes = t.column("code").to_pylist()
    del t
    sb = shapely.total_bounds(G) if len(G) else (0, 0, -1, -1)
    feet = [
        load_foot(out_dir, k)
        for bx, k in covs
        if bx[0] <= sb[2] and bx[2] >= sb[0] and bx[1] <= sb[3] and bx[3] >= sb[1]
    ]
    for f in feet:
        shapely.prepare(f)
    ca = np.array(feet, dtype=object)
    tree = shapely.STRtree(ca) if len(ca) else None
    min_a = 0.5 * meta["m_per_px"] ** 2
    by_code, before = {}, 0
    for code, q in zip(codes, G, strict=True):
        if tree is not None:
            idx = tree.query(q)
            idx = idx[shapely.intersects(ca[idx], q)] if len(idx) else idx
            if len(idx):
                # on the footprints' 1 cm grid: plain overlay crashed GEOS here (access
                # violation in union_all on the Anekal title map; < 1 cm change)
                bx = shapely.box(*q.bounds)
                cut = [shapely.intersection(c, bx, grid_size=0.01) for c in ca[idx]]
                cut = [
                    p
                    for c in cut
                    for p in shapely.get_parts(c)
                    if p.geom_type == "Polygon"
                ]
                if cut:
                    q = shapely.difference(
                        q,
                        shapely.union_all(np.array(cut, dtype=object), grid_size=0.01),
                        grid_size=0.01,
                    )
        for p in shapely.get_parts(q):
            if p.geom_type != "Polygon" or p.area < min_a:
                continue
            by_code.setdefault(code, []).append(p)
            before += 1
    del G
    out_g, out_c = [], []
    class_u = []
    for code in sorted(by_code):
        u = shapely.union_all(np.array(by_code[code], dtype=object), grid_size=0.01)
        class_u.append(u)
        for p in shapely.get_parts(u):
            if p.geom_type == "Polygon" and not p.is_empty:
                out_g.append(p)
                out_c.append(code)
    out_g = np.array(out_g, dtype=object)
    # footprint: the per-class unions tile without overlap (one raster), so a coverage
    # union is enough; a snapped union_all over every piece crashed GEOS (access violation)
    foot = None
    if len(out_g):
        cu = np.array([shapely.make_valid(u) for u in class_u], dtype=object)
        try:
            foot = shapely.make_valid(shapely.coverage_union_all(cu))
        except shapely.errors.GEOSException:
            # snapping can leave hairline overlaps between classes: union the few class
            # unions (not every piece, which crashed GEOS) on the 1 cm grid
            foot = shapely.union_all(cu, grid_size=0.01)
        foot = shapely.union_all(
            [q for q in shapely.get_parts(foot) if q.geom_type == "Polygon"]
        )
    base = os.path.join(out_dir, f"merged_{key}")
    with open(os.path.join(out_dir, f"foot_{key}.wkb"), "wb") as f:
        f.write(shapely.to_wkb(foot) if foot is not None else b"")
    with open(base + ".json", "w") as f:
        json.dump(
            {"pieces_before_dissolve": before, "pieces_after_dissolve": len(out_g)}, f
        )
    pq.write_table(
        pa.table(
            {"code": out_c, "geometry": pa.array(shapely.to_wkb(out_g), pa.binary())}
        ),
        base + ".parquet.part",
    )
    os.replace(base + ".parquet.part", base + ".parquet")
    return foot


def merge(order, metas, out_dir):
    covs = []
    for s in order:
        key = s["key"]
        done = os.path.join(out_dir, f"merged_{key}.parquet")
        foot = (
            load_foot(out_dir, key)
            if os.path.exists(done)
            else merge_sheet(s, metas[key], covs, out_dir)
        )
        metas[key].update(read_json(os.path.join(out_dir, f"merged_{key}.json")))
        if foot is not None:
            covs.append((tuple(shapely.total_bounds(foot)), key))
        print(f"  merged {s['name']}", flush=True)
    return covs


def _polys(g):
    return [
        q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty
    ]


def _overlay(fn, *a):
    try:
        return fn(*a)
    except shapely.errors.GEOSException:
        return fn(*a, grid_size=0.01)


def uncovered(lpa, covs, out_dir, tile=2000.0):
    x0, y0, x1, y1 = lpa.bounds
    tiles = [
        shapely.box(x, y, x + tile, y + tile)
        for x in np.arange(x0, x1, tile)
        for y in np.arange(y0, y1, tile)
    ]
    cuts = {i: [] for i in range(len(tiles))}
    tree = shapely.STRtree(tiles)
    for bx, k in covs:
        foot = load_foot(out_dir, k)
        for i in tree.query(shapely.box(*bx)):
            cuts[i].extend(_polys(_overlay(shapely.intersection, foot, tiles[i])))
        del foot
    pieces = []
    for i, t in enumerate(tiles):
        part = _overlay(shapely.intersection, lpa, t)
        if not part.is_empty and cuts[i]:
            cut = _overlay(shapely.union_all, np.array(cuts[i], dtype=object))
            part = _overlay(shapely.difference, part, cut)
        cuts[i] = None
        pieces.extend(_polys(part))
    if not pieces:
        return shapely.Polygon()
    return _overlay(shapely.union_all, np.array(pieces, dtype=object))


QA_TYPE = pa.struct(
    [
        ("doc_id", pa.string()),
        ("status", pa.string()),
        ("extraction", pa.string()),
        ("georef_rmse_m", pa.float64()),
        ("m_per_px", pa.float64()),
        ("georef_method", pa.string()),
        ("legend_check", pa.string()),
        ("qa_failures", pa.list_(pa.string())),
        ("sheet_scale", pa.string()),
        ("source_layer", pa.string()),
        ("sheet", pa.string()),
        ("warnings", pa.list_(pa.string())),
    ]
)
ZONE_SCHEMA = pa.schema(
    [
        ("zone_uid", pa.string()),
        ("plan_id", pa.string()),
        ("doc_id", pa.string()),
        ("zone_label_native", pa.string()),
        ("zone_code_native", pa.string()),
        ("class_norm", pa.string()),
        ("status", pa.string()),
        ("status_label", pa.string()),
        ("status_condition", pa.string()),
        ("inferred_under_hatch", pa.bool_()),
        ("inferred_under_stream", pa.bool_()),
        ("note", pa.string()),
        ("cartographic", pa.bool_()),
        ("area_m2", pa.float64()),
        ("source_layer", pa.string()),
        ("source_scale", pa.string()),
        ("sheet", pa.string()),
        ("position_uncertainty_m", pa.float64()),
        ("qa", QA_TYPE),
        ("geometry", pa.binary()),
    ]
)


def zone_row(plan, label, cnorm, area, layer, scale, sheet, doc_id, unc, qa, note=None):
    return {
        "plan_id": plan["plan_id"],
        "doc_id": doc_id,
        "zone_label_native": label,
        "zone_code_native": None,
        "class_norm": cnorm,
        "status": plan["status"],
        "status_label": plan["status_label"],
        "status_condition": plan.get("status_condition") or None,
        "inferred_under_hatch": False,
        "inferred_under_stream": False,
        "note": note,
        "cartographic": False,
        "area_m2": float(area),
        "source_layer": layer,
        "source_scale": f"1:{scale:,}" if scale else None,
        "sheet": sheet,
        "position_uncertainty_m": unc,
        "qa": qa,
    }


def write_zones(path, order, metas, covs, lpa, plan, classes, out_dir, lpa_doc_id):
    rest = uncovered(lpa, covs, out_dir)
    rest_parts = [p for p in _polys(rest) if p.area >= 1.0]
    del rest
    boxes = [bx for bx, _k in covs]
    if rest_parts:
        boxes.append(shapely.total_bounds(np.array(rest_parts, dtype=object)))
    b = np.array(boxes)
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Polygon"],
                "crs": CRS_M.to_json_dict(),
                "bbox": [
                    float(b[:, 0].min()),
                    float(b[:, 1].min()),
                    float(b[:, 2].max()),
                    float(b[:, 3].max()),
                ],
            }
        },
    }
    schema = ZONE_SCHEMA.with_metadata({b"geo": json.dumps(geo).encode()})
    by, layer_area, uid = {}, {}, 0
    prefix = plan["plan_id"]

    def batch(rows, geoms):
        nonlocal uid
        for r in rows:
            uid += 1
            r["zone_uid"] = f"{prefix}-{uid:06d}"
            by[r["class_norm"]] = by.get(r["class_norm"], 0.0) + r["area_m2"]
        for r, w in zip(
            rows, shapely.to_wkb(np.array(geoms, dtype=object)), strict=True
        ):
            r["geometry"] = w
        return pa.Table.from_pylist(rows, schema=schema)

    with pq.ParquetWriter(path + ".part", schema, compression="zstd") as w:
        for s in order:
            meta = metas[s["key"]]
            t = pq.read_table(os.path.join(out_dir, f"merged_{s['key']}.parquet"))
            G = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
            rows = []
            for code, p in zip(t.column("code").to_pylist(), G, strict=True):
                c = classes[code - 10]
                rows.append(
                    zone_row(
                        plan,
                        c["label"],
                        c["cnorm"],
                        p.area,
                        s["layer"],
                        s.get("scale"),
                        s["name"],
                        s["doc_id"],
                        meta["position_uncertainty_m"],
                        meta["qa"],
                    )
                )
                layer_area[s["layer"]] = layer_area.get(s["layer"], 0.0) + p.area
            if rows:
                w.write_table(batch(rows, G))
        none_qa = {
            "doc_id": lpa_doc_id,
            "status": plan["status"],
            "extraction": "raster_palette",
            "georef_rmse_m": None,
            "m_per_px": None,
            "georef_method": None,
            "legend_check": "warn",
            "qa_failures": [],
            "sheet_scale": None,
            "source_layer": None,
            "sheet": None,
            "warnings": [],
        }
        rows = [
            zone_row(
                plan,
                UNCOLOURED_LABEL,
                "uncoloured",
                p.area,
                None,
                None,
                None,
                lpa_doc_id,
                None,
                none_qa,
                note="LPA area on no published zone sheet",
            )
            for p in rest_parts
        ]
        layer_area["none"] = sum(p.area for p in rest_parts)
        if rows:
            w.write_table(batch(rows, rest_parts))
    os.replace(path + ".part", path)
    total = sum(layer_area.values())
    diff = 100.0 * (total - lpa.area) / lpa.area
    return (
        uid,
        by,
        layer_area,
        {
            "zones_total_ha": round(total / 1e4, 1),
            "lpa_ha": round(lpa.area / 1e4, 1),
            "diff_pct": round(diff, 3),
            "pass": abs(diff) <= 0.5,
        },
    )


def write_lpa(path, plan, label, lpa):
    geoparquet(
        path,
        pa.table(
            {
                "plan_id": [plan["plan_id"]],
                "authority": [plan["authority"]],
                "label": [label],
            }
        ),
        np.array([lpa], dtype=object),
        CRS_M,
    )
