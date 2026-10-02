#!/usr/bin/env python3
"""One polygon per cadastral village (Bengaluru Urban dist 20, Rural dist 21).

Usage:
    python build_villages.py --data-root <dir> --cadastral-dir <dir>

Each village = union of its parcels (X/Y swapped back, EPSG:32643, 0.1 m grid), so
village-to-LPA shares are polygon intersections. Villages whose parquet has no geometry
(placeholder files) are listed with an empty geometry.

Writes <data-root>/planning/villages.parquet.
"""

import argparse
import glob
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from extract_hoskote import CRS_M
from extract_plucomp import geoparquet

DISTS = ("20", "21")


def swap(g):
    return shapely.transform(g, lambda xy: xy[:, ::-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    args = ap.parse_args()
    out = os.path.join(args.data_root, "planning", "villages.parquet")
    rows, geoms = [], []
    for dist in DISTS:
        for f in sorted(
            glob.glob(
                os.path.join(
                    args.cadastral_dir,
                    f"dist_{dist}",
                    "taluk_*",
                    "hobli_*",
                    "vlg_*.parquet",
                )
            )
        ):
            parts = f.replace("\\", "/").split("/")
            key = (
                dist,
                parts[-3].split("_")[1],
                parts[-2].split("_")[1],
                parts[-1][4:-8],
            )
            names = pq.read_schema(f).names
            if "geometry" not in names:
                rows.append(
                    {
                        "dist": key[0],
                        "taluk": key[1],
                        "hobli": key[2],
                        "vlg": key[3],
                        "village_name": "",
                        "parcels": 0,
                        "parcel_area_m2": 0.0,
                    }
                )
                geoms.append(shapely.Polygon())
                continue
            t = pq.read_table(f, columns=["geometry", "village_name"])
            g = shapely.make_valid(
                swap(
                    shapely.from_wkb(
                        t.column("geometry").to_numpy(zero_copy_only=False)
                    )
                )
            )
            g = g[~shapely.is_empty(g)]
            vn = next((n for n in t.column("village_name").to_pylist() if n), "")
            g = np.array(
                [q for q in shapely.get_parts(g) if q.geom_type == "Polygon"],
                dtype=object,
            )
            u = shapely.union_all(g, grid_size=0.1) if len(g) else shapely.Polygon()
            u = shapely.union_all(
                [q for q in shapely.get_parts(u) if q.geom_type == "Polygon"]
            )
            if u.geom_type not in ("Polygon", "MultiPolygon"):
                u = shapely.MultiPolygon(
                    [q for q in shapely.get_parts(u) if q.geom_type == "Polygon"]
                )
            rows.append(
                {
                    "dist": key[0],
                    "taluk": key[1],
                    "hobli": key[2],
                    "vlg": key[3],
                    "village_name": vn,
                    "parcels": len(g),
                    "parcel_area_m2": float(shapely.area(g).sum()),
                }
            )
            geoms.append(u)
            if len(rows) % 200 == 0:
                print(f"  {len(rows)} villages", flush=True)
    geoparquet(
        out,
        pa.table({k: [r[k] for r in rows] for k in rows[0]}),
        np.array(geoms, dtype=object),
        CRS_M,
    )
    print(f"done: {len(rows)} villages -> {out}")


if __name__ == "__main__":
    main()
