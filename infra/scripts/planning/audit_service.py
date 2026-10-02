#!/usr/bin/env python3
"""Audit B6/B7: run the planning service's own functions (no HTTP) over every BDA parcel
and every village key / a point grid. Read-only.

Run with the planning service venv (it imports the service's `app` package):
    services/planning/.venv/Scripts/python infra/scripts/planning/audit_service.py \
        --data-root <dir> --cadastral-dir <cadastral_lake_v2> [--steps b6 b7] [--workers 6]

B6 mirrors GET /zones/at: parcel parts with the same survey number are read from the
parquet (X/Y swap), reprojected to WGS84 as the cadastral /data route does, then passed to
parcel_geometry -> zone_hits -> overlays_nearby.
B7 calls get_authority() for every village key and for a 1 km point grid over both districts.

Writes to <data-root>/planning/audit/:
    zones_at_villages.csv   per village: parcels run, errors, empty, sums outside 95-101 %
    zones_at_errors.csv     every error with its parcel key
    zones_at_sum_off.csv    parcels fully inside the LPA whose hits sum outside 95-101 %
    zones_at_summary.json
    authority_villages_check.csv, authority_points_check.csv, authority_summary.json
"""

import argparse
import collections
import csv
import glob
import json
import os
import sys
import time
import traceback
from multiprocessing import Pool

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "services", "planning"))
for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
    sys.modules.pop(m, None)

from app.services import store as store_mod
from app.services import zones_service as zs

PLAN_ID = "BDA-RMP2031"
DISTS = ("20", "21")
SUM_LO, SUM_HI = 95.0, 101.0
SUM_GOAL = 99.0  # audit fix M1 target: every parcel inside the LPA sums to >= 99 %
GRID_M = 1000.0
TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True).transform

_W = {}


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0].keys()) if rows else ["empty"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=fields, lineterminator="\n", extrasaction="ignore"
        )
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {os.path.basename(path)} ({len(rows)} rows)", flush=True)


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_layers(zdir, reg):
    docs = {r["doc_id"]: r for r in read_csv(os.path.join(reg, "plan_docs.csv"))}
    zones = store_mod._load_layer(os.path.join(zdir, f"{PLAN_ID}.parquet"), docs)
    overlays = store_mod._load_layer(
        os.path.join(zdir, f"{PLAN_ID}_overlays.parquet"), docs
    )
    return store_mod.PlanLayers(PLAN_ID, zones, overlays)


def _init(zdir, reg, lpa_wkb):
    _W["layers"] = load_layers(zdir, reg)
    _W["lpa"] = shapely.from_wkb(lpa_wkb)
    shapely.prepare(_W["lpa"])


def run_village(job):
    """All survey numbers of one village through the /zones/at functions."""
    key, path = job
    layers, lpa = _W["layers"], _W["lpa"]
    out = {
        "key": "/".join(key),
        "parcels_run": 0,
        "parcel_errors": 0,
        "parcels_empty": 0,
        "parcels_sum_off": 0,
        "parcels_below_99": 0,
        "parcels_partly_outside_lpa": 0,
        "parts_without_survey_no": 0,
    }
    errors, sum_off = [], []
    try:
        if "geometry" not in pq.read_schema(path).names:
            out["village_error"] = "parcel file has no geometry"
            return out, errors, sum_off
        t = pq.read_table(path, columns=["survey_no", "geometry", "village_name"])
    except Exception as e:  # noqa: BLE001
        out["village_error"] = f"{type(e).__name__}: {e}"
        return out, errors, sum_off
    sv = [
        str(s) if s not in (None, "") else "" for s in t.column("survey_no").to_pylist()
    ]
    vn = t.column("village_name").to_pylist()
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    g = shapely.transform(g, lambda xy: xy[:, ::-1])  # cadastral X/Y swap
    g = shapely.transform(g, lambda xy: np.column_stack(TO_WGS(xy[:, 0], xy[:, 1])))
    groups = collections.defaultdict(list)
    for i, s in enumerate(sv):
        if s:
            groups[s].append(i)
        else:
            out["parts_without_survey_no"] += 1
    for s, idx in groups.items():
        out["parcels_run"] += 1
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": json.loads(shapely.to_geojson(g[i])),
                    "properties": {"survey_no": s, "village_name": vn[i]},
                }
                for i in idx
            ],
        }
        try:
            parcel, _props = zs.parcel_geometry(fc)
            if parcel.is_empty or parcel.area <= 0:
                raise ValueError("parcel geometry empty after repair")
            hits, traces = zs.zone_hits(layers, parcel)
            zs.overlays_nearby([layers], parcel)
        except Exception as e:  # noqa: BLE001
            out["parcel_errors"] += 1
            errors.append(
                {
                    "key": "/".join(key),
                    "survey": s,
                    "error_type": type(e).__name__,
                    "error": str(e)[:200],
                    "where": traceback.extract_tb(e.__traceback__)[-1].name,
                }
            )
            continue
        if not hits:
            out["parcels_empty"] += 1
            continue
        total = sum(h["overlap_pct"] for h in hits) + sum(
            h["overlap_pct"] for h in traces
        )
        inside = parcel.intersection(lpa).area / parcel.area
        if inside < 0.99:
            out["parcels_partly_outside_lpa"] += 1
            continue
        off = not (SUM_LO <= total <= SUM_HI)
        out["parcels_sum_off"] += off
        if total < SUM_GOAL or off:
            out["parcels_below_99"] += total < SUM_GOAL
            sum_off.append(
                {
                    "key": "/".join(key),
                    "survey": s,
                    "sum_pct": round(total, 2),
                    "area_m2": round(parcel.area, 1),
                    "n_hits": len(hits),
                    "n_trace": len(traces),
                }
            )
    return out, errors, sum_off


def step_b6(args, out_dir):
    t0 = time.time()
    reg = os.path.join(REPO, "infra", "planning")
    zdir = os.path.join(args.data_root, "planning", "zones")
    auth = read_csv(os.path.join(reg, "authority_villages.csv"))
    jobs = []
    for r in auth:
        if r["coverage"] not in ("full", "partial"):
            continue
        k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
        p = os.path.join(
            args.cadastral_dir,
            f"dist_{k[0]}",
            f"taluk_{k[1]}",
            f"hobli_{k[2]}",
            f"vlg_{k[3]}.parquet",
        )
        if os.path.exists(p):
            jobs.append((k, p))
    if args.limit:
        jobs = jobs[: args.limit]
    lpa = (
        pq.read_table(
            os.path.join(zdir, f"{PLAN_ID}_lpa.parquet"), columns=["geometry"]
        )
        .column("geometry")[0]
        .as_py()
    )
    print(
        f"  {len(jobs)} BDA villages with a parcel file; {args.workers} workers",
        flush=True,
    )
    villages, errors, sum_off = [], [], []
    with Pool(args.workers, initializer=_init, initargs=(zdir, reg, lpa)) as pool:
        for n, (v, e, s) in enumerate(
            pool.imap_unordered(run_village, jobs, chunksize=2), 1
        ):
            villages.append(v)
            errors += e
            sum_off += s
            if n % 25 == 0:
                done = sum(x["parcels_run"] for x in villages)
                print(
                    f"  {n}/{len(jobs)} villages, {done:,} parcels, {len(errors)} errors ({time.time() - t0:.0f}s)",
                    flush=True,
                )
    write_csv(
        os.path.join(out_dir, "zones_at_villages.csv"),
        villages,
        [
            "key",
            "parcels_run",
            "parcel_errors",
            "parcels_empty",
            "parcels_sum_off",
            "parcels_below_99",
            "parcels_partly_outside_lpa",
            "parts_without_survey_no",
            "village_error",
        ],
    )
    write_csv(
        os.path.join(out_dir, "zones_at_errors.csv"),
        errors,
        ["key", "survey", "error_type", "error", "where"],
    )
    write_csv(
        os.path.join(out_dir, "zones_at_sum_off.csv"),
        sum_off,
        ["key", "survey", "sum_pct", "area_m2", "n_hits", "n_trace"],
    )
    tot = {
        f: sum(int(v.get(f) or 0) for v in villages)
        for f in (
            "parcels_run",
            "parcel_errors",
            "parcels_empty",
            "parcels_sum_off",
            "parcels_below_99",
            "parcels_partly_outside_lpa",
            "parts_without_survey_no",
        )
    }
    tot["villages"] = len(villages)
    tot["village_errors"] = sum(1 for v in villages if v.get("village_error"))
    tot["errors_by_type"] = dict(
        collections.Counter(f"{e['error_type']} in {e['where']}" for e in errors)
    )
    tot["seconds"] = round(time.time() - t0)
    with open(os.path.join(out_dir, "zones_at_summary.json"), "w") as f:
        json.dump(tot, f, indent=1)
    print(json.dumps(tot, indent=1), flush=True)


def step_b7(args, out_dir):
    from app.routers import registry
    from fastapi import HTTPException

    t0 = time.time()
    reg = os.path.join(REPO, "infra", "planning")
    zdir = os.path.join(args.data_root, "planning", "zones")
    os.environ["FLAGS"] = "feature.planning.layers feature.planning.plan.BDA-RMP2031"
    st = store_mod.Store()
    st.plans = {r["plan_id"]: r for r in read_csv(os.path.join(reg, "plans.csv"))}
    st.docs = {r["doc_id"]: r for r in read_csv(os.path.join(reg, "plan_docs.csv"))}
    auth = read_csv(os.path.join(reg, "authority_villages.csv"))
    for r in auth:
        st.authority[(r["dist"], r["taluk"], r["hobli"], r["vlg"])] = r
        st.authority_dists.add(r["dist"])
    lpa = shapely.from_wkb(
        pq.read_table(
            os.path.join(zdir, f"{PLAN_ID}_lpa.parquet"), columns=["geometry"]
        )
        .column("geometry")[0]
        .as_py()
    )
    shapely.prepare(lpa)
    st.lpa["BDA"] = lpa
    registry.get_store = lambda: (
        st
    )  # the router's store lookup, pointed at the audit store

    def call(**kw):
        try:
            return registry.get_authority(
                **{
                    k: kw.get(k)
                    for k in ("dist", "taluk", "hobli", "vlg", "lat", "lng")
                }
            ), None
        except HTTPException as e:
            return None, f"HTTP {e.status_code}: {e.detail}"
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"

    vrows, counts = [], collections.Counter()
    for r in auth:
        res, err = call(
            dist=r["dist"], taluk=r["taluk"], hobli=r["hobli"], vlg=r["vlg"]
        )
        if err:
            counts["village_error"] += 1
            vrows.append(
                {
                    "key": "/".join((r["dist"], r["taluk"], r["hobli"], r["vlg"])),
                    "problem": err,
                }
            )
            continue
        want_bda = r["coverage"] in ("full", "partial")
        problems = []
        if (res["authority"] == "BDA") != want_bda:
            problems.append(
                f"authority {res['authority']} vs table coverage {r['coverage']}"
            )
        if res["coverage"] != r["coverage"]:
            problems.append(f"coverage {res['coverage']} vs table {r['coverage']}")
        if want_bda and not res["draft_plans"]:
            problems.append("no draft plan listed")
        if res["operative_plan"] is not None:
            problems.append("operative_plan set (RMP 2015 not loaded)")
        counts["village_ok" if not problems else "village_mismatch"] += 1
        if problems:
            vrows.append(
                {
                    "key": "/".join((r["dist"], r["taluk"], r["hobli"], r["vlg"])),
                    "problem": "; ".join(problems),
                }
            )
    # a code in a listed district that the table does not have must be a 404
    res, err = call(dist="20", taluk="99", hobli="99", vlg="999")
    counts["unknown_code_404"] = int(bool(err and err.startswith("HTTP 404")))

    # point grid over both districts
    V = pq.read_table(os.path.join(out_dir, "village_polys.parquet"))
    vg = shapely.from_wkb(V.column("geometry").to_numpy(zero_copy_only=False))
    vkeys = list(
        zip(
            *(V.column(c).to_pylist() for c in ("dist", "taluk", "hobli", "vlg")),
            strict=True,
        )
    )
    vtree = shapely.STRtree(vg)
    x0, y0, x1, y1 = shapely.total_bounds(vg)
    xs, ys = np.meshgrid(np.arange(x0, x1, GRID_M), np.arange(y0, y1, GRID_M))
    pts = shapely.points(xs.ravel(), ys.ravel())
    lng, lat = TO_WGS(xs.ravel(), ys.ravel())
    hit = vtree.query(pts, predicate="within")
    village_of = dict(zip(hit[0].tolist(), hit[1].tolist(), strict=False))
    prows = []
    for i in range(len(pts)):
        res, err = call(lat=float(lat[i]), lng=float(lng[i]))
        if err:
            counts["point_error"] += 1
            prows.append(
                {"lat": round(lat[i], 6), "lng": round(lng[i], 6), "problem": err}
            )
            continue
        counts["points"] += 1
        inside = res["authority"] == "BDA"
        counts["points_in_lpa"] += inside
        j = village_of.get(i)
        if j is None:
            counts["points_no_village_polygon"] += 1
            continue
        a = st.authority.get(tuple(vkeys[j]))
        cov = a["coverage"] if a else "(not in table)"
        problem = ""
        if inside and cov == "none":
            problem = "point inside LPA but village coverage none"
        elif not inside and cov == "full":
            problem = "point outside LPA but village coverage full"
        elif a is None:
            problem = "village not in authority table"
        if problem:
            counts["point_mismatch"] += 1
            share = a["share_pct"] if a else ""
            prows.append(
                {
                    "lat": round(lat[i], 6),
                    "lng": round(lng[i], 6),
                    "village": "/".join(vkeys[j]),
                    "village_name": a["village_name"] if a else "",
                    "coverage": cov,
                    "share_pct": share,
                    "distance_to_lpa_edge_m": round(
                        float(lpa.boundary.distance(pts[i])), 1
                    ),
                    "problem": problem,
                }
            )
        else:
            counts["point_consistent"] += 1
    write_csv(
        os.path.join(out_dir, "authority_villages_check.csv"), vrows, ["key", "problem"]
    )
    write_csv(
        os.path.join(out_dir, "authority_points_check.csv"),
        prows,
        [
            "lat",
            "lng",
            "village",
            "village_name",
            "coverage",
            "share_pct",
            "distance_to_lpa_edge_m",
            "problem",
        ],
    )
    summ = dict(counts) | {
        "villages_checked": len(auth),
        "grid_m": GRID_M,
        "seconds": round(time.time() - t0),
    }
    with open(os.path.join(out_dir, "authority_summary.json"), "w") as f:
        json.dump(summ, f, indent=1)
    print(json.dumps(summ, indent=1), flush=True)


def step_http(args, out_dir):
    """Real HTTP calls to a running planning service: catches what function-level runs
    cannot (serialisation, routing, auth wiring). ~200 parcels sampled from the authority
    table's covered villages (seeded)."""
    import random
    import urllib.error
    import urllib.parse
    import urllib.request

    t0 = time.time()
    base = args.planning_url.rstrip("/")
    auth = [
        r
        for r in read_csv(
            os.path.join(REPO, "infra", "planning", "authority_villages.csv")
        )
        if r["coverage"] in ("full", "partial")
    ]
    rnd = random.Random(2031)
    rnd.shuffle(auth)
    jobs = []
    for r in auth:
        if len(jobs) >= args.http_n:
            break
        k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
        p = os.path.join(
            args.cadastral_dir,
            f"dist_{k[0]}",
            f"taluk_{k[1]}",
            f"hobli_{k[2]}",
            f"vlg_{k[3]}.parquet",
        )
        if not os.path.exists(p) or "survey_no" not in pq.read_schema(p).names:
            continue
        sv = [
            s_
            for s_ in pq.read_table(p, columns=["survey_no"])
            .column("survey_no")
            .to_pylist()
            if s_
        ]
        if sv:
            jobs.append((k, str(rnd.choice(sv)), r["authority"]))
    rows, counts = [], collections.Counter()

    def get(path, q):
        url = f"{base}{path}?{urllib.parse.urlencode(q)}"
        t = time.time()
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                body = resp.read()
                json.loads(body)
                return resp.status, "", round(time.time() - t, 3)
        except urllib.error.HTTPError as e:
            return (
                e.code,
                e.read().decode(errors="replace")[:200],
                round(time.time() - t, 3),
            )
        except Exception as e:  # noqa: BLE001
            return 0, f"{type(e).__name__}: {e}"[:200], round(time.time() - t, 3)

    for k, survey, authority in jobs:
        q = dict(zip(("dist", "taluk", "hobli", "vlg"), k, strict=True))
        for path, params in (("/zones/at", q | {"survey": survey}), ("/authority", q)):
            status, err, secs = get(path, params)
            ok = status == 200
            counts[f"{path} {'ok' if ok else status}"] += 1
            rows.append(
                {
                    "path": path,
                    "key": "/".join(k),
                    "survey": survey if path == "/zones/at" else "",
                    "authority": authority,
                    "status": status,
                    "seconds": secs,
                    "error": err,
                }
            )
    write_csv(
        os.path.join(out_dir, "http_check.csv"),
        rows,
        ["path", "key", "survey", "authority", "status", "seconds", "error"],
    )
    secs = sorted(r["seconds"] for r in rows if r["path"] == "/zones/at")
    summ = dict(counts) | {
        "parcels": len(jobs),
        "zones_at_p50_s": secs[len(secs) // 2] if secs else None,
        "zones_at_p95_s": secs[int(len(secs) * 0.95)] if secs else None,
        "seconds": round(time.time() - t0),
    }
    with open(os.path.join(out_dir, "http_summary.json"), "w") as f:
        json.dump(summ, f, indent=1)
    print(json.dumps(summ, indent=1), flush=True)


# ---------------------------------------------------------------- step G: all loaded plans
LOADED_PLANS = ("BDA-RMP2031", "BMRDA-HSK-MP2031", "BMRDA-ANK-MP2031")
TALUKS = {
    ("20", "1"): "Bangalore North",
    ("20", "2"): "Bangalore South",
    ("20", "3"): "Anekal",
    ("20", "4"): "Bangalore East",
    ("20", "5"): "Yelahanka (Bangalore North Additional)",
    ("21", "1"): "Nelamangala",
    ("21", "2"): "Doddaballapura",
    ("21", "3"): "Devanahalli",
    ("21", "4"): "Hoskote",
}
NOT_A_ZONE = ("uncoloured", "road_space")


def _init_plan(zdir, reg, lpa_wkb, plan_id):
    docs = {r["doc_id"]: r for r in read_csv(os.path.join(reg, "plan_docs.csv"))}
    zones = store_mod._load_layer(os.path.join(zdir, f"{plan_id}.parquet"), docs)
    ov_path = os.path.join(zdir, f"{plan_id}_overlays.parquet")
    overlays = store_mod._load_layer(ov_path, docs) if os.path.exists(ov_path) else None
    _W["layers"] = store_mod.PlanLayers(plan_id, zones, overlays)
    _W["lpa"] = shapely.from_wkb(lpa_wkb)
    shapely.prepare(_W["lpa"])
    _W["plan_id"] = plan_id


def run_village_plan(job):
    """run_village for one plan + the uncoloured share of parcels inside its LPA."""
    out, errors, sum_off = run_village(job)
    out["plan_id"] = _W["plan_id"]
    # uncoloured area share (parcels inside the LPA), from a second light pass
    _key, path = job
    un = tot = 0.0
    try:
        t = pq.read_table(path, columns=["survey_no", "geometry"])
        g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
        g = shapely.transform(g, lambda xy: xy[:, ::-1])
        g = g[shapely.contains(_W["lpa"], g)]
        if len(g):
            z = _W["layers"].zones
            for p in g:
                idx = z.sindex.query(p, predicate="intersects")
                if not len(idx):
                    continue
                sub = z.iloc[idx]
                a = shapely.area(
                    shapely.intersection(np.asarray(sub.geometry.values), p)
                )
                tot += float(a.sum())
                un += float(a[np.isin(sub["class_norm"].to_numpy(), NOT_A_ZONE)].sum())
    except Exception:  # noqa: BLE001, S110 - the share is informative only
        pass
    out["zone_area_m2"] = round(tot, 1)
    out["uncoloured_area_m2"] = round(un, 1)
    return out, errors, sum_off


def step_all(args, out_dir):
    """Every parcel in each loaded plan's LPA through the /zones/at functions, one plan at
    a time (memory), then the per-taluk table (G1)."""
    t0 = time.time()
    reg = os.path.join(REPO, "infra", "planning")
    zdir = os.path.join(args.data_root, "planning", "zones")
    v = pq.read_table(os.path.join(args.data_root, "planning", "villages.parquet"))
    vg = shapely.from_wkb(v.column("geometry").to_numpy(zero_copy_only=False))
    vkeys = list(
        zip(
            *(v.column(c).to_pylist() for c in ("dist", "taluk", "hobli", "vlg")),
            strict=True,
        )
    )
    all_villages, all_errors, all_off = [], [], []
    for plan_id in args.plans or LOADED_PLANS:
        lp = os.path.join(zdir, f"{plan_id}_lpa.parquet")
        if not os.path.exists(
            os.path.join(zdir, f"{plan_id}.parquet")
        ) or not os.path.exists(lp):
            print(f"  {plan_id}: not loaded, skipped", flush=True)
            continue
        lpa_g = shapely.union_all(
            shapely.from_wkb(
                pq.read_table(lp, columns=["geometry"])
                .column("geometry")
                .to_numpy(zero_copy_only=False)
            )
        )
        hit = shapely.intersects(vg, lpa_g) & ~shapely.is_empty(vg)
        jobs = []
        for k, h in zip(vkeys, hit, strict=True):
            if not h:
                continue
            p = os.path.join(
                args.cadastral_dir,
                f"dist_{k[0]}",
                f"taluk_{k[1]}",
                f"hobli_{k[2]}",
                f"vlg_{k[3]}.parquet",
            )
            if os.path.exists(p):
                jobs.append((k, p))
        if args.limit:
            jobs = jobs[: args.limit]
        workers = 2 if plan_id != "BDA-RMP2031" else args.workers
        print(
            f"  {plan_id}: {len(jobs)} villages touching its LPA; {workers} workers",
            flush=True,
        )
        with Pool(
            workers,
            initializer=_init_plan,
            initargs=(zdir, reg, shapely.to_wkb(lpa_g), plan_id),
        ) as pool:
            for n, (vv, e, s) in enumerate(
                pool.imap_unordered(run_village_plan, jobs, chunksize=2), 1
            ):
                all_villages.append(vv)
                all_errors += [x | {"plan_id": plan_id} for x in e]
                all_off += [x | {"plan_id": plan_id} for x in s]
                if n % 50 == 0:
                    print(
                        f"    {n}/{len(jobs)} villages, {len(all_errors)} errors ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
    fields = [
        "plan_id",
        "key",
        "parcels_run",
        "parcel_errors",
        "parcels_empty",
        "parcels_sum_off",
        "parcels_below_99",
        "parcels_partly_outside_lpa",
        "parts_without_survey_no",
        "zone_area_m2",
        "uncoloured_area_m2",
        "village_error",
    ]
    # one file set per run, so plans can be audited separately; the taluk step reads them all
    tag = "_".join(args.plans) if args.plans else "all"
    write_csv(
        os.path.join(out_dir, f"all_zones_at_villages__{tag}.csv"), all_villages, fields
    )
    write_csv(
        os.path.join(out_dir, f"all_zones_at_errors__{tag}.csv"),
        all_errors,
        ["plan_id", "key", "survey", "error_type", "error", "where"],
    )
    write_csv(
        os.path.join(out_dir, f"all_zones_at_below99__{tag}.csv"),
        all_off,
        ["plan_id", "key", "survey", "sum_pct", "area_m2", "n_hits", "n_trace"],
    )
    print(f"  done ({time.time() - t0:.0f}s)", flush=True)


def step_taluk(args, out_dir):
    """G1 table: per taluk, villages, plan_coverage counts, parcels inside loaded plans
    summing >= 99 %, uncoloured %, /zones/at function errors and HTTP errors."""
    auth = read_csv(os.path.join(REPO, "infra", "planning", "authority_villages.csv"))
    vil = [
        r
        for f in sorted(
            glob.glob(os.path.join(out_dir, "all_zones_at_villages__*.csv"))
        )
        for r in read_csv(f)
    ]
    http = (
        read_csv(os.path.join(out_dir, "http500_check.csv"))
        if os.path.exists(os.path.join(out_dir, "http500_check.csv"))
        else []
    )
    rows = []
    for (d, t), name in TALUKS.items():
        a = [r for r in auth if (r["dist"], r["taluk"]) == (d, t)]
        pc = collections.Counter(r["plan_coverage"] or "(blank)" for r in a)
        vv = [r for r in vil if r["key"].split("/")[:2] == [d, t]]
        run = sum(int(r["parcels_run"] or 0) for r in vv)
        outside = sum(int(r["parcels_partly_outside_lpa"] or 0) for r in vv)
        empty = sum(int(r["parcels_empty"] or 0) for r in vv)
        below = sum(int(r["parcels_below_99"] or 0) for r in vv)
        inside = run - outside - empty - sum(int(r["parcel_errors"] or 0) for r in vv)
        za = sum(float(r["zone_area_m2"] or 0) for r in vv)
        ua = sum(float(r["uncoloured_area_m2"] or 0) for r in vv)
        he = [
            h
            for h in http
            if h["key"].split("/")[:2] == [d, t] and h["status"] != "200"
        ]
        rows.append(
            {
                "taluk": f"{name} ({d}/{t})",
                "villages": len(a),
                **{
                    k: pc.get(k, 0)
                    for k in (
                        "plan_loaded",
                        "plan_registered_not_loaded",
                        "lpa_no_zone_map",
                        "no_master_plan_found",
                        "(blank)",
                    )
                },
                "parcels_inside_loaded_plans": inside,
                "parcels_sum_ge_99": inside - below,
                "parcels_sum_ge_99_pct": round(100 * (inside - below) / inside, 3)
                if inside
                else None,
                "uncoloured_pct": round(100 * ua / za, 2) if za else None,
                "zones_at_errors": sum(int(r["parcel_errors"] or 0) for r in vv),
                "http_errors": len(he),
            }
        )
    write_csv(os.path.join(out_dir, "g1_taluks.csv"), rows)
    with open(os.path.join(out_dir, "g1_taluks.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print(json.dumps(rows, indent=1), flush=True)


def step_http500(args, out_dir):
    """G2: ~500 parcels over HTTP, spread across plans and plan_coverage values (seeded):
    status codes, JSON validity, p50/p95 latency per endpoint."""
    import random
    import urllib.error
    import urllib.parse
    import urllib.request

    t0 = time.time()
    base = args.planning_url.rstrip("/")
    auth = read_csv(os.path.join(REPO, "infra", "planning", "authority_villages.csv"))
    strata = collections.defaultdict(list)
    for r in auth:
        strata[(r["plan_coverage"], r["authority"] or "-")].append(r)
    rnd = random.Random(2031)
    n_target = args.http_n
    per = max(5, n_target // max(1, len(strata)))
    jobs = []
    for _s, rs in sorted(strata.items()):
        rnd.shuffle(rs)
        for r in rs[: per * 3]:
            if sum(1 for j in jobs if j[3] == _s) >= per:
                break
            k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
            p = os.path.join(
                args.cadastral_dir,
                f"dist_{k[0]}",
                f"taluk_{k[1]}",
                f"hobli_{k[2]}",
                f"vlg_{k[3]}.parquet",
            )
            survey = None
            if os.path.exists(p) and "survey_no" in pq.read_schema(p).names:
                sv = [
                    x
                    for x in pq.read_table(p, columns=["survey_no"])
                    .column("survey_no")
                    .to_pylist()
                    if x
                ]
                survey = str(rnd.choice(sv)) if sv else None
            jobs.append((k, survey, r["authority"], _s))
    # top up from the largest strata to reach the target
    big = sorted(strata.items(), key=lambda kv: -len(kv[1]))
    i = 0
    while len(jobs) < n_target and i < 5000:
        s_, rs = big[i % len(big)]
        r = rs[rnd.randrange(len(rs))]
        k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
        p = os.path.join(
            args.cadastral_dir,
            f"dist_{k[0]}",
            f"taluk_{k[1]}",
            f"hobli_{k[2]}",
            f"vlg_{k[3]}.parquet",
        )
        if os.path.exists(p) and "survey_no" in pq.read_schema(p).names:
            sv = [
                x
                for x in pq.read_table(p, columns=["survey_no"])
                .column("survey_no")
                .to_pylist()
                if x
            ]
            if sv:
                jobs.append((k, str(rnd.choice(sv)), r["authority"], s_))
        i += 1

    def get(path, q):
        url = f"{base}{path}?{urllib.parse.urlencode(q)}"
        t = time.time()
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                body = resp.read()
                try:
                    json.loads(body)
                    valid = True
                except ValueError:
                    valid = False
                return resp.status, valid, "", round(time.time() - t, 3)
        except urllib.error.HTTPError as e:
            return (
                e.code,
                False,
                e.read().decode(errors="replace")[:200],
                round(time.time() - t, 3),
            )
        except Exception as e:  # noqa: BLE001
            return 0, False, f"{type(e).__name__}: {e}"[:200], round(time.time() - t, 3)

    rows = []
    for k, survey, authority, stratum in jobs:
        q = dict(zip(("dist", "taluk", "hobli", "vlg"), k, strict=True))
        calls = [("/authority", q)]
        if survey:
            calls.append(("/zones/at", q | {"survey": survey}))
        for path, params in calls:
            status, valid, err, secs = get(path, params)
            rows.append(
                {
                    "path": path,
                    "key": "/".join(k),
                    "survey": survey or "",
                    "authority": authority,
                    "plan_coverage": stratum[0],
                    "status": status,
                    "json_valid": valid,
                    "seconds": secs,
                    "error": err,
                }
            )
    write_csv(os.path.join(out_dir, "http500_check.csv"), rows)
    summ = {"parcels": len(jobs), "calls": len(rows), "strata": len(strata)}
    for path in ("/zones/at", "/authority"):
        rr = [r for r in rows if r["path"] == path]
        secs = sorted(r["seconds"] for r in rr)
        summ[path] = {
            "status": dict(collections.Counter(str(r["status"]) for r in rr)),
            "json_valid": sum(1 for r in rr if r["json_valid"]),
            "p50_s": secs[len(secs) // 2] if secs else None,
            "p95_s": secs[int(len(secs) * 0.95)] if secs else None,
        }
    summ["by_plan_coverage"] = dict(
        collections.Counter(
            r["plan_coverage"] for r in rows if r["path"] == "/authority"
        )
    )
    summ["seconds"] = round(time.time() - t0)
    with open(os.path.join(out_dir, "http500_summary.json"), "w") as f:
        json.dump(summ, f, indent=1)
    print(json.dumps(summ, indent=1), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    ap.add_argument("--steps", nargs="+", default=["b6", "b7"])
    ap.add_argument("--planning-url", default="http://localhost:8012")
    ap.add_argument("--http-n", type=int, default=200)
    ap.add_argument(
        "--plans",
        nargs="*",
        default=None,
        help="all: plan_ids to audit (default: every loaded plan)",
    )
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument(
        "--limit", type=int, default=0, help="b6: first N villages only (timing)"
    )
    args = ap.parse_args()
    out_dir = os.path.join(args.data_root, "planning", "audit")
    os.makedirs(out_dir, exist_ok=True)
    for s in args.steps:
        print(f"[{s}]", flush=True)
        {
            "b6": step_b6,
            "b7": step_b7,
            "http": step_http,
            "all": step_all,
            "http500": step_http500,
            "taluk": step_taluk,
        }[s](args, out_dir)


if __name__ == "__main__":
    main()
