#!/usr/bin/env python3
"""Load Karnataka cadastral hierarchy into CockroachDB.

Reads echawadi_village_list.json (alongside CADASTRAL_DATA_DIR) and populates
districts / taluks / hoblis / villages tables. Idempotent — uses ON CONFLICT DO NOTHING.

Usage:
    export DATABASE_URL="postgresql://user:pass@host/builder_prod?sslmode=verify-full"
    export CADASTRAL_DATA_DIR="/path/to/cadastral_lake_v2"
    python scripts/load_cadastral_hierarchy.py
"""

from __future__ import annotations

import json
import os
import sys

import psycopg2


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    data_dir = os.environ.get("CADASTRAL_DATA_DIR", "data/cadastral_lake_v2")
    json_path = os.path.join(os.path.dirname(data_dir), "echawadi_village_list.json")
    if not os.path.isfile(json_path):
        print(f"ERROR: {json_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = data.get("Vlglist", [])
    print(f"Parsing {len(rows)} rows from echawadi_village_list.json")

    districts: dict[int, str] = {}
    taluks: dict[tuple[int, int], str] = {}
    hoblis: dict[tuple[int, int, int], str] = {}
    villages: list[tuple[int, int, int, int, str, int]] = []

    for row in rows:
        parts = row.get("vlgcode", "").split(",")
        names = row.get("vlgname", "").split("|")
        if len(parts) < 4 or len(names) < 4:
            continue
        try:
            vlg_c = int(parts[0])
            hob_c = int(parts[1])
            tal_c = int(parts[2])
            dis_c = int(parts[3])
        except ValueError:
            continue
        vname, hname, tname, dname = names[0], names[1], names[2], names[3]

        lgd_raw = row.get("lgdcode", "0")
        try:
            lgd = int(lgd_raw) if lgd_raw else 0
        except (ValueError, TypeError):
            lgd = 0

        districts.setdefault(dis_c, dname)
        taluks.setdefault((dis_c, tal_c), tname)
        hoblis.setdefault((dis_c, tal_c, hob_c), hname)
        villages.append((dis_c, tal_c, hob_c, vlg_c, vname, lgd))

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    print(f"Inserting {len(districts)} districts...")
    cur.executemany(
        "INSERT INTO districts (dist_code, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        list(districts.items()),
    )

    print(f"Inserting {len(taluks)} taluks...")
    cur.executemany(
        "INSERT INTO taluks (dist_code, taluk_code, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        [(k[0], k[1], v) for k, v in taluks.items()],
    )

    print(f"Inserting {len(hoblis)} hoblis...")
    cur.executemany(
        "INSERT INTO hoblis (dist_code, taluk_code, hobli_code, name) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        [(k[0], k[1], k[2], v) for k, v in hoblis.items()],
    )

    print(f"Inserting {len(villages)} villages...")
    cur.executemany(
        "INSERT INTO villages (dist_code, taluk_code, hobli_code, vlg_code, name, lgd_code) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        villages,
    )

    conn.commit()
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
