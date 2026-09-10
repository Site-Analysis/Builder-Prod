#!/usr/bin/env python3
"""Build lgd_index.db from lgd_villages.parquet (Karnataka only).

Usage:
    python build_lgd_index.py <parquet_path> [output_db_path]

Default output: lgd_index.db in same dir as parquet.
"""

import json
import os
import sqlite3
import sys

import geopandas as gpd

parquet_path = sys.argv[1] if len(sys.argv) > 1 else "lgd_villages.parquet"
db_path = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.path.join(os.path.dirname(parquet_path), "lgd_index.db")
)

print(f"Reading {parquet_path} ...")
gdf = gpd.read_parquet(parquet_path)
print(f"Total rows: {len(gdf)}, CRS: {gdf.crs}")

if "state_lgd" in gdf.columns:
    gdf = gdf[gdf["state_lgd"] == 29].reset_index(drop=True)
    print(f"Karnataka rows: {len(gdf)}")

if gdf.crs and gdf.crs.to_epsg() != 4326:
    print(f"Reprojecting {gdf.crs} to EPSG:4326 ...")
    gdf = gdf.to_crs(4326)

if os.path.exists(db_path):
    os.remove(db_path)

conn = sqlite3.connect(db_path)
conn.execute("""
    CREATE TABLE lgd_villages (
        lgd_code    INTEGER PRIMARY KEY,
        village_name TEXT,
        centroid_lat REAL,
        centroid_lng REAL,
        geom_geojson TEXT
    )
""")
conn.execute("CREATE INDEX idx_centroid ON lgd_villages (centroid_lat, centroid_lng)")

centroids = gdf.geometry.centroid
rows = []
errors = 0
for i, row in gdf.iterrows():
    try:
        lgd_code = int(row.get("vil_lgd", 0))
        name = str(row.get("vilname11", "") or "")
        clat = float(centroids.iloc[i].y)
        clng = float(centroids.iloc[i].x)
        geom = json.dumps(row.geometry.__geo_interface__) if row.geometry else None
        rows.append((lgd_code, name, clat, clng, geom))
    except Exception as e:
        errors += 1
        if errors <= 5:
            print(f"  Skip row {i}: {e}")

conn.executemany("INSERT OR REPLACE INTO lgd_villages VALUES (?,?,?,?,?)", rows)
conn.commit()
conn.close()

size_mb = os.path.getsize(db_path) / (1024**2)
print(f"Done: {db_path}")
print(f"  Villages: {len(rows)} (errors: {errors})")
print(f"  Size: {size_mb:.1f} MB")
