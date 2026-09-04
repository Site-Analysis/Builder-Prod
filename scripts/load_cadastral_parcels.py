#!/usr/bin/env python3
"""Load Karnataka cadastral parcel geometries into CockroachDB.

Processes one district at a time. Resumable — skips villages already present.
Applies X/Y swap fix (affine [0,1,1,0,0,0]) + EPSG:32643→4326 reproject.

Usage:
    export DATABASE_URL="postgresql://user:pass@host/builder_prod?sslmode=verify-full"
    export CADASTRAL_DATA_DIR="/path/to/cadastral_lake_v2"
    python scripts/load_cadastral_parcels.py              # full load
    python scripts/load_cadastral_parcels.py --dry-run    # schema check + first 100 rows only
    python scripts/load_cadastral_parcels.py --dist 1     # single district
"""

from __future__ import annotations

import argparse
import glob
import io
import logging
import os
import sys

import geopandas as gpd
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_SWAP_XY = [0, 1, 1, 0, 0, 0]


def _load_village(path: str) -> gpd.GeoDataFrame | None:
    try:
        gdf = gpd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if gdf.empty or "geometry" not in gdf.columns:
        return None
    gdf["geometry"] = gdf.geometry.affine_transform(_SWAP_XY)
    gdf = gdf.set_crs(32643, allow_override=True).to_crs(4326)
    return gdf


def _normalize(s: str) -> str:
    return s.split("/")[0].strip()


def _make_village_code(dist: str, taluk: str, hobli: str, vlg: str) -> str:
    return f"{dist}_{taluk}_{hobli}_{vlg}"


def _already_loaded(cur: "psycopg2.cursor", village_code: str) -> bool:
    cur.execute(
        "SELECT 1 FROM parcels WHERE village_code = %s LIMIT 1", (village_code,)
    )
    return cur.fetchone() is not None


def _copy_parcels(
    cur: "psycopg2.cursor",
    rows: list[tuple[str, str, str, str]],
) -> None:
    """Bulk-insert via COPY FROM STDIN."""
    buf = io.StringIO()
    for village_code, survey_no, survey_no_norm, wkt in rows:
        buf.write(f"{village_code}\t{survey_no}\t{survey_no_norm}\t{wkt}\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY parcels (village_code, survey_no, survey_no_norm, geom) "
        "FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL '')",
        buf,
    )


def process_district(
    conn: "psycopg2.connection",
    data_dir: str,
    dist: str,
    dry_run: bool,
) -> None:
    pattern = os.path.join(
        data_dir, f"dist_{dist}", "taluk_*", "hobli_*", "vlg_*.parquet"
    )
    paths = sorted(glob.glob(pattern))
    log.info("dist_%s: %d parquets found", dist, len(paths))
    cur = conn.cursor()
    done = 0
    skipped = 0

    for path in paths:
        parts = path.replace("\\", "/").split("/")
        try:
            di = next(i for i, p in enumerate(parts) if p.startswith("dist_"))
            taluk = parts[di + 1].replace("taluk_", "")
            hobli = parts[di + 2].replace("hobli_", "")
            vlg = os.path.splitext(parts[di + 3])[0].replace("vlg_", "")
        except (StopIteration, IndexError):
            continue

        village_code = _make_village_code(dist, taluk, hobli, vlg)

        if not dry_run and _already_loaded(cur, village_code):
            skipped += 1
            continue

        gdf = _load_village(path)
        if gdf is None or gdf.empty:
            continue
        if "survey_no" not in gdf.columns:
            continue

        rows = []
        for _, row in gdf.iterrows():
            sno = str(row.get("survey_no") or "").strip()
            if not sno:
                continue
            wkt = (
                row.geometry.wkt if row.geometry and not row.geometry.is_empty else None
            )
            if not wkt:
                continue
            rows.append((village_code, sno, _normalize(sno), wkt))

        if not rows:
            continue

        if dry_run:
            log.info(
                "[dry-run] %s → %d rows (sample: %s)", village_code, len(rows), rows[0]
            )
            done += 1
            if done >= 5:
                log.info("[dry-run] stopping after 5 villages")
                break
            continue

        try:
            _copy_parcels(cur, rows)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log.warning("COPY failed for %s: %s", village_code, exc)
            continue

        done += 1
        if done % 100 == 0:
            log.info("dist_%s: %d villages loaded, %d skipped", dist, done, skipped)

    cur.close()
    log.info("dist_%s complete: %d loaded, %d skipped", dist, done, skipped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dist", help="Process single district code only")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    data_dir = os.environ.get("CADASTRAL_DATA_DIR", "data/cadastral_lake_v2")
    if not os.path.isdir(data_dir):
        print(f"ERROR: CADASTRAL_DATA_DIR not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    if args.dist:
        dists = [args.dist]
    else:
        dists = sorted(
            d.replace("dist_", "")
            for d in os.listdir(data_dir)
            if d.startswith("dist_") and os.path.isdir(os.path.join(data_dir, d))
        )

    log.info("Districts to process: %s", dists)
    conn = psycopg2.connect(db_url)

    for dist in dists:
        process_district(conn, data_dir, dist, dry_run=args.dry_run)

    conn.close()
    log.info("All done.")


if __name__ == "__main__":
    main()
