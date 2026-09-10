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
import math
import os
import sqlite3
import threading
from typing import Any

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("CADASTRAL_DATA_DIR", "data/cadastral_lake_v2")
_INDEX_DB = os.environ.get("SURVEY_INDEX_DB", "/app/survey_index.db")

# Optional LGD boundary dataset (for /nearby endpoint). Graceful fallback if absent.
_LGD_PARQUET = os.environ.get(
    "LGD_PARQUET_PATH",
    os.path.join(os.path.dirname(DATA_DIR), "lgd_villages.parquet"),
)
_VILLAGE_ROSTER_DB = os.environ.get(
    "VILLAGE_ROSTER_DB",
    os.path.join(os.path.dirname(DATA_DIR), "village_roster.db"),
)

_SWAP_XY = [0, 1, 1, 0, 0, 0]


def load_village(path: str) -> gpd.GeoDataFrame | None:
    """Read one vlg_*.parquet, fix swapped axes and reproject UTM→WGS84."""
    try:
        gdf = gpd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if gdf.empty or "geometry" not in gdf.columns:
        return None
    gdf["geometry"] = gdf.geometry.affine_transform(_SWAP_XY)
    gdf = gdf.set_crs(32643, allow_override=True).to_crs(4326)
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


def search_villages(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """In-memory prefix search of villages with parquet data."""
    q_lower = q.lower().strip()
    if len(q_lower) < 2 or not _PARQUET_VLGS:
        return []
    results: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for key, name in _NAMES.items():
        if len(key) == 4 and key in _PARQUET_VLGS and name.lower().startswith(q_lower):
            if key not in seen:
                seen.add(key)
                results.append({
                    "village_name": name,
                    "dist": key[0], "taluk": key[1], "hobli": key[2], "vlg": key[3],
                    "dist_name": _NAMES.get((key[0],), key[0]),
                    "taluk_name": _NAMES.get((key[0], key[1]), key[1]),
                })
    results.sort(key=lambda x: x["village_name"])
    return results[:limit]


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

# LGD nearby-search state — populated by _load_lgd_support() background thread.
_LGD_CENTROIDS_READY = threading.Event()
_LGD_CENTROID_DF: "pd.DataFrame | None" = None
_LGD_CODES_WITH_DATA: set[int] = set()
_LGD_TO_ECHADAWI: dict[int, tuple[str, str, str, str]] = {}
_LGD_GEOM_CACHE: dict[int, "dict | None"] = {}
_LGD_VILLAGE_NAMES: dict[int, str] = {}
_PARQUET_VLGS: set[tuple[str, str, str, str]] = set()


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


def _load_lgd_support() -> None:
    """Load LGD village centroids + geometry for /nearby. Runs once at startup."""
    global _LGD_CENTROID_DF, _LGD_CODES_WITH_DATA, _LGD_TO_ECHADAWI, _LGD_VILLAGE_NAMES  # noqa: PLW0603

    if not os.path.isfile(_LGD_PARQUET):
        logger.info("lgd_villages.parquet absent — /nearby will return empty")
        _LGD_CENTROIDS_READY.set()
        return

    try:
        gdf = gpd.read_parquet(_LGD_PARQUET)
        # Filter Karnataka only (state_lgd==29) to keep memory/speed reasonable
        if "state_lgd" in gdf.columns:
            gdf = gdf[gdf["state_lgd"] == 29].reset_index(drop=True)
    except Exception as e:
        logger.warning("LGD parquet load failed: %s", e)
        _LGD_CENTROIDS_READY.set()
        return

    # Pre-cache all village geometries as GeoJSON dicts
    for _, row in gdf.iterrows():
        try:
            code = int(row["vil_lgd"])
            geom_ser = gpd.GeoSeries([row.geometry], crs=gdf.crs)
            _LGD_GEOM_CACHE[code] = json.loads(geom_ser.to_json())["features"][0]["geometry"]
        except Exception:  # noqa: BLE001
            pass

    # Cache village names directly from LGD parquet
    if "vilname11" in gdf.columns:
        for _, row in gdf.iterrows():
            try:
                _LGD_VILLAGE_NAMES[int(row["vil_lgd"])] = str(row["vilname11"] or "")
            except Exception:  # noqa: BLE001
                pass

    # Centroid table: vil_lgd, lat, lon
    c = gdf.geometry.centroid
    df = gdf[["vil_lgd"]].copy()
    df = df.rename(columns={"vil_lgd": "lgd_code"})
    df["lat"] = c.y
    df["lon"] = c.x
    _LGD_CENTROID_DF = df

    # Build echadawi mapping from village_roster.db (lgd_code → dist,taluk,hobli,vlg)
    if os.path.isfile(_VILLAGE_ROSTER_DB):
        conn = sqlite3.connect(_VILLAGE_ROSTER_DB)
        try:
            rows = conn.execute(
                "SELECT lgd_code, dist, taluk, hobli, vlg FROM village_roster"
            ).fetchall()
            for r in rows:
                _LGD_TO_ECHADAWI[int(r[0])] = (str(r[1]), str(r[2]), str(r[3]), str(r[4]))
        except Exception as e:
            logger.warning("village_roster.db read failed: %s", e)
        finally:
            conn.close()

    # Fallback: name-match LGD villages against echawadi names if roster.db absent
    if not _LGD_TO_ECHADAWI and _NAMES and _LGD_VILLAGE_NAMES:
        name_to_echadawi: dict[str, tuple] = {}
        for key, vname in _NAMES.items():
            if len(key) == 4:
                norm = vname.lower().strip().replace(" ", "")
                name_to_echadawi.setdefault(norm, key)
        for lgd_code, vname_lgd in _LGD_VILLAGE_NAMES.items():
            norm = vname_lgd.lower().strip().replace(" ", "")
            if norm in name_to_echadawi:
                _LGD_TO_ECHADAWI[lgd_code] = name_to_echadawi[norm]
        logger.info("LGD: name-matched %d villages from echawadi", len(_LGD_TO_ECHADAWI))

    # Which echadawi (dist,taluk,hobli,vlg) tuples have parquet data
    parquet_vlgs: set[tuple[str, str, str, str]] = set()
    for p in find_paths():
        parts = p.replace("\\", "/").split("/")
        try:
            di = next(i for i, s in enumerate(parts) if s.startswith("dist_"))
            parquet_vlgs.add((
                parts[di].replace("dist_", ""),
                parts[di + 1].replace("taluk_", ""),
                parts[di + 2].replace("hobli_", ""),
                os.path.splitext(parts[di + 3])[0].replace("vlg_", ""),
            ))
        except (StopIteration, IndexError):
            continue

    for lgd_code, echadawi in _LGD_TO_ECHADAWI.items():
        if echadawi in parquet_vlgs:
            _LGD_CODES_WITH_DATA.add(lgd_code)

    _LGD_CENTROIDS_READY.set()
    logger.info(
        "LGD support loaded: %d villages, %d mapped, %d with parquet data",
        len(df), len(_LGD_TO_ECHADAWI), len(_LGD_CODES_WITH_DATA),
    )


threading.Thread(target=_load_lgd_support, daemon=True).start()


def _build_parquet_village_set() -> None:
    global _PARQUET_VLGS  # noqa: PLW0603
    vlgs: set[tuple[str, str, str, str]] = set()
    for p in find_paths():
        parts = p.replace("\\", "/").split("/")
        try:
            di = next(i for i, s in enumerate(parts) if s.startswith("dist_"))
            vlgs.add((
                parts[di].replace("dist_", ""),
                parts[di + 1].replace("taluk_", ""),
                parts[di + 2].replace("hobli_", ""),
                os.path.splitext(parts[di + 3])[0].replace("vlg_", ""),
            ))
        except (StopIteration, IndexError):
            continue
    _PARQUET_VLGS = vlgs
    logger.info("Village set built: %d villages with parquet data", len(vlgs))


threading.Thread(target=_build_parquet_village_set, daemon=True).start()


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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_nearby_boundaries(lat: float, lng: float, radius_km: float) -> str:
    """LGD village polygons within radius_km of (lat, lng), sorted nearest-first.

    Returns empty FeatureCollection if LGD data not loaded or lgd_villages.parquet absent.
    Feature properties: lgd_code, village_name, has_data, dist/taluk/hobli/vlg (null if unmapped).
    """
    if not _LGD_CENTROIDS_READY.is_set() or _LGD_CENTROID_DF is None:
        return '{"type":"FeatureCollection","features":[]}'

    results: list[tuple[float, dict]] = []
    for _, row in _LGD_CENTROID_DF.iterrows():
        lgd_code = int(row["lgd_code"])
        d = _haversine_km(lat, lng, float(row["lat"]), float(row["lon"]))
        if d > radius_km:
            continue
        echadawi = _LGD_TO_ECHADAWI.get(lgd_code)
        vname = _LGD_VILLAGE_NAMES.get(lgd_code) or (_NAMES.get(echadawi, "") if echadawi else "")
        feat: dict = {
            "type": "Feature",
            "geometry": _LGD_GEOM_CACHE.get(lgd_code),
            "properties": {
                "lgd_code": lgd_code,
                "village_name": vname,
                "has_data": lgd_code in _LGD_CODES_WITH_DATA,
                "dist": echadawi[0] if echadawi else None,
                "taluk": echadawi[1] if echadawi else None,
                "hobli": echadawi[2] if echadawi else None,
                "vlg": echadawi[3] if echadawi else None,
            },
        }
        results.append((d, feat))

    results.sort(key=lambda x: x[0])
    return json.dumps({"type": "FeatureCollection", "features": [f for _, f in results]})
