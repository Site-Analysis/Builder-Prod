#!/usr/bin/env python3
"""Coverage and accuracy audit of the BDA RMP 2031 (Draft) layer. Read-only: fixes nothing.

Usage:
    python audit_rmp2031.py --data-root <dir> --cadastral-dir <cadastral_lake_v2> \
        [--steps villages a1 a2 a3 a4 a5 c9 c10 summary]

Needs <data-root>/planning/zones/BDA-RMP2031_pd_extents.parquet from
`crosscheck_pdr.py --all --extents` (PD boundaries are not published as vectors; each PD's
area is the coloured extent of its PDR figure registered onto PLUCOMP).

Writes CSVs under <data-root>/planning/audit/:
    villages      village_polys.parquet (union of parcels, EPSG:32643; cache for other steps)
    a1            pd_landuse.csv, pd_vs_pdr.csv       per-PD zone area by class vs PDR tables
    a2            gaps.csv                            LPA area with no zone/overlay polygon
    a3            admin_hobli.csv, bda_villages_no_parcels.csv, admin_errors.csv
    a4            village_quality.csv                 uncoloured / inferred share per BDA village
    a5            documents.csv                       plan_docs rows and their use
    c9            georef_pd.csv, georef_points.csv    OSM junction residuals per PD
    c10           sample_parcels.csv, sample_mismatch_picks.json (for acceptance_pack.py)
    summary       summary_taluk.csv (reads the B6/B7 CSVs from audit_service.py if present)
"""

import argparse
import csv
import glob
import itertools
import json
import os
import random
import re
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import shapely
from pyproj import CRS, Transformer

sys.path.insert(0, os.path.dirname(__file__))
from build_authority import village_names
from extract_plucomp import geoparquet, load_legend, load_mosaic
from georef_plucomp import (
    apply_affine,
    isolated,
    junctions_from_segments,
    osm,
    osm_lines_and_junctions,
    plucomp_roads,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(REPO, "infra", "planning")
PLAN_ID = "BDA-RMP2031"
DISTS = ("20", "21")
PDR_TOL_PP = 5.0  # +- percentage points of PD area
VILLAGE_FLAG_PCT = 30.0
GEOREF_FLAG_M = 20.0
JUNCTION_ISOLATION_M = 150.0
JUNCTION_PAIR_M = 60.0
MISMATCH_RESIDUAL_M = (
    40.0  # same rule as georef_plucomp: dropped when the sheet junction
)
MISMATCH_ON_ROAD_M = 30.0  # sits on an OSM road but its cross road is missing from OSM
SAMPLE_PER_PD = 3
SEED = 2031
TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True).transform

# PDR table rows -> our classes (PDR "Unclassified" is grey on the PD figures, as are
# Defense areas on PLUCOMP; compared against our uncoloured + Defense together)
PDR_MAP = {
    "residential": ["Residential"],
    "commercial": ["Commercial"],
    "industrial": ["Industrial"],
    "public & semi public": ["Public and Semi Public"],
    "public and semi public": ["Public and Semi Public"],
    "unclassified": ["Not coloured on the plan", "Defense"],
    "defence": ["Defense"],
    "defense": ["Defense"],
    "public utility": ["Public Utilities"],
    "public utilities": ["Public Utilities"],
    "parks / open spaces": ["Parks and Open spaces"],
    "parks/open spaces": ["Parks and Open spaces"],
    "parks & open spaces": ["Parks and Open spaces"],
    "transport & communication": ["Transport and Communication"],
    "transport and communication": ["Transport and Communication"],
    "water bodies": ["Water Bodies"],
    "agriculture": ["Agriculture"],
    "forest": ["@forest_symbol"],
    "ngt buffer": ["@ngt_buffer"],
    "streams": ["@stream_inferred"],
}


# ---------------------------------------------------------------- io
def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0].keys()) if rows else ["empty"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {os.path.basename(path)} ({len(rows)} rows)", flush=True)


def load_table(path, cols):
    t = pq.read_table(path, columns=[*cols, "geometry"])
    return {c: t.column(c).to_pylist() for c in cols}, shapely.from_wkb(
        t.column("geometry").to_numpy(zero_copy_only=False)
    )


class Ctx:
    def __init__(self, args):
        self.args = args
        self.zdir = os.path.join(args.data_root, "planning", "zones")
        self.out = os.path.join(args.data_root, "planning", "audit")
        os.makedirs(self.out, exist_ok=True)
        Z, self.zg = load_table(
            os.path.join(self.zdir, f"{PLAN_ID}.parquet"),
            ["zone_label_native", "class_norm", "note"],
        )
        self.zlab = np.array(Z["zone_label_native"], dtype=object)
        self.zcls = np.array(Z["class_norm"], dtype=object)
        self.znote = np.array([n or "" for n in Z["note"]], dtype=object)
        self.ztree = shapely.STRtree(self.zg)
        O, self.og = load_table(
            os.path.join(self.zdir, f"{PLAN_ID}_overlays.parquet"), ["class_norm"]
        )
        self.ocls = np.array(O["class_norm"], dtype=object)
        self.otree = shapely.STRtree(self.og)
        self.lpa = load_table(os.path.join(self.zdir, f"{PLAN_ID}_lpa.parquet"), [])[1][
            0
        ]
        shapely.prepare(self.lpa)
        ext = os.path.join(self.zdir, f"{PLAN_ID}_pd_extents.parquet")
        if os.path.exists(ext):
            E, g = load_table(ext, ["pd", "area_ha", "registration_agreement"])
            self.pds = {int(p): geom for p, geom in zip(E["pd"], g, strict=True)}
            self.pd_reg = dict(zip(E["pd"], E["registration_agreement"], strict=True))
        else:
            self.pds, self.pd_reg = {}, {}
        self.auth = {
            (r["dist"], r["taluk"], r["hobli"], r["vlg"]): r
            for r in read_csv(os.path.join(REG, "authority_villages.csv"))
        }
        self.names = {
            k: v for k, v in village_names(args.cadastral_dir).items() if k[0] in DISTS
        }
        self._vp = None

    # zone areas inside a geometry, by label, plus uncoloured / inferred areas
    def zone_areas(self, geom):
        idx = self.ztree.query(geom, predicate="intersects")
        if not len(idx):
            return {}, 0.0, 0.0, 0.0
        inter = shapely.area(shapely.intersection(self.zg[idx], geom))
        by = {}
        for lab, a in zip(self.zlab[idx], inter, strict=True):
            by[lab] = by.get(lab, 0.0) + float(a)
        unc = float(inter[self.zcls[idx] == "uncoloured"].sum())
        inf = float(inter[self.znote[idx] != ""].sum())
        inf_stream = float(
            inter[np.char.find(self.znote[idx].astype(str), "stream") >= 0].sum()
        )
        return by, unc, inf, inf_stream

    def overlay_area(self, geom, kind):
        idx = self.otree.query(geom, predicate="intersects")
        idx = idx[self.ocls[idx] == kind]
        if not len(idx):
            return 0.0
        return float(shapely.intersection(shapely.union_all(self.og[idx]), geom).area)

    def pd_of(self, geom):
        best, ba = None, 0.0
        for pd, g in self.pds.items():
            if g.intersects(geom):
                a = g.intersection(geom).area
                if a > ba:
                    best, ba = pd, a
        return best

    def village_polys(self):
        if self._vp is None:
            path = os.path.join(self.out, "village_polys.parquet")
            if not os.path.exists(path):
                step_villages(self)
            V, g = load_table(path, ["dist", "taluk", "hobli", "vlg", "n_parcels"])
            self._vp = {
                (d, t, h, v): (geom, n)
                for d, t, h, v, n, geom in zip(
                    V["dist"],
                    V["taluk"],
                    V["hobli"],
                    V["vlg"],
                    V["n_parcels"],
                    g,
                    strict=True,
                )
            }
        return self._vp


def parquet_path(cad, k):
    return os.path.join(
        cad, f"dist_{k[0]}", f"taluk_{k[1]}", f"hobli_{k[2]}", f"vlg_{k[3]}.parquet"
    )


def read_parcels(cad, k):
    """(survey_no list, geometry array in EPSG:32643) or None when the file has no geometry."""
    f = parquet_path(cad, k)
    if not os.path.exists(f) or "geometry" not in pq.read_schema(f).names:
        return None
    t = pq.read_table(f, columns=["survey_no", "geometry"])
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    g = shapely.make_valid(
        shapely.transform(g, lambda xy: xy[:, ::-1])
    )  # (N, E) -> (E, N)
    return [
        str(s) if s is not None else "" for s in t.column("survey_no").to_pylist()
    ], g


# ---------------------------------------------------------------- steps
def step_villages(c):
    """Village outline = union of its parcels. Cached for the other steps."""
    rows, geoms = [], []
    for f in sorted(
        glob.glob(
            os.path.join(
                c.args.cadastral_dir,
                "dist_2[01]",
                "taluk_*",
                "hobli_*",
                "vlg_*.parquet",
            )
        )
    ):
        parts = f.replace("\\", "/").split("/")
        k = (parts[-4][5:], parts[-3][6:], parts[-2][6:], parts[-1][4:-8])
        p = read_parcels(c.args.cadastral_dir, k)
        if p is None:
            continue
        g = shapely.get_parts(p[1])
        g = g[np.isin(shapely.get_type_id(g), (3, 6))]
        if not len(g):
            continue
        u = shapely.make_valid(shapely.union_all(g, grid_size=0.01))
        rows.append(
            dict(zip(("dist", "taluk", "hobli", "vlg"), k, strict=True))
            | {"n_parcels": len(p[0])}
        )
        geoms.append(u)
    geoparquet(
        os.path.join(c.out, "village_polys.parquet"),
        pa.table(
            {
                k: [r[k] for r in rows]
                for k in ("dist", "taluk", "hobli", "vlg", "n_parcels")
            }
        ),
        np.array(geoms, dtype=object),
        CRS.from_epsg(32643),
    )
    print(f"  village outlines: {len(rows)}", flush=True)


def pdr_tables(pdr_path):
    """PD -> {category: (area_ha, pct)} from each 'PD n Proposed Land Use Area Statement'."""
    doc = pymupdf.open(pdr_path)
    out = {}
    # the PD comes from the chapter number (Table N-x is PD N-1): captions misprint the PD
    # (PD 11's table says "PD 10") or leave it out (PD 33)
    pat = re.compile(
        r"Table\s*(\d+)\s*-\s*\d+\s*:?\s*(?:PD\s*0?\d+\s+)?Prop\w*\s+Land\s*use\s+Area\s+St\w*",
        re.IGNORECASE,
    )
    for i in range(10, len(doc)):  # skip the table of contents
        page = doc[i]
        m = pat.search(page.get_text())
        if not m:
            continue
        pd = int(m.group(1)) - 1
        rows = {}
        for pg in (page, doc[i + 1] if i + 1 < len(doc) else None):
            if pg is None:
                continue
            for tb in pg.find_tables().tables:
                for r in tb.extract():
                    if not r or not r[0]:
                        continue
                    name = re.sub(r"\s+", " ", r[0]).strip().lower()
                    try:
                        area = float(str(r[1]).replace(",", ""))
                    except (TypeError, ValueError, IndexError):
                        continue
                    rows.setdefault(name, area)
            if "total pd area" in rows:
                break
        if "total pd area" not in rows:
            rows = text_rows(page.get_text()[m.end() :]) or rows
        if pd not in out and rows:
            out[pd] = {"page": i + 1, "rows": rows}
    return out


def text_rows(text):
    """Fallback when no table is detected: 'category' line followed by an area line."""
    rows, lines = {}, [ln.strip() for ln in text.splitlines() if ln.strip()]
    for a, b in itertools.pairwise(lines):
        name = re.sub(r"\s+", " ", a).lower()
        if name in PDR_MAP or name.startswith("total"):
            try:
                rows.setdefault(name, float(b.replace(",", "")))
            except ValueError:
                continue
        if "total pd area" in rows:
            break
    return rows


def step_a1(c):
    t0 = time.time()
    tables = pdr_tables(
        os.path.join(c.args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-PDR.pdf")
    )
    classes = sorted(set(c.zlab))
    land, comp = [], []
    for pd in range(1, 43):
        g = c.pds.get(pd)
        base = {"pd": pd}
        if g is None or g.is_empty:
            land.append(base | {"status": "no PD extent (PDR figure not registered)"})
            continue
        area = g.area
        by, unc, inf, inf_stream = c.zone_areas(g)
        zone_area = sum(by.values())
        row = (
            base
            | {
                "status": "ok" if zone_area > 0 else "no zones",
                "pd_extent_ha": round(area / 1e4, 1),
                "zone_area_ha": round(zone_area / 1e4, 1),
                "no_polygon_pct": round(100 * (1 - zone_area / area), 2),
                "uncoloured_pct": round(100 * unc / area, 2),
                "inferred_pct": round(100 * inf / area, 2),
                "ngt_buffer_pct": round(
                    100 * c.overlay_area(g, "ngt_buffer") / area, 2
                ),
                "forest_symbol_pct": round(
                    100 * c.overlay_area(g, "forest_symbol") / area, 2
                ),
                "registration_agreement": round(c.pd_reg.get(pd, float("nan")), 3),
            }
            | {f"{k} %": round(100 * by.get(k, 0.0) / area, 2) for k in classes}
        )
        land.append(row)
        ours = {k: 100 * v / area for k, v in by.items()}
        ours["@ngt_buffer"] = row["ngt_buffer_pct"]
        ours["@forest_symbol"] = row["forest_symbol_pct"]
        ours["@stream_inferred"] = 100 * inf_stream / area
        t = tables.get(pd)
        if not t:
            comp.append(
                {"pd": pd, "category": "(table)", "result": "no PDR table found"}
            )
            continue
        if sum(1 for n in t["rows"] if not n.startswith("total")) < 5:
            comp.append(
                {
                    "pd": pd,
                    "category": "(table)",
                    "result": "PDR table incomplete (category areas blank in the source)",
                    "pdr_page": t["page"],
                }
            )
            continue
        total = t["rows"].get("total pd area") or t["rows"].get(
            "total developable area"
        )
        comp.append(
            {
                "pd": pd,
                "category": "Total PD area (ha)",
                "pdr_ha": total,
                "ours_ha": round(area / 1e4, 1),
                "diff_pp": round(100 * (area / 1e4 - total) / total, 1)
                if total
                else "",
                "result": "info",
                "pdr_page": t["page"],
            }
        )
        # PLUCOMP draws roads as vector lines over white corridors, so road space is
        # "Not coloured" (or a dropped sliver) in our layer but "Transport" in the PDR
        pdr_roads = sum(
            v
            for n, v in t["rows"].items()
            if n
            in (
                "unclassified",
                "transport & communication",
                "transport and communication",
            )
        )
        ours_roads = sum(
            ours.get(x, 0.0)
            for x in (
                "Not coloured on the plan",
                "Defense",
                "Transport and Communication",
            )
        )
        ours_roads += row["no_polygon_pct"]
        pr = 100 * pdr_roads / total if total else 0.0
        comp.append(
            {
                "pd": pd,
                "category": "(combined) unclassified + transport",
                "ours_as": "Not coloured + Defense + Transport + no polygon",
                "pdr_ha": round(pdr_roads, 2),
                "pdr_pct": round(pr, 2),
                "ours_pct": round(ours_roads, 2),
                "diff_pp": round(ours_roads - pr, 2),
                "result": "pass" if abs(ours_roads - pr) <= PDR_TOL_PP else "FAIL",
                "pdr_page": t["page"],
            }
        )
        for name, area_ha in t["rows"].items():
            if name.startswith("total"):
                continue
            ours_labels = PDR_MAP.get(name)
            if ours_labels is None:
                comp.append(
                    {
                        "pd": pd,
                        "category": name,
                        "pdr_ha": area_ha,
                        "result": "unmapped PDR category",
                    }
                )
                continue
            pdr_pct = 100 * area_ha / total if total else 0.0
            our_pct = sum(ours.get(x, 0.0) for x in ours_labels)
            diff = our_pct - pdr_pct
            comp.append(
                {
                    "pd": pd,
                    "category": name,
                    "ours_as": " + ".join(ours_labels),
                    "pdr_ha": area_ha,
                    "pdr_pct": round(pdr_pct, 2),
                    "ours_pct": round(our_pct, 2),
                    "diff_pp": round(diff, 2),
                    "result": "pass" if abs(diff) <= PDR_TOL_PP else "FAIL",
                    "pdr_page": t["page"],
                }
            )
    fields = [
        "pd",
        "status",
        "pd_extent_ha",
        "zone_area_ha",
        "no_polygon_pct",
        "uncoloured_pct",
        "inferred_pct",
        "ngt_buffer_pct",
        "forest_symbol_pct",
        "registration_agreement",
    ]
    fields += [f"{k} %" for k in classes]
    write_csv(os.path.join(c.out, "pd_landuse.csv"), land, fields)
    write_csv(
        os.path.join(c.out, "pd_vs_pdr.csv"),
        comp,
        [
            "pd",
            "category",
            "ours_as",
            "pdr_ha",
            "ours_ha",
            "pdr_pct",
            "ours_pct",
            "diff_pp",
            "result",
            "pdr_page",
        ],
    )
    print(
        f"  a1 done ({time.time() - t0:.0f}s); PDR tables found for {len(tables)} PDs",
        flush=True,
    )


def step_a2(c):
    t0 = time.time()
    polys = np.concatenate([c.zg, c.og[np.isin(shapely.get_type_id(c.og), (3, 6))]])
    try:
        cover = shapely.coverage_union_all(c.zg)
        cover = shapely.union_all(
            [cover, *c.og[np.isin(shapely.get_type_id(c.og), (3, 6))]]
        )
    except Exception:  # noqa: BLE001  (zones not a clean coverage: fall back)
        cover = shapely.union_all(polys, grid_size=0.01)
    gaps = shapely.get_parts(shapely.difference(c.lpa, cover))
    gaps = gaps[np.isin(shapely.get_type_id(gaps), (3, 6))]
    areas = shapely.area(gaps)
    order = np.argsort(-areas)
    vp = c.village_polys()
    vkeys = list(vp)
    vtree = shapely.STRtree(np.array([vp[k][0] for k in vkeys], dtype=object))
    rows = []
    for rank, i in enumerate(order):
        g = gaps[i]
        pt = g.representative_point()
        lng, lat = TO_WGS(pt.x, pt.y)
        near = vtree.query_nearest(pt, all_matches=False)
        vk = vkeys[int(near[0])] if len(near) else None
        rows.append(
            {
                "rank": rank + 1,
                "area_m2": round(float(areas[i]), 1),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "pd": c.pd_of(g) or "",
                "nearest_village": "/".join(vk) if vk else "",
                "village_name": c.names.get(vk, {}).get("village", "") if vk else "",
                "width_m": round(2 * float(areas[i]) / max(g.length, 1e-9), 2),
                "on_lpa_edge": bool(g.distance(c.lpa.boundary) < 1.0),
            }
        )
    write_csv(os.path.join(c.out, "gaps.csv"), rows)
    tot = float(areas.sum())
    edge = np.array([r["on_lpa_edge"] for r in rows], bool)
    inner = np.array([r["area_m2"] for r in rows])[~edge] if len(rows) else np.array([])
    print(
        f"  a2: {len(rows)} gap pieces, total {tot / 1e4:.2f} ha "
        f"({100 * tot / c.lpa.area:.4f} % of LPA); >= 100 m2: {(areas >= 100).sum()} ({time.time() - t0:.0f}s)",
        flush=True,
    )
    with open(os.path.join(c.out, "gaps_summary.json"), "w") as f:
        json.dump(
            {
                "pieces": len(rows),
                "total_m2": tot,
                "lpa_m2": c.lpa.area,
                "pieces_ge_100m2": int((areas >= 100).sum()),
                "pieces_ge_1000m2": int((areas >= 1000).sum()),
                "area_ge_100m2": float(areas[areas >= 100].sum()),
                "edge_pieces": int(edge.sum()),
                "edge_m2": float(np.array([r["area_m2"] for r in rows])[edge].sum())
                if len(rows)
                else 0.0,
                "interior_pieces": len(inner),
                "interior_m2": float(inner.sum()),
            },
            f,
            indent=1,
        )


def step_a3(c):
    vp = c.village_polys()
    all_keys = set(c.names) | {k for k in c.auth if k[0] in DISTS}
    geo_missing, hob, no_parcels, errors = set(), {}, [], []
    for k in sorted(
        all_keys, key=lambda k: tuple(int(x) if x.isdigit() else 0 for x in k)
    ):
        f = parquet_path(c.args.cadastral_dir, k)
        has_file = os.path.exists(f)
        has_geom = k in vp
        if has_file and not has_geom:
            geo_missing.add(k)
        a = c.auth.get(k)
        cov = a["coverage"] if a else "(not in authority table)"
        nm = c.names.get(k, {})
        hk = (k[0], k[1], k[2])
        h = hob.setdefault(
            hk,
            {
                "dist": k[0],
                "taluk": k[1],
                "hobli": k[2],
                "taluk_name": nm.get("taluk", ""),
                "hobli_name": nm.get("hobli", ""),
                "villages": 0,
                "with_parcels": 0,
                "bda_full": 0,
                "bda_partial": 0,
                "bda_no_parcels": 0,
                "outside_bda": 0,
                "outside_bda_no_parcels": 0,
                "errors": 0,
            },
        )
        h["villages"] += 1
        h["with_parcels"] += has_geom
        if a is None:
            h["errors"] += 1
            errors.append(
                {
                    "key": "/".join(k),
                    "village": nm.get("village", ""),
                    "error": "village missing from authority_villages.csv",
                }
            )
        elif cov in ("full", "partial"):
            h[f"bda_{cov}"] += 1
            if not has_geom:
                h["bda_no_parcels"] += 1
                no_parcels.append(
                    {
                        "key": "/".join(k),
                        "village": nm.get("village", "") or a["village_name"],
                        "hobli": nm.get("hobli", ""),
                        "taluk": nm.get("taluk", ""),
                        "coverage": cov,
                        "coverage_source": a["source"],
                        "pd": a["pd"],
                        "reason": "parcel file has no geometry (placeholder)"
                        if has_file
                        else "no parcel file",
                    }
                )
        else:
            h["outside_bda"] += 1
            h["outside_bda_no_parcels"] += not has_geom
        if a is not None and k not in c.names:
            errors.append(
                {
                    "key": "/".join(k),
                    "village": a["village_name"],
                    "error": "in authority table but not in the e-Chawadi village list",
                }
            )
    write_csv(os.path.join(c.out, "admin_hobli.csv"), list(hob.values()))
    write_csv(os.path.join(c.out, "bda_villages_no_parcels.csv"), no_parcels)
    write_csv(
        os.path.join(c.out, "admin_errors.csv"), errors, ["key", "village", "error"]
    )


def step_a4(c):
    t0 = time.time()
    vp = c.village_polys()
    rows = []
    for k, a in c.auth.items():
        if a["coverage"] not in ("full", "partial"):
            continue
        base = {
            "key": "/".join(k),
            "village": a["village_name"],
            "coverage": a["coverage"],
            "text_pd": a["pd"],
        }
        if k not in vp:
            rows.append(base | {"status": "no parcel geometry"})
            continue
        g = shapely.intersection(vp[k][0], c.lpa)
        area = g.area
        if area <= 0:
            rows.append(base | {"status": "no area inside the LPA on the map"})
            continue
        by, unc, inf, _ = c.zone_areas(g)
        zone_area = sum(by.values())
        bad = 100 * (unc + inf) / area
        rows.append(
            base
            | {
                "status": "ok",
                "pd": c.pd_of(g) or "",
                "in_lpa_ha": round(area / 1e4, 2),
                "uncoloured_pct": round(100 * unc / area, 1),
                "inferred_pct": round(100 * inf / area, 1),
                "uncoloured_or_inferred_pct": round(bad, 1),
                "no_polygon_pct": round(100 * (1 - zone_area / area), 2),
                "flag_over_30": "YES" if bad > VILLAGE_FLAG_PCT else "",
                "top_zone": max(by, key=by.get) if by else "",
            }
        )
    write_csv(
        os.path.join(c.out, "village_quality.csv"),
        rows,
        [
            "key",
            "village",
            "coverage",
            "text_pd",
            "pd",
            "status",
            "in_lpa_ha",
            "uncoloured_pct",
            "inferred_pct",
            "uncoloured_or_inferred_pct",
            "no_polygon_pct",
            "flag_over_30",
            "top_zone",
        ],
    )
    print(f"  a4 done ({time.time() - t0:.0f}s)", flush=True)


# which script reads which document, from the code (checked 30 Sep 2026)
DOC_USE = {
    "BDA-RMP2031-PLUCOMP": (
        "layer",
        "zones, overlays, LPA boundary (extract_plucomp.py, georef_plucomp.py)",
    ),
    "BDA-RMP2031-MPD": (
        "layer",
        "LPA village schedule, Annexure 1 -> authority_villages.csv (build_authority.py)",
    ),
    "BDA-RMP2031-PDINDEX": (
        "layer",
        "village -> PD names -> authority_villages.csv pd column (build_authority.py)",
    ),
    "BDA-RMP2031-PDR": (
        "qa",
        "per-PD land-use tables (audit a1); figures via the PDR-PLU rows",
    ),
}


def step_a5(c):
    rows = []
    for r in read_csv(os.path.join(REG, "plan_docs.csv")):
        d = r["doc_id"]
        use, how = DOC_USE.get(d, ("not used yet", ""))
        if d.startswith(f"{PLAN_ID}-PDR-PLU-PD"):
            use, how = (
                "qa",
                "PDR figure cross-check and PD extent (crosscheck_pdr.py --all)",
            )
        elif d.startswith(f"{PLAN_ID}-ELU-PD") or d == f"{PLAN_ID}-ELU":
            how = "Existing Land Use (2015 base), not a 2031 layer"
        elif d == f"{PLAN_ID}-ZR":
            how = "Zoning Regulations: needed for /classify, which waits for Phase 1 sign-off"
        rows.append(
            {
                "doc_id": d,
                "type": r["type"],
                "status": r["status"],
                "use": use,
                "how": how,
                "title": r["title"],
            }
        )
    elu = {
        int(m.group(1)) for r in rows if (m := re.search(r"-ELU-PD(\d+)$", r["doc_id"]))
    }
    plu = {
        int(m.group(1))
        for r in rows
        if (m := re.search(r"-PDR-PLU-PD(\d+)$", r["doc_id"]))
    }
    rows.append(
        {
            "doc_id": "(check) PD coverage",
            "use": "",
            "how": f"ELU sheets for {len(elu)} PDs, missing {sorted(set(range(1, 43)) - elu)}; "
            f"PDR PLU figures for {len(plu)} PDs, missing {sorted(set(range(1, 43)) - plu)}; "
            f"PD extents registered: {sorted(c.pds)} (missing {sorted(set(range(1, 43)) - set(c.pds))})",
        }
    )
    write_csv(
        os.path.join(c.out, "documents.csv"),
        rows,
        ["doc_id", "type", "status", "use", "how", "title"],
    )


def step_c9(c):
    t0 = time.time()
    geo = read_json(os.path.join(c.args.data_root, "georef", f"{PLAN_ID}-PLUCOMP.json"))
    A = np.array(geo["affine_page_to_32643"])
    doc = pymupdf.open(
        os.path.join(c.args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-PLUCOMP.pdf")
    )
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    S = plucomp_roads(doc[0])
    Jp = junctions_from_segments(S, snap=0.5)
    lines, Jo = osm_lines_and_junctions(
        osm(c.args.data_root, "major") + osm(c.args.data_root, "sec"), tr
    )
    ltree = shapely.STRtree(lines)
    Jg = apply_affine(A, Jp)
    iso_p, iso_o = (
        isolated(Jg, JUNCTION_ISOLATION_M),
        isolated(Jo, JUNCTION_ISOLATION_M),
    )
    otree = shapely.STRtree(shapely.points(Jo))
    ptree = shapely.STRtree(shapely.points(Jg))
    (pi, oj), d = otree.query_nearest(
        shapely.points(Jg), return_distance=True, all_matches=False
    )
    (_, pi2), _ = ptree.query_nearest(
        shapely.points(Jo[oj]), return_distance=True, all_matches=False
    )
    mutual = pi2 == pi
    keep = (d < JUNCTION_PAIR_M) & iso_p[pi] & iso_o[oj] & mutual
    P, G = Jg[pi[keep]], Jo[oj[keep]]
    R = P - G
    r = np.hypot(*R.T)
    _, d_line = ltree.query_nearest(
        shapely.points(P), return_distance=True, all_matches=False
    )
    dropped = (r > MISMATCH_RESIDUAL_M) & (d_line < MISMATCH_ON_ROAD_M)
    pts = []
    for i in range(len(P)):
        pt = shapely.Point(P[i])
        pd = next((p for p, g in c.pds.items() if g.contains(pt)), None)
        lng, lat = TO_WGS(*G[i])
        pts.append(
            {
                "pd": pd or "",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "residual_m": round(float(r[i]), 1),
                "de_m": round(float(R[i, 0]), 1),
                "dn_m": round(float(R[i, 1]), 1),
                "dropped_junction_mismatch": bool(dropped[i]),
            }
        )
    write_csv(os.path.join(c.out, "georef_points.csv"), pts)
    rows = []
    for pd in range(1, 43):
        rr = np.array(
            [
                p["residual_m"]
                for p in pts
                if p["pd"] == pd and not p["dropped_junction_mismatch"]
            ]
        )
        nd = sum(1 for p in pts if p["pd"] == pd and p["dropped_junction_mismatch"])
        if not len(rr):
            rows.append({"pd": pd, "n": 0, "dropped": nd, "flag": "no check points"})
            continue
        rmse = float(np.sqrt(np.mean(rr**2)))
        flags = []
        if rmse > GEOREF_FLAG_M:
            flags.append(f"RMSE > {GEOREF_FLAG_M:.0f} m")
        if len(rr) < 3:
            flags.append("fewer than 3 points")
        rows.append(
            {
                "pd": pd,
                "n": len(rr),
                "dropped": nd,
                "rmse_m": round(rmse, 1),
                "median_m": round(float(np.median(rr)), 1),
                "max_m": round(float(rr.max()), 1),
                "flag": "; ".join(flags),
            }
        )
    allr = np.array(
        [p["residual_m"] for p in pts if not p["dropped_junction_mismatch"]]
    )
    rows.append(
        {
            "pd": "ALL",
            "n": len(allr),
            "dropped": int(dropped.sum()),
            "rmse_m": round(float(np.sqrt(np.mean(allr**2))), 1),
            "median_m": round(float(np.median(allr)), 1),
            "max_m": round(float(allr.max()), 1),
            "flag": "",
        }
    )
    write_csv(
        os.path.join(c.out, "georef_pd.csv"),
        rows,
        ["pd", "n", "dropped", "rmse_m", "median_m", "max_m", "flag"],
    )
    print(
        f"  c9: {len(P)} pairs, {dropped.sum()} dropped ({time.time() - t0:.0f}s)",
        flush=True,
    )


def step_c10(c):
    t0 = time.time()
    meta = read_json(os.path.join(c.zdir, f"{PLAN_ID}_classes.json"))
    A = np.array(meta["affine_page_to_32643"])
    Ai = np.linalg.inv(np.vstack([A.T, [0, 0, 1]]))
    doc = pymupdf.open(
        os.path.join(c.args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-PLUCOMP.pdf")
    )
    img, (gx0, gy0, cpt, rpt) = load_mosaic(doc[0], doc)
    colour_code, zones, _ = load_legend()
    code_label = {v: k for k, v in zones.items()}
    hexcode = {int(h[1:], 16): code for h, code in colour_code.items()}
    WHITE = 0xFFFFFF

    def source_at(x, y):
        px, py = (np.array([x, y, 1.0]) @ Ai.T)[:2]
        col, row = int((px - gx0) / cpt), int((py - gy0) / rpt)
        if not (0 <= row < img.shape[0] and 0 <= col < img.shape[1]):
            return "(off sheet)", ""
        v = int(img[row, col])
        win = img[max(row - 2, 0) : row + 3, max(col - 2, 0) : col + 3].ravel()
        if v == WHITE:
            lab = "Not coloured on the plan"
        elif v in hexcode:
            code = hexcode[v]
            lab = {2: "(NGT hatch)", 3: "(forest glyph)"}.get(
                code, code_label.get(code, str(code))
            )
        else:
            lab = f"(other colour #{v:06x})"
        labs = []
        for w in win.tolist():
            if w == WHITE:
                labs.append("Not coloured on the plan")
            elif w in hexcode:
                labs.append(
                    {2: "(NGT hatch)", 3: "(forest glyph)"}.get(
                        hexcode[w], code_label.get(hexcode[w], "")
                    )
                )
        maj = max(set(labs), key=labs.count) if labs else ""
        return lab, maj

    vp = c.village_polys()
    rnd = random.Random(SEED)
    rows, picks = [], {}
    bda = [
        k for k, a in c.auth.items() if a["coverage"] in ("full", "partial") and k in vp
    ]
    vk = np.array(bda, dtype=object)
    vtree = shapely.STRtree(np.array([vp[k][0] for k in bda], dtype=object))
    for pd in range(1, 43):
        g = c.pds.get(pd)
        if g is None:
            rows.append({"pd": pd, "result": "no PD extent"})
            continue
        cands = [tuple(x) for x in vk[vtree.query(g, predicate="intersects")]]
        rnd.shuffle(cands)
        got = 0
        for k in cands:
            if got >= SAMPLE_PER_PD:
                break
            p = read_parcels(c.args.cadastral_dir, k)
            if p is None:
                continue
            sv, geoms = p
            cnt = {}
            for s in sv:
                cnt[s] = cnt.get(s, 0) + 1
            idx = [
                i
                for i, s in enumerate(sv)
                if s and cnt[s] == 1 and 300 < geoms[i].area < 50000
            ]
            rnd.shuffle(idx)
            for i in idx:
                cen = shapely.centroid(geoms[i])
                if not (g.contains(cen) and c.lpa.contains(cen)):
                    continue
                zi = c.ztree.query(cen, predicate="intersects")
                ours = c.zlab[zi[0]] if len(zi) else "(no polygon)"
                note = c.znote[zi[0]] if len(zi) else ""
                src, maj = source_at(cen.x, cen.y)
                if src == ours:
                    res = "match"
                elif note and src in ("(NGT hatch)", "(forest glyph)", "Streams"):
                    res = "source shows a map symbol; ours inferred"
                elif src.startswith("(other colour") and maj == ours:
                    res = "match (centre pixel on a line/label; 5x5 majority agrees)"
                elif src.startswith("(other colour"):
                    res = "source pixel is a line/label colour"
                else:
                    res = "MISMATCH"
                lng, lat = TO_WGS(cen.x, cen.y)
                name = f"pd{pd:02d}_{got + 1}"
                rows.append(
                    {
                        "pd": pd,
                        "sample": name,
                        "key": "/".join(k),
                        "survey": sv[i],
                        "village": c.names.get(k, {}).get("village", ""),
                        "lat": round(lat, 6),
                        "lng": round(lng, 6),
                        "ours": ours,
                        "ours_inferred_note": note,
                        "source_pixel": src,
                        "source_5x5_majority": maj,
                        "result": res,
                    }
                )
                if res == "MISMATCH":
                    picks[name] = {
                        "dist": k[0],
                        "taluk": k[1],
                        "hobli": k[2],
                        "vlg": k[3],
                        "survey": sv[i],
                        "village": c.names.get(k, {}).get("village", ""),
                        "coverage": c.auth[k]["coverage"],
                    }
                got += 1
                break  # one parcel per village keeps the sample spread out
        if got < SAMPLE_PER_PD:
            rows.append(
                {"pd": pd, "result": f"only {got} parcels found inside this PD"}
            )
    write_csv(
        os.path.join(c.out, "sample_parcels.csv"),
        rows,
        [
            "pd",
            "sample",
            "key",
            "survey",
            "village",
            "lat",
            "lng",
            "ours",
            "ours_inferred_note",
            "source_pixel",
            "source_5x5_majority",
            "result",
        ],
    )
    with open(os.path.join(c.out, "sample_mismatch_picks.json"), "w") as f:
        json.dump(picks, f, indent=1)
    n = sum(1 for r in rows if r.get("sample"))
    mm = sum(1 for r in rows if r.get("result") == "MISMATCH")
    print(f"  c10: {n} samples, {mm} mismatches ({time.time() - t0:.0f}s)", flush=True)


def step_summary(c):
    """District > taluk roll-up from a3/a4 and, when present, the B6/B7 CSVs."""
    hob = read_csv(os.path.join(c.out, "admin_hobli.csv"))
    vq = read_csv(os.path.join(c.out, "village_quality.csv"))
    b6 = os.path.join(c.out, "zones_at_villages.csv")
    b6 = read_csv(b6) if os.path.exists(b6) else []
    pd_fail = set()
    p = os.path.join(c.out, "pd_vs_pdr.csv")
    if os.path.exists(p):
        pd_fail = {r["pd"] for r in read_csv(p) if r["result"] == "FAIL"}
    gp = os.path.join(c.out, "georef_pd.csv")
    geo_flag = (
        {r["pd"] for r in read_csv(gp) if r["flag"]} if os.path.exists(gp) else set()
    )
    out = {}
    for h in hob:
        k = (h["dist"], h["taluk"])
        o = out.setdefault(
            k,
            {
                "dist": h["dist"],
                "taluk": h["taluk"],
                "taluk_name": h["taluk_name"],
                "villages": 0,
                "with_parcels": 0,
                "bda_villages": 0,
                "bda_no_parcels": 0,
                "outside_bda": 0,
                "admin_errors": 0,
                "villages_flag_30": 0,
                "parcels_run": 0,
                "parcel_errors": 0,
                "parcels_empty": 0,
                "parcels_sum_off": 0,
                "pds": set(),
            },
        )
        for f in ("villages", "with_parcels", "bda_no_parcels", "outside_bda"):
            o[f] += int(h[f])
        o["bda_villages"] += int(h["bda_full"]) + int(h["bda_partial"])
        o["admin_errors"] += int(h["errors"])
    for r in vq:
        k = tuple(r["key"].split("/")[:2])
        if k in out:
            out[k]["villages_flag_30"] += r["flag_over_30"] == "YES"
            if r.get("pd"):
                out[k]["pds"].add(r["pd"])
    for r in b6:
        k = tuple(r["key"].split("/")[:2])
        if k in out:
            for f in (
                "parcels_run",
                "parcel_errors",
                "parcels_empty",
                "parcels_sum_off",
            ):
                out[k][f] += int(r.get(f) or 0)
    rows = []
    for o in out.values():
        pds = sorted(o.pop("pds"), key=int)
        o["searchable_bda_pct"] = (
            round(
                100 * (o["bda_villages"] - o["bda_no_parcels"]) / o["bda_villages"], 1
            )
            if o["bda_villages"]
            else ""
        )
        o["pds"] = " ".join(pds)
        o["pds_failing_pdr"] = " ".join(p for p in pds if p in pd_fail)
        o["pds_georef_flag"] = " ".join(p for p in pds if p in geo_flag)
        rows.append(o)
    write_csv(os.path.join(c.out, "summary_taluk.csv"), rows)


STEPS = {
    "villages": step_villages,
    "a1": step_a1,
    "a2": step_a2,
    "a3": step_a3,
    "a4": step_a4,
    "a5": step_a5,
    "c9": step_c9,
    "c10": step_c10,
    "summary": step_summary,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--data-root", default=os.getenv("PLANNING_DATA_ROOT"), required=False
    )
    ap.add_argument("--cadastral-dir", required=True)
    ap.add_argument("--steps", nargs="+", default=list(STEPS))
    args = ap.parse_args()
    c = Ctx(args)
    for s in args.steps:
        print(f"[{s}]", flush=True)
        STEPS[s](c)


if __name__ == "__main__":
    main()
