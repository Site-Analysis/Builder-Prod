// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Pre-drawn 2031 zone layers (open-decisions #65): one raster PMTiles file per plan (and zoom
// range), built once by infra/scripts/planning/build_tiles.py and kept in the public Supabase
// Storage bucket `planning-tiles`. A plan switch shows its tiles straight from storage: no
// sheet download, no extraction, no planning-service call. Behind
// NEXT_PUBLIC_PLANNING_PREBUILT_TILES=1; plans not in the manifest stay on demand.

"use client";

import { useEffect, useState } from "react";
import { useMap } from "react-leaflet";
import { PMTiles, leafletRasterLayer } from "pmtiles";
import type { Layer } from "leaflet";

export interface PrebuiltPlan {
  build_id: string;
  extent: [number, number, number, number];
  files: { url: string; minzoom: number; maxzoom: number; bytes: number }[];
  legend: { label: string; cnorm: string | null; colour: string }[];
  warnings: string[];
  placement_unconfirmed: string[];
}

export type PrebuiltManifest = Record<string, PrebuiltPlan>;

export const PREBUILT_ON = process.env.NEXT_PUBLIC_PLANNING_PREBUILT_TILES === "1";
const MANIFEST_URL = `${(process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").replace(/\/$/, "")}/storage/v1/object/public/planning-tiles/manifest.json`;

/** The manifest of pre-drawn plans ({} when off or unavailable: everything stays on demand). */
export function usePrebuiltManifest(): PrebuiltManifest {
  const [m, setM] = useState<PrebuiltManifest>({});
  useEffect(() => {
    if (!PREBUILT_ON) return;
    const ctrl = new AbortController();
    fetch(MANIFEST_URL, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : { plans: {} }))
      .then((j) => setM(j.plans ?? {}))
      .catch(() => setM({}));
    return () => ctrl.abort();
  }, []);
  return m;
}

/** One plan's pre-drawn tiles on the map (mounts inside <MapContainer>). */
export function PrebuiltPlanLayer({ plan, opacity }: { plan: PrebuiltPlan; opacity: number }) {
  const map = useMap();
  useEffect(() => {
    const layers: Layer[] = plan.files.map((f, i) => {
      const last = i === plan.files.length - 1;
      return leafletRasterLayer(new PMTiles(f.url), {
        minZoom: f.minzoom,
        // the last file is over-zoomed beyond its native zoom (scaled by Leaflet)
        maxZoom: last ? 22 : f.maxzoom,
        maxNativeZoom: f.maxzoom,
        minNativeZoom: f.minzoom,
        opacity,
        zIndex: 350,
        attribution: "2031 plan zones (pre-drawn)",
      }) as Layer;
    });
    layers.forEach((l) => l.addTo(map));
    return () => layers.forEach((l) => map.removeLayer(l));
  }, [map, plan, opacity]);
  return null;
}
