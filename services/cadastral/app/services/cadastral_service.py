# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Core cadastral data access: parcel parquets + SQLite survey index.

Source parquets (cadastral_lake_v2) store EPSG:32643 with X/Y swapped — a known upstream
scraper bug. load_village() compensates via affine transform at read time. Do not read the
parquets directly without applying the same fix.

Data layout: CADASTRAL_DATA_DIR/dist_<d>/taluk_<t>/hobli_<h>/vlg_<v>.parquet
SQLite (optional): SURVEY_INDEX_DB  (survey_index table — built once at startup in background)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import threading
from typing import Any

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("CADASTRAL_DATA_DIR", "data/cadastral_lake_v2")
_INDEX_DB = os.environ.get("SURVEY_INDEX_DB", "/app/survey_index.db")

# Swap X↔Y: compensates for scraper building Polygon(Northing, Easting) instead of
# Polygon(Easting, Northing) in EPSG:32643.
_SWAP_XY = [0, 1, 1, 0, 0, 0]

# CADASTRAL_DATUM controls source ellipsoid assumption for reprojection.
# Options: wgs84 (default), everest (Everest 1830 ellipsoid only),
#          kalianpur (Everest ellipsoid + full 3-param Bursa-Wolf shift to WGS84).
# Everest confirmed better alignment than wgs84 for pre-2000 Karnataka cadastral data.
# Kalianpur adds ~59 m N / ~108 m W geocentric origin correction on top of everest.
_DATUM = os.environ.get("CADASTRAL_DATUM", "wgs84").lower()
_EVEREST_UTM43N = "+proj=utm +zone=43 +a=6377276.345 +b=6356075.413 +units=m +no_defs"
_KALIANPUR_UTM43N = "+proj=utm +zone=43 +a=6377309.613 +b=6356108.571 +towgs84=295,736,257,0,0,0,0 +units=m +no_defs"


def load_village(path: str) -> gpd.GeoDataFrame | None:
    """Read one vlg_*.parquet, fix swapped axes, reproject to WGS84. None if placeholder."""
    try:
        gdf = gpd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if gdf.empty or "geometry" not in gdf.columns:
        return None
    gdf["geometry"] = gdf.geometry.affine_transform(_SWAP_XY)
    if _DATUM == "everest":
        src_crs = _EVEREST_UTM43N
    elif _DATUM == "kalianpur":
        src_crs = _KALIANPUR_UTM43N
    else:
        src_crs = 32643
    gdf = gdf.set_crs(src_crs, allow_override=True).to_crs(4326)
    return gdf


def find_paths(
    dist: str | None = None,
    taluk: str | None = None,
    hobli: str | None = None,
    vlg: str | None = None,
) -> list[str]:
    dist_part = f"dist_{dist}" if dist else "dist_*"
    taluk_part = f"taluk_{taluk}" if taluk else "taluk_*"
    hobli_part = f"hobli_{hobli}" if hobli else "hobli_*"
    vlg_part = f"vlg_{vlg}.parquet" if vlg else "vlg_*.parquet"
    pattern = os.path.join(DATA_DIR, dist_part, taluk_part, hobli_part, vlg_part)
    return sorted(glob.glob(pattern))


def build_geojson(
    dist: str | None = None,
    taluk: str | None = None,
    hobli: str | None = None,
    vlg: str | None = None,
    survey: str | None = None,
) -> str:
    frames = [
        g
        for p in find_paths(dist, taluk, hobli, vlg)
        if (g := load_village(p)) is not None
    ]
    if not frames:
        return '{"type":"FeatureCollection","features":[]}'
    merged = pd.concat(frames, ignore_index=True)
    if survey and "survey_no" in merged.columns:
        merged = merged[merged["survey_no"] == survey]
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=4326).to_json()


def build_boundary(
    dist: str,
    taluk: str,
    hobli: str,
    vlg: str | None = None,
) -> str:
    """Return GeoJSON FeatureCollection of village boundary polygon(s).

    vlg=code  → one polygon for that village (for /boundary endpoint).
    vlg=None  → one polygon per village in the hobli (for /boundaries endpoint).

    Boundary is derived via union_all() of all parcel geometries — no separate
    boundary dataset required.
    """
    import re

    paths = find_paths(dist, taluk, hobli, vlg)
    features: list[dict] = []

    def _union_to_feature(frames: list, vcode: str, vname: str) -> dict:
        import shapely

        merged = pd.concat(frames, ignore_index=True)
        gdf = gpd.GeoDataFrame(merged, geometry="geometry", crs=4326)
        gdf["geometry"] = gdf.geometry.make_valid()
        # make_valid may produce GeometryCollection; keep only polygon parts for Leaflet
        polys = []
        for g in gdf.geometry:
            if g is None or g.is_empty:
                continue
            if g.geom_type in ("Polygon", "MultiPolygon"):
                polys.append(g)
            elif g.geom_type == "GeometryCollection":
                polys.extend(
                    s for s in g.geoms if s.geom_type in ("Polygon", "MultiPolygon")
                )
        if not polys:
            return None
        boundary = shapely.union_all(polys)
        geom = json.loads(gpd.GeoSeries([boundary], crs=4326).to_json())["features"][0][
            "geometry"
        ]
        return {
            "type": "Feature",
            "geometry": geom,
            "properties": {"village_code": vcode, "village_name": vname},
        }

    if vlg:
        frames = [g for p in paths if (g := load_village(p)) is not None]
        if frames:
            vname = (
                str(frames[0]["village_name"].iloc[0])
                if "village_name" in frames[0].columns
                else ""
            )
            feat = _union_to_feature(frames, vlg, vname)
            if feat:
                feat["properties"]["has_data"] = True
                features.append(feat)
    else:
        # All LGD villages for this hobli from echawadi list (may be empty if JSON missing)
        all_vlg_codes: dict[str, str] = {
            k[3]: v
            for k, v in _NAMES.items()
            if len(k) == 4 and k[0] == dist and k[1] == taluk and k[2] == hobli
        }

        by_vlg: dict[str, list] = {}
        for p in paths:
            m = re.search(r"vlg_(\w+)\.parquet$", p)
            if m:
                by_vlg.setdefault(m.group(1), []).append(p)

        for vcode, vpaths in sorted(by_vlg.items()):
            frames = [g for p in vpaths if (g := load_village(p)) is not None]
            if not frames:
                continue
            # Prefer echawadi name; fall back to parquet column
            vname = all_vlg_codes.get(vcode) or (
                str(frames[0]["village_name"].iloc[0])
                if "village_name" in frames[0].columns
                else ""
            )
            feat = _union_to_feature(frames, vcode, vname)
            if feat:
                feat["properties"]["has_data"] = True
                features.append(feat)

        # LGD villages with no parquet data — null geometry, flagged for frontend
        for vcode, vname in sorted(all_vlg_codes.items()):
            if vcode not in by_vlg:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": {
                            "village_code": vcode,
                            "village_name": vname,
                            "has_data": False,
                        },
                    }
                )

    return json.dumps({"type": "FeatureCollection", "features": features})


def search_survey(q: str, limit: int = 25) -> list[dict[str, Any]]:
    q_norm = q.split("/")[0].strip()
    if len(q_norm) < 2:
        return []
    conn = sqlite3.connect(_INDEX_DB)
    try:
        rows = conn.execute(
            """SELECT DISTINCT survey_no,
                      COALESCE(NULLIF(village_name,''), '') AS vname,
                      dist, taluk, hobli, vlg
               FROM survey_index
               WHERE survey_no_norm LIKE ?
               ORDER BY CAST(survey_no_norm AS INTEGER), vname
               LIMIT ?""",
            (q_norm + "%", limit),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()
    return [
        {
            "survey_no": r[0],
            "village_name": r[1],
            "dist": r[2],
            "taluk": r[3],
            "hobli": r[4],
            "vlg": r[5],
        }
        for r in rows
    ]


def _list_dir_codes(path: str, prefix: str) -> list[str]:
    if not os.path.isdir(path):
        return []
    codes = []
    for n in os.listdir(path):
        if n.startswith(prefix):
            stem = os.path.splitext(n[len(prefix) :])[0]
            if stem.isdigit():
                codes.append(stem)
    return sorted(codes, key=int)


# Parsed from echawadi_village_list.json once at import time.
_NAMES: dict[tuple, str] = {}


def _load_names() -> None:
    json_path = os.path.join(os.path.dirname(DATA_DIR), "echawadi_village_list.json")
    if not os.path.isfile(json_path):
        return
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    for row in data.get("Vlglist", []):
        parts = row.get("vlgcode", "").split(",")
        names = row.get("vlgname", "").split("|")
        if len(parts) < 4 or len(names) < 4:
            continue
        vlg, hobli, taluk, dist = parts[0], parts[1], parts[2], parts[3]
        vname, hname, tname, dname = names[0], names[1], names[2], names[3]
        _NAMES.setdefault((dist,), dname)
        _NAMES.setdefault((dist, taluk), tname)
        _NAMES.setdefault((dist, taluk, hobli), hname)
        _NAMES.setdefault((dist, taluk, hobli, vlg), vname)


_load_names()


def _build_survey_index() -> None:
    """Build survey_index SQLite DB from parquets. Skips if already populated."""
    conn = sqlite3.connect(_INDEX_DB)
    try:
        existing = conn.execute("SELECT COUNT(*) FROM survey_index").fetchone()[0]
        if existing > 0:
            conn.close()
            return
    except sqlite3.OperationalError:
        pass

    logger.info("survey_index missing — building from parquets (background)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS survey_index "
        "(survey_no TEXT, survey_no_norm TEXT, village_name TEXT, "
        "village_code TEXT, dist TEXT, taluk TEXT, hobli TEXT, vlg TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_survey_no_norm ON survey_index(survey_no_norm)"
    )
    conn.commit()

    pattern = os.path.join(DATA_DIR, "dist_*", "taluk_*", "hobli_*", "vlg_*.parquet")
    paths = sorted(glob.glob(pattern))
    logger.info("survey_index: indexing %d parquets", len(paths))
    done = 0
    for path in paths:
        parts = path.replace("\\", "/").split("/")
        try:
            di = next(i for i, p in enumerate(parts) if p.startswith("dist_"))
            dist = parts[di].replace("dist_", "")
            taluk = parts[di + 1].replace("taluk_", "")
            hobli = parts[di + 2].replace("hobli_", "")
            vlg = os.path.splitext(parts[di + 3])[0].replace("vlg_", "")
        except (StopIteration, IndexError):
            continue
        try:
            df = pd.read_parquet(
                path, columns=["survey_no", "village_name", "village_code"]
            )
        except Exception:  # noqa: BLE001,S112
            continue
        if df.empty or "survey_no" not in df.columns:
            continue
        rows = []
        for _, row in df.iterrows():
            sno = str(row.get("survey_no") or "").strip()
            if not sno:
                continue
            rows.append(
                (
                    sno,
                    sno.split("/")[0].strip(),
                    str(row.get("village_name") or ""),
                    str(row.get("village_code") or ""),
                    dist,
                    taluk,
                    hobli,
                    vlg,
                )
            )
        if rows:
            conn.executemany("INSERT INTO survey_index VALUES (?,?,?,?,?,?,?,?)", rows)
        done += 1
        if done % 500 == 0:
            conn.commit()
            logger.info("survey_index: %d/%d parquets done", done, len(paths))
    conn.commit()
    conn.close()
    logger.info("survey_index build complete")


threading.Thread(target=_build_survey_index, daemon=True).start()


def list_districts() -> list[dict[str, str]]:
    codes = _list_dir_codes(DATA_DIR, "dist_")
    result = [{"code": c, "name": _NAMES.get((c,), c)} for c in codes]
    return sorted(result, key=lambda x: x["name"])


def list_taluks(dist: str) -> list[dict[str, str]]:
    path = os.path.join(DATA_DIR, f"dist_{dist}")
    codes = _list_dir_codes(path, "taluk_")
    result = [{"code": c, "name": _NAMES.get((dist, c), c)} for c in codes]
    return sorted(result, key=lambda x: x["name"])


def list_hoblis(dist: str, taluk: str) -> list[dict[str, str]]:
    path = os.path.join(DATA_DIR, f"dist_{dist}", f"taluk_{taluk}")
    codes = _list_dir_codes(path, "hobli_")
    result = [{"code": c, "name": _NAMES.get((dist, taluk, c), c)} for c in codes]
    return sorted(result, key=lambda x: x["name"])


def list_villages(dist: str, taluk: str, hobli: str) -> list[dict[str, str]]:
    path = os.path.join(DATA_DIR, f"dist_{dist}", f"taluk_{taluk}", f"hobli_{hobli}")
    codes = _list_dir_codes(path, "vlg_")
    result = [
        {"code": c, "name": _NAMES.get((dist, taluk, hobli, c), c)} for c in codes
    ]
    return sorted(result, key=lambda x: x["name"])
