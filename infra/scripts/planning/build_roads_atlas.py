# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan roads from an atlas whose sheets are already georeferenced in the layer index (zone
rows with a `sheet_grid_labels` grid fit): first the Hoskote LPA Master Plan atlas
(open-decisions #79).

  python build_roads_atlas.py --plan BMRDA-HSK-MP2031 [--geojson DIR] [--publish]
                              [--spotcheck N] [--pages 53,34]

The Hoskote atlas (Master_Plans.pdf) draws, on its 1:10,000 and 1:5,000 detail sheets, the
road ROW edges as red vector strokes to scale and the width as bold numbers in circles;
existing roads are grey bands in the raster base. Hobli maps (1:15,000 and smaller) draw
narrow roads as fixed symbols, so there only the labels count. Each sheet is extracted in a
capped worker (roads_mob.py with the plan's style), placed with its row's grid fit (the
zones' georeference), clipped to its own extent with detail sheets over hobli maps, then
attributed and checked like Anekal (build_roads.py). Text summary to layer_index.json
("roads"); --publish uploads the gzipped GeoJSON next to the map tiles.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

import build_roads as br
import numpy as np
import pymupdf
import qnit_fetch as qf
import shapely
import winjob
from shapely.geometry import LineString, Point

PLANS = {
    "BMRDA-HSK-MP2031": {
        "doc_id": "BMRDA-HSK-MP2031-MP",
        "zr_doc": "BMRDA-HSK-MP2031-ZRR",
        # hobli "Proposed Circulation" sheets (1:15,000-1:35,000, UTM grid values in the text):
        # denser width labels than the hobli land-use maps; not zone rows, fitted here
        "circulation_pages": [82, 83, 84, 85, 86, 87],
        "style": {
            "grey": 152,
            "grey_tol": 9,
            "red": {"vector": [["#ff0000", 0.72], ["#ff0000", 0.96]]},
            "label": {
                "bold_min_size": 7.5,
                "values": [9, 12, 15, 18, 24, 30, 35, 45, 60, 80, 90],
            },
            "rings": False,
            "wmax": 95.0,
        },
        # MP Report Table 76 (PDF p. 272, printed p. 239): planned road network, km by ROW
        "report_km": {
            "9": 0.98,
            "12": 76.49,
            "15": 6.25,
            "18": 217.53,
            "24": 205.17,
            "30": 84.92,
            "45": 45.76,
            "60": 15.39,
            "80": 16.76,
            "90": 66.50,
        },
    }
}
DETAIL_MAX_SCALE = 10000  # sheets at 1:10,000 or larger draw ROW edges to scale


def log(m: str) -> None:
    print(m, flush=True)


def sheet_rows(plan_id: str, doc_id: str) -> list[dict]:
    with open(br.INDEX, encoding="utf-8") as f:
        ix = json.load(f)
    rows = [
        r
        for r in ix["rows"]
        if r["plan_id"] == plan_id
        and r["kind"] == "zones"
        and r.get("status") == "indexed"
        and r["doc_id"] == doc_id
        and (r.get("georef") or {}).get("grid_fit")
    ]
    return sorted(rows, key=lambda r: r["page"])


def fit_fn(row):
    e, n = row["georef"]["grid_fit"]["E"], row["georef"]["grid_fit"]["N"]

    def f(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return e[0] * x + e[1], n[0] * y + n[1]

    def inv(ex, ny):
        return (ex - e[1]) / e[0], (ny - n[1]) / n[0]

    return f, inv


def page_box(row, page_rect) -> list[float]:
    """The sheet's extent in page points (inverse grid fit), on the page, padded."""
    _f, inv = fit_fn(row)
    x0, y0, x1, y1 = row["extent"]["epsg32643"]
    xa, ya = inv(x0, y1)
    xb, yb = inv(x1, y0)
    return [
        max(page_rect.x0, min(xa, xb) - 20),
        max(page_rect.y0, min(ya, yb) - 20),
        min(page_rect.x1, max(xa, xb) + 20),
        min(page_rect.y1, max(ya, yb) + 20),
    ]


def run_worker(path, page, box, style):
    job = {"source_path": path, "page": page, "box": box, "style": style}
    rc, out, err, peak = winjob.run_capped(
        [sys.executable, "-X", "faulthandler", os.path.join(br.HERE, "roads_mob.py")],
        json.dumps(job).encode(),
        br.WORKER_CAP,
        cwd=br.HERE,
    )
    if rc != 0:
        raise RuntimeError(
            f"roads worker page {page + 1}: rc {rc}: {err.decode(errors='replace')[-800:]}"
        )
    return json.loads(out), peak


def circulation_rows(doc, pages) -> list[dict]:
    """Rows for the circulation sheets: grid fit from the UTM values in their text, extent
    from the span of those values, scale from the title."""
    import re

    import extract_hoskote as xh

    out = []
    for p in pages:
        page = doc[p - 1]
        fit = xh.grid_fit(page)
        if fit["E"][3] < 3 or fit["N"][3] < 3 or fit["E"][2] > 15 or fit["N"][2] > 15:
            log(f"  circulation p{p}: grid fit rejected {fit}")
            continue
        t = " ".join(page.get_text().split())
        sc = re.findall(r"1\s*:\s*([\d,]{4,})", t)
        scale = int(sc[0].replace(",", "")) if sc else 30000
        es = [
            int(w[4])
            for w in page.get_text("words")
            if re.fullmatch(r"\d{6}", w[4]) and 700000 < int(w[4]) < 900000
        ]
        ns = [
            int(w[4])
            for w in page.get_text("words")
            if re.fullmatch(r"\d{7}", w[4]) and 1400000 < int(w[4]) < 1500000
        ]
        out.append(
            {
                "page": p,
                "sheet": f"Proposed Circulation (atlas p. {p}, 1:{scale:,})",
                "extraction": {"scale": scale},
                "georef": {"grid_fit": {"E": list(fit["E"]), "N": list(fit["N"])}},
                "extent": {"epsg32643": [min(es), min(ns), max(es), max(ns)]},
                "road_priority": 50000,
            }
        )
        log(
            f"  circulation p{p}: 1:{scale}, grid residual E {fit['E'][2]:.1f} N {fit['N'][2]:.1f} (pt->m)"
        )
    return out


def priority(r) -> float:
    """Lower wins: detail sheets by scale (1:5,000 first), then circulation sheets, then the
    hobli land-use maps."""
    sc = r["extraction"].get("scale") or 15000
    if sc <= DETAIL_MAX_SCALE:
        return sc
    return r.get("road_priority", 60000)


def level(r) -> int:
    """0: detail sheets (edges to scale), 1: circulation sheets, 2: hobli land-use maps."""
    p = priority(r)
    return 0 if p <= DETAIL_MAX_SCALE else (1 if p < 60000 else 2)


def footprints(rows) -> dict[int, shapely.Geometry]:
    """Each sheet's own area. Detail sheets: their extent minus the extents of detail sheets
    at a larger scale. Smaller-scale sheets keep their whole extent; their roads are later
    kept only where no higher-level road lies within DEDUP_M (detail frames are larger than
    the roads their labels reach)."""
    ext = {r["page"]: shapely.box(*r["extent"]["epsg32643"]) for r in rows}
    sc = {r["page"]: priority(r) for r in rows}
    lv = {r["page"]: level(r) for r in rows}
    out = {}
    for p, g in ext.items():
        better = [
            ext[q] for q in ext if q != p and lv[q] == lv[p] and (sc[q], q) < (sc[p], p)
        ]
        out[p] = g.difference(shapely.union_all(better)) if better else g
    return out


DEDUP_M = 25.0


def dedupe(feats, rows):
    """Roads from lower-level sheets only where no higher-level road lies within DEDUP_M."""
    lv = {r["page"]: level(r) for r in rows}
    out = [f_ for f_ in feats if lv[f_["page"]] == 0]
    for lvl in (1, 2):
        cover = (
            shapely.union_all([f_["geom"].buffer(DEDUP_M) for f_ in out])
            if out
            else None
        )
        for f_ in feats:
            if lv[f_["page"]] != lvl:
                continue
            g = f_["geom"].difference(cover) if cover is not None else f_["geom"]
            parts = [
                x
                for x in getattr(g, "geoms", [g])
                if x.geom_type == "LineString" and x.length >= 20
            ]
            out.extend({**f_, "geom": x} for x in parts)
    return out


SNAP_M = 1.5
cfg_std = (9, 12, 15, 18, 24, 30, 45, 60, 80, 90)  # widths the Hoskote plan labels


def assemble(rows, results, foot):
    feats, labels_all, per_sheet = [], [], []
    small_low_km = 0.0
    byp = {r["page"]: r for r in rows}
    for page_no, res in results.items():
        row = byp[page_no + 1]
        f, _inv = fit_fn(row)
        cell = foot[row["page"]]
        detail = (row["extraction"].get("scale") or 15000) <= DETAIL_MAX_SCALE
        labels = res["labels"]
        for lb in labels:
            x, y = f(lb["x"], lb["y"])
            if cell.contains(Point(float(x), float(y))):
                labels_all.append(
                    {**lb, "e": float(x), "n": float(y), "page": row["page"]}
                )
        km = 0.0
        for ed in res["edges"]:
            c = np.asarray(ed["pt"])
            x, y = f(c[:, 0], c[:, 1])
            line = LineString(np.column_stack([x, y])).intersection(cell)
            if line.is_empty or line.length < 3:
                continue
            lb = labels[ed["label_id"]] if ed["label_id"] is not None else None
            band_m, band_conf = br.drawn_band(ed) if detail else (None, "LOW")
            red_frac = (ed.get("stations_red") or 0) / max(1, ed.get("stations") or 1)
            if lb is not None and ed["w"] is None and red_frac < 0.3:
                status = "existing_row_stated"
            elif lb is not None or ed["w"] is not None:
                status = "to_be_widened" if ed["grey"] >= 0.5 else "proposed"
            else:
                status = "existing_drawn"
            if lb is not None and ed["w"] is not None:
                source, conf = "label_and_drawn", "HIGH"
            elif lb is not None:
                source = "label"
                conf = (
                    "HIGH" if ed["label_path_m"] <= br.LABEL_PATH_HIGH_M else "MEDIUM"
                )
            elif ed["w"] is not None:
                source, conf = "drawn", "MEDIUM"
            else:
                source, conf = "drawn_band", band_conf
            if status == "existing_drawn":
                if band_conf != "MEDIUM":
                    small_low_km += line.length / 1000
                    continue
                row_m = band_m
            elif lb is None and (ed["w"] < 6.0 or ed["w"] > 36.0 or ed["len_m"] < 40.0):
                continue  # drawn-only: noise, or a wide gap between two roads
            elif lb is not None:
                row_m = float(ed["label"])
            else:
                # unlabelled, edges drawn to scale (+-1 m against labels): the plan's standard
                # width when the measurement is within SNAP_M of one, else the measurement
                std = min(cfg_std, key=lambda v: abs(v - ed["w"]))
                row_m = (
                    float(std)
                    if abs(std - ed["w"]) <= SNAP_M
                    else round(ed["w"] * 2) / 2
                )
            feats.append(
                {
                    "geom": line,
                    "row_m": row_m,
                    "status": status,
                    "width_source": source,
                    "confidence": conf,
                    "drawn_row_m": round(ed["w"], 1) if ed["w"] is not None else None,
                    "drawn_band_m": band_m if band_conf == "MEDIUM" else None,
                    "label_m": ed["label"],
                    "label_path_m": ed["label_path_m"],
                    "page": row["page"],
                }
            )
            km += line.length / 1000
        per_sheet.append(
            {
                "page": row["page"],
                "sheet": row["sheet"],
                "scale": row["extraction"].get("scale"),
                "edges_km": round(km, 2),
                "labels": len(labels),
                "worker_s": res["secs"],
            }
        )
    return feats, labels_all, per_sheet, round(small_low_km, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="BMRDA-HSK-MP2031", choices=sorted(PLANS))
    ap.add_argument("--geojson", default=None)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--spotcheck", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument(
        "--local", default=None, help="a local copy of the source (sha256 checked)"
    )
    ap.add_argument(
        "--pages", default=None, help="1-based pages (test run: no index write)"
    )
    args = ap.parse_args()
    cfg = PLANS[args.plan]
    t0 = time.time()
    src = br.doc_row(cfg["doc_id"])
    with qf.TempArea(f"build/roads_{args.plan}") as area:
        if args.local:
            import hashlib

            h = hashlib.sha256()
            with open(args.local, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != src["sha256"]:
                raise SystemExit("--local: sha256 does not match plan_docs.csv")
            path = args.local
        else:
            path, _ = qf.download(src["source_url"], area, "atlas.pdf", src["sha256"])
        doc = pymupdf.open(path)
        all_rows = sheet_rows(args.plan, cfg["doc_id"]) + circulation_rows(
            doc, cfg.get("circulation_pages", [])
        )
        foot = footprints(all_rows)
        rows = all_rows
        if args.pages:
            want = {int(p) for p in args.pages.split(",")}
            rows = [r for r in rows if r["page"] in want]
        log(f"{args.plan}: {len(rows)} georeferenced sheets")
        jobs = []
        for r in rows:
            scale = r["extraction"].get("scale") or 15000
            style = {
                **cfg["style"],
                "scale": scale,
                "edges_to_scale": scale <= DETAIL_MAX_SCALE,
                "keep_small": scale <= DETAIL_MAX_SCALE,
                # small-scale sheets label long rural roads once: carry further (MEDIUM > 200 m)
                "prop_max_m": 600.0 if scale <= DETAIL_MAX_SCALE else 1500.0,
                "label": {
                    **cfg["style"]["label"],
                    "bold_min_size": 7.5 if scale <= DETAIL_MAX_SCALE else 5.0,
                },
            }
            jobs.append((r["page"] - 1, page_box(r, doc[r["page"] - 1].rect), style))
        results, peaks = {}, []
        with cf.ThreadPoolExecutor(args.workers) as ex:
            futs = {ex.submit(run_worker, path, p, b, s): p for p, b, s in jobs}
            for fu in cf.as_completed(futs):
                res, peak = fu.result()
                results[futs[fu]] = res
                peaks.append(peak)
                log(
                    f"  page {futs[fu] + 1}: {len(res['edges'])} edges, {len(res['labels'])} labels, {res['secs']} s, peak {peak / 1e6:.0f} MB"
                )
        feats, labels, per_sheet, small_low_km = assemble(rows, results, foot)
        feats = dedupe(feats, rows)
        merged = br.merge_features(feats)
        km = lambda fs: round(sum(f_["geom"].length for f_ in fs) / 1000, 1)
        by_row, by_status, by_conf = {}, {}, {}
        for f_ in merged:
            L = f_["geom"].length / 1000
            by_row[f_["row_m"]] = by_row.get(f_["row_m"], 0.0) + L
            by_status[f_["status"]] = by_status.get(f_["status"], 0.0) + L
            by_conf[f_["confidence"]] = by_conf.get(f_["confidence"], 0.0) + L
        diffs = [
            f_["drawn_row_m"] - f_["label_m"]
            for f_ in feats
            if f_["drawn_row_m"] is not None and f_["label_m"]
        ]
        label_check = {
            "edges": len(diffs),
            "drawn_minus_label_m": {
                q: round(float(np.percentile(diffs, p)), 2)
                for q, p in (("p10", 10), ("median", 50), ("p90", 90))
            }
            if diffs
            else None,
            "within_1m": round(float(np.mean(np.abs(diffs) <= 1.0)), 3)
            if diffs
            else None,
            "within_2m": round(float(np.mean(np.abs(diffs) <= 2.0)), 3)
            if diffs
            else None,
        }
        labelled_km = {}
        for f_ in merged:
            if f_["status"] != "existing_drawn":
                k = f"{f_['row_m']:g}"
                labelled_km[k] = labelled_km.get(k, 0.0) + f_["geom"].length / 1000
        report = {
            k: {"report_km": v, "extracted_km": round(labelled_km.get(k, 0.0), 1)}
            for k, v in cfg["report_km"].items()
        }
        small = [f_ for f_ in merged if f_["status"] == "existing_drawn"]
        qa = {
            "label_vs_drawn": label_check,
            "drawn_band_calibration": br.band_calibration(merged),
            "vs_master_plan_report_km": report,
            "km_total": km(merged),
            "km_by_row_m": {f"{k:g}": round(v, 1) for k, v in sorted(by_row.items())},
            "km_by_status": {k: round(v, 1) for k, v in by_status.items()},
            "km_by_confidence": {k: round(v, 1) for k, v in by_conf.items()},
            "small_roads": {
                "km_with_width": km(small),
                "km_left_out_low": small_low_km,
            },
            "labels_in_sheets": len(labels),
            "worker_peak_mb": round(max(peaks) / 1e6) if peaks else None,
        }
        log(json.dumps(qa, indent=1)[:5000])
        published = None
        if args.geojson:
            os.makedirs(args.geojson, exist_ok=True)
            out = os.path.join(args.geojson, f"{args.plan}.roads.geojson")
            fc = br.geojson_features(merged, [], labels)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(fc, fh, separators=(",", ":"))
            log(f"  geojson: {len(fc['features'])} features")
            if args.spotcheck:
                spot(
                    doc,
                    results,
                    rows,
                    args.spotcheck,
                    os.path.join(args.geojson, f"{args.plan}_spotcheck.png"),
                )
            if args.publish and not args.pages:
                published = br.publish(
                    out,
                    len(fc["features"]),
                    src,
                    plan_id=args.plan,
                    doc_id=cfg["doc_id"],
                )
        doc.close()
    if args.pages:
        log("test run (--pages): index not written")
        return
    with open(br.INDEX, encoding="utf-8") as fh:
        ix = json.load(fh)
    ix.setdefault("roads", {})[args.plan] = {
        "doc_id": cfg["doc_id"],
        "source_url": src["source_url"],
        "sha256": src["sha256"],
        "status": src["status"],
        "status_label": src["status_label"],
        "georef": {
            "method": "sheet_grid_labels (the zone rows' grid fit, per sheet)",
            "sheets": len(rows),
        },
        "extraction": {
            "method": "road_network_sheet",
            "script": "infra/scripts/planning/roads_mob.py",
            "style": cfg["style"],
            "detail_max_scale": DETAIL_MAX_SCALE,
            "priority": "1:5,000 over 1:10,000 over hobli maps; each sheet clipped to its own extent",
        },
        "published": published,
        "sheets": sorted(per_sheet, key=lambda r: r["page"]),
        "qa": qa,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    with open(br.INDEX, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ix, fh, indent=1, sort_keys=False)
        fh.write("\n")
    log(f"done in {time.time() - t0:.0f} s")


def spot(doc, results, rows, n, out_png):
    """n random labelled pieces on detail sheets: source crop with centreline and corridor."""
    from PIL import Image, ImageDraw

    byp = {r["page"]: r for r in rows}
    rng = np.random.default_rng(2031)
    cands = [
        (p, i)
        for p, res in results.items()
        if (byp[p + 1]["extraction"].get("scale") or 15000) <= DETAIL_MAX_SCALE
        for i, ed in enumerate(res["edges"])
        if ed["label"] and ed["len_m"] > 40
    ]
    if not cands:
        return
    pick = [cands[k] for k in rng.choice(len(cands), min(n, len(cands)), replace=False)]
    tiles = []
    for p, i in pick:
        ed = results[p]["edges"][i]
        scale = byp[p + 1]["extraction"].get("scale") or 10000
        mpt = scale * 0.0254 / 72
        c = np.asarray(ed["pt"])
        cx, cy = c[len(c) // 2]
        r = 140.0 / mpt  # 280 m wide crop
        clip = pymupdf.Rect(cx - r, cy - r, cx + r, cy + r)
        pm = doc[p].get_pixmap(dpi=150, clip=clip, alpha=False)
        im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
        dr = ImageDraw.Draw(im)
        k = pm.width / (2 * r)
        xy = [((x - clip.x0) * k, (y - clip.y0) * k) for x, y in c]
        dr.line(xy, fill=(0, 90, 255), width=2)
        half = ed["label"] / 2 / mpt * k
        for side in (half, -half):
            try:
                dr.line(
                    list(LineString(xy).offset_curve(side).coords),
                    fill=(0, 200, 0),
                    width=1,
                )
            except Exception as ex:  # noqa: BLE001 - odd curve: no edge lines
                log(f"  spotcheck: {ex}")
        dr.text(
            (3, 3),
            f"p{p + 1} {ed['label']}m drawn {ed['w'] and round(ed['w'], 1)}",
            fill=(255, 0, 255),
        )
        tiles.append(im.resize((300, 300)))
    sheet = Image.new("RGB", (5 * 300, ((len(tiles) + 4) // 5) * 300), "white")
    for j, t in enumerate(tiles):
        sheet.paste(t, ((j % 5) * 300, (j // 5) * 300))
    sheet.save(out_png)


if __name__ == "__main__":
    main()
