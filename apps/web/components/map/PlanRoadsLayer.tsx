// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Plan road layer (contract 1.20; open-decisions #72-#77): ROW corridors drawn to scale from a
// plan's Mobility Plan, published with the map tiles (`manifest.roads`). Corridors are coloured
// by ROW, dashed when proposed; width labels from zoom 15; a click on a corridor tells what the
// plan says about it. Read once per page from storage; no planning-service call.

"use client";

import { useEffect } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import type { PrebuiltRoads } from "./PrebuiltPlanLayers";

export const ROAD_CLASSES: { max: number; colour: string; label: string }[] = [
  { max: 12, colour: "#00897B", label: "up to 12 m" },
  { max: 18, colour: "#1E88E5", label: "over 12 to 18 m" },
  { max: 24, colour: "#F4511E", label: "over 18 to 24 m" },
  { max: 30, colour: "#8E24AA", label: "over 24 to 30 m" },
  { max: 45, colour: "#D81B60", label: "over 30 to 45 m" },
  { max: Infinity, colour: "#4E342E", label: "over 45 m (ring and radial roads)" },
];

export const ROAD_STATUS_TEXT: Record<string, string> = {
  to_be_widened: "Existing road to be widened (plan ROW)",
  proposed: "Proposed road (plan ROW)",
  existing_row_stated: "Existing road; the plan states its ROW",
  ring_proposed: "Proposed ring / radial road (plan ROW from the sheet legend)",
  existing_drawn: "Existing road (no width label on the plan)",
  plan_row_stated: "Road on the plan; the plan states its ROW",
};

const SOURCE_TEXT: Record<string, string> = {
  label_and_drawn: "width label, matching the drawn ROW lines",
  label: "width label on the plan",
  drawn: "drawn ROW lines (no label)",
  legend: "sheet legend (checked against the Zonal Regulations)",
  drawn_band: "road as drawn on the plan, measured to scale (about ±1-2 m)",
};

export function roadColour(rowM: number): string {
  return (ROAD_CLASSES.find((c) => rowM <= c.max) ?? ROAD_CLASSES[ROAD_CLASSES.length - 1]).colour;
}

const MIN_ZOOM = 5; // corridors from far out, as the zone tiles
const THIN_ZOOM = 14; // below this a corridor is under ~2 px wide: draw a thicker outline
const LABEL_ZOOM = 15;
const SMALL_LABEL_ZOOM = 16; // widths of drawn-only roads
const MAX_LABELS = 300;
const PANE = "qnit-plan-roads"; // under the parcel layer, so parcel clicks still reach parcels

const cache = new Map<string, Promise<GeoJSON.FeatureCollection | null>>();
function loadRoads(url: string): Promise<GeoJSON.FeatureCollection | null> {
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then(async (r) => {
      if (!r.ok) return null;
      const buf = new Uint8Array(await r.arrayBuffer());
      // published roads files are gzip (1.21): decompress in the browser
      if (buf[0] === 0x1f && buf[1] === 0x8b) {
        const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
        return JSON.parse(await new Response(stream).text());
      }
      return JSON.parse(new TextDecoder().decode(buf));
    }).catch(() => null));
  }
  return cache.get(url)!;
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function popupHtml(p: Record<string, unknown>, roads: PrebuiltRoads): string {
  const row = Number(p.row_m);
  const name = p.road_name ? `${esc(String(p.road_name))} · ` : "";
  const drawn = p.drawn_row_m_median != null ? `<div>Drawn ROW lines: ${Number(p.drawn_row_m_median).toFixed(1)} m apart</div>` : "";
  const band = p.drawn_band_m != null && p.status !== "existing_drawn" ? `<div>Existing road as drawn: about ${Number(p.drawn_band_m).toFixed(1)} m</div>` : "";
  if (p.status === "existing_drawn") {
    return `<div style="font-size:11px;line-height:1.4;max-width:240px">
      <div style="font-weight:800">${name}About ${row} m wide (as drawn)</div>
      <div>Existing road; the plan gives no width label, this is its width as drawn to scale (about ±1-2 m)</div>
      <div style="color:#5B6B60;margin-top:3px">Source: ${esc(roads.doc_id)}</div>
    </div>`;
  }
  return `<div style="font-size:11px;line-height:1.4;max-width:240px">
    <div style="font-weight:800">${name}ROW ${row} m</div>
    <div>${esc(ROAD_STATUS_TEXT[String(p.status)] ?? String(p.status))}</div>
    <div>Width from: ${esc(SOURCE_TEXT[String(p.width_source)] ?? String(p.width_source))} (${esc(String(p.confidence))})</div>
    ${drawn}
    ${band}
    <div style="color:#5B6B60;margin-top:3px">Source: ${esc(roads.doc_id)}</div>
  </div>`;
}

/** One plan's road layer on the map (mounts inside <MapContainer>). */
export function PlanRoadsLayer({ roads }: { roads: PrebuiltRoads }) {
  const map = useMap();
  useEffect(() => {
    let live = true;
    if (!map.getPane(PANE)) {
      const pane = map.createPane(PANE);
      pane.style.zIndex = "390"; // zone tiles 350 < roads < parcels (overlay pane 400)
    }
    const renderer = L.canvas({ pane: PANE, padding: 0.5 });
    let corridors: L.GeoJSON | null = null;
    const labels = L.layerGroup();
    let points: { ll: L.LatLng; row: number; approx?: boolean }[] = [];
    let lastThin: boolean | null = null;
    // outline width by zoom: corridors stay visible as lines when zoomed out; drawn-only
    // (unlabelled) roads lighter and thinner
    const styleFor = (f?: GeoJSON.Feature): L.PathOptions => {
      const p = (f?.properties ?? {}) as Record<string, unknown>;
      const col = roadColour(Number(p.row_m));
      const proposed = p.status === "proposed" || p.status === "ring_proposed";
      const small = p.status === "existing_drawn";
      const thin = map.getZoom() < THIN_ZOOM;
      return {
        renderer, color: col, weight: thin ? (small ? 1.5 : 2.5) : small ? 0.6 : 1,
        opacity: small ? 0.7 : 0.9, fillColor: col,
        fillOpacity: small ? 0.2 : p.status === "existing_row_stated" ? 0.28 : 0.45,
        dashArray: proposed ? "4 3" : undefined,
      };
    };

    const refreshLabels = () => {
      labels.clearLayers();
      if (map.getZoom() < LABEL_ZOOM) return;
      const b = map.getBounds();
      let n = 0;
      const z = map.getZoom();
      for (const pt of points) {
        if (pt.approx && z < SMALL_LABEL_ZOOM) continue;
        if (!b.contains(pt.ll)) continue;
        if (++n > MAX_LABELS) break;
        labels.addLayer(L.marker(pt.ll, {
          pane: PANE, interactive: false, keyboard: false,
          icon: L.divIcon({
            className: "qnit-road-label",
            html: pt.approx
              ? `<span style="background:rgba(255,255,255,0.85);border:1px dashed ${roadColour(pt.row)};color:#3A3F3B;border-radius:9px;padding:0 3px;font:600 9px/12px system-ui;white-space:nowrap">≈ ${pt.row} m</span>`
              : `<span style="background:#FFFFFF;border:1.5px solid ${roadColour(pt.row)};color:#1F2A24;border-radius:9px;padding:0 4px;font:700 10px/14px system-ui;white-space:nowrap">${pt.row} m</span>`,
            iconSize: [0, 0],
          }),
        }));
      }
    };
    const refresh = () => {
      if (!corridors) return;
      const z = map.getZoom();
      const show = z >= MIN_ZOOM;
      // outline width by zoom: corridors stay visible as lines when zoomed out
      const thin = z < THIN_ZOOM;
      if (thin !== lastThin) { lastThin = thin; corridors.setStyle(styleFor as L.StyleFunction); }
      if (show && !map.hasLayer(corridors)) corridors.addTo(map);
      if (!show && map.hasLayer(corridors)) map.removeLayer(corridors);
      refreshLabels();
    };

    loadRoads(roads.url).then((fc) => {
      if (!live || !fc) return;
      points = fc.features
        .filter((f) => (f.properties as Record<string, unknown>)?.part === "label" && f.geometry.type === "Point")
        .map((f) => {
          const c = (f.geometry as GeoJSON.Point).coordinates;
          return { ll: L.latLng(c[1], c[0]), row: Number((f.properties as Record<string, unknown>).row_m) };
        });
      // drawn-only roads: one approximate width label per road piece, at its middle
      for (const f of fc.features) {
        const p = (f.properties ?? {}) as Record<string, unknown>;
        if (p.part !== "centreline" || p.status !== "existing_drawn" || f.geometry.type !== "LineString") continue;
        const cs = (f.geometry as GeoJSON.LineString).coordinates;
        const c = cs[Math.floor(cs.length / 2)];
        points.push({ ll: L.latLng(c[1], c[0]), row: Number(p.row_m), approx: true });
      }
      corridors = L.geoJSON(fc, {
        pane: PANE,
        filter: (f) => (f.properties as Record<string, unknown>)?.part === "corridor",
        style: styleFor,
        onEachFeature: (f, layer) => {
          layer.bindPopup(() => popupHtml((f.properties ?? {}) as Record<string, unknown>, roads));
        },
      });
      labels.addTo(map);
      refresh();
    });
    map.on("zoomend moveend", refresh);
    return () => {
      live = false;
      map.off("zoomend moveend", refresh);
      if (corridors) map.removeLayer(corridors);
      map.removeLayer(labels);
    };
  }, [map, roads]);
  return null;
}
