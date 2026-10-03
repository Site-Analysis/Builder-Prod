# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Point -> village, for the village summary of a lat/lng answer (contract 1.18).

Village outlines are the cadastral service's parcel unions (`/boundaries`, one request per
hobli of authority_villages.csv), fetched in a background thread on the first point query
and kept in RAM
only (open-decisions #35); nothing is written to disk. Until they are loaded, or when the
cadastral service cannot be reached, the lookup returns None and the answer's
`village_summary` is null.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx
import numpy as np
import shapely
from pyproj import Transformer

log = logging.getLogger(__name__)

_TO_METRIC = Transformer.from_crs(4326, 32643, always_xy=True)
_TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True)
SIMPLIFY_M = 5.0


class VillageIndex:
    def __init__(self, hoblis: list[tuple[str, str, str]], cadastral_url: str):
        self._hoblis = sorted(set(hoblis))
        self._url = cadastral_url.rstrip("/")
        self._keys: list[tuple[str, str, str, str]] = []
        self._tree: shapely.STRtree | None = None
        self._geoms: np.ndarray | None = None
        self.loaded_hoblis = 0
        on = os.getenv("PLANNING_VILLAGE_INDEX", "1") == "1" and self._hoblis
        self.state = "not_started" if on else "off"
        self._start_lock = threading.Lock()
        self._gj: dict[int, str] = {}  # village index -> GeoJSON geometry (25 m, WGS84)

    def _start(self) -> None:
        """Load on the first point query, not at start: the cadastral service unions ~2,300
        villages' parcels for it (~5 min), which would compete with the first sheet loads."""
        with self._start_lock:
            if self.state != "not_started":
                return
            self.state = "loading"
        threading.Thread(
            target=self._load, name="planning-villages", daemon=True
        ).start()

    def _load(self) -> None:
        keys, geoms = [], []
        t0 = time.time()
        headers = {}
        tok = os.getenv("PLANNING_CADASTRAL_TOKEN")  # optional service token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        with httpx.Client(timeout=300.0, headers=headers) as c:
            for d, t, h in self._hoblis:
                try:
                    r = c.get(
                        f"{self._url}/boundaries",
                        params={"dist": d, "taluk": t, "hobli": h},
                    )
                    r.raise_for_status()
                    fc = r.json()
                except Exception as ex:  # noqa: BLE001 - degrade: no village summary
                    log.warning("village outlines for %s/%s/%s: %s", d, t, h, ex)
                    continue
                for f in fc.get("features") or []:
                    g = f.get("geometry")
                    p = f.get("properties") or {}
                    code = str(p.get("village_code") or "")
                    if not g or not code:
                        continue
                    shp = shapely.geometry.shape(g)
                    shp = shapely.transform(
                        shp,
                        lambda xy: np.column_stack(
                            _TO_METRIC.transform(xy[:, 0], xy[:, 1])
                        ),
                    )
                    keys.append((d, t, h, code))
                    geoms.append(shapely.simplify(shapely.make_valid(shp), SIMPLIFY_M))
                self.loaded_hoblis += 1
        if not geoms:
            self.state = "unavailable"
            return
        self._geoms = np.array(geoms, dtype=object)
        self._keys = keys
        self._tree = shapely.STRtree(self._geoms)
        self.state = "ready"
        log.info(
            "village outlines: %d villages, %d hoblis, %.0f s",
            len(keys),
            self.loaded_hoblis,
            time.time() - t0,
        )

    def lookup(self, x: float, y: float) -> tuple[str, str, str, str] | None:
        """Village key containing the EPSG:32643 point, or None (not loaded / outside)."""
        if self._tree is None:
            self._start()
            return None
        pt = shapely.Point(x, y)
        hit = self._tree.query(pt, predicate="intersects")
        if len(hit):
            return self._keys[int(hit[0])]
        return None

    def geojson_in(self, box) -> list[tuple[tuple[str, str, str, str], str]]:
        """(village key, GeoJSON geometry text) of every village meeting the EPSG:32643 box,
        simplified to 25 m (Coverage status layer, 1.19); starts loading if needed."""
        if self._tree is None:
            self._start()
            return []
        out = []
        for i in sorted(self._tree.query(box, predicate="intersects").tolist()):
            gj = self._gj.get(i)
            if gj is None:
                g = shapely.simplify(self._geoms[i], 25.0, preserve_topology=True)
                g = shapely.transform(
                    g,
                    lambda xy: np.round(
                        np.column_stack(_TO_WGS.transform(xy[:, 0], xy[:, 1])), 6
                    ),
                )
                gj = shapely.to_geojson(g)
                self._gj[i] = gj
            out.append((self._keys[i], gj))
        return out
