// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Build step 1.4 — RMP 2031 (Draft) zone layer, map-symbol overlays, legend and the
// parcel-card section. Rendered only when NEXT_PUBLIC_ENABLE_PLANNING_LAYERS is on.
// Facts only: no answer or confidence wording.

"use client";

import { useEffect, useRef, useState } from "react";
import { GeoJSON, useMap } from "react-leaflet";
import type { PathOptions } from "leaflet";
import {
  fetchOverlays, fetchZones, simplifyForZoom, splitBbox,
  type Bbox, type OverlayKind, type ZonesAtResult, type ZoneProperties,
} from "@/lib/api/planning";
import { PLANNING_LEGEND } from "@/lib/planning/legend.generated";

const MAX_TILES = 4; // 4 service boxes of 0.05 deg; beyond that ask the user to zoom in
const DRAFT_LABEL = "Draft, never approved";
const COLOUR_BY_LABEL = new Map(PLANNING_LEGEND.map((e) => [e.label, e.colour]));

export interface PlanningToggles {
  zones: boolean;
  ngt_buffer: boolean;
  forest_symbol: boolean;
  stream_centreline: boolean;
}

export const OVERLAY_UI: Record<OverlayKind, { label: string; legend: string; style: PathOptions }> = {
  ngt_buffer: {
    label: "NGT buffer",
    legend: "NGT buffer (map symbol)",
    style: { color: "#38a800", weight: 1, dashArray: "4 3", fillColor: "#38a800", fillOpacity: 0.12 },
  },
  forest_symbol: {
    label: "Forest",
    legend: "Forest symbol area (map symbol)",
    style: { color: "#2f8d00", weight: 1, dashArray: "1 3", fillColor: "#2f8d00", fillOpacity: 0.15 },
  },
  stream_centreline: {
    label: "Streams",
    legend: "Stream centreline (map symbol)",
    style: { color: "#0084a8", weight: 2, opacity: 0.9 },
  },
};

const OVERLAY_KINDS: OverlayKind[] = ["ngt_buffer", "forest_symbol", "stream_centreline"];
const STORAGE_KEY = "planning.toggles.v1";
const ALL_OFF: PlanningToggles = { zones: false, ngt_buffer: false, forest_symbol: false, stream_centreline: false };

/** Saved switch state; all off when nothing is saved or storage is unavailable. */
export function loadPlanningToggles(): PlanningToggles {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return ALL_OFF;
    const saved = JSON.parse(raw) as Partial<PlanningToggles>;
    return {
      zones: saved.zones === true,
      ngt_buffer: saved.ngt_buffer === true,
      forest_symbol: saved.forest_symbol === true,
      stream_centreline: saved.stream_centreline === true,
    };
  } catch {
    return ALL_OFF;
  }
}

export function savePlanningToggles(t: PlanningToggles): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
  } catch {
    // storage blocked (private window etc.): the switches still work for this page
  }
}

/** The zone switch gates everything: overlays are drawn only while it is on. */
export function effectiveToggles(t: PlanningToggles): PlanningToggles {
  return t.zones ? t : ALL_OFF;
}

function zoneStyle(f?: GeoJSON.Feature): PathOptions {
  const p = (f?.properties ?? {}) as ZoneProperties;
  const colour = COLOUR_BY_LABEL.get(p.zone_label_native) ?? "#999999";
  const uncoloured = p.class_norm === "uncoloured";
  if (p.class_norm === "road_space") {
    // cartographic class: road corridors drawn as lines over white on the sheet
    return { color: "#BDBDBD", weight: 0, fillColor: "#E0E0E0", fillOpacity: 0.35 };
  }
  return {
    color: p.inferred_note ? "#8E24AA" : uncoloured ? "#9E9E9E" : colour,
    weight: p.inferred_note ? 1 : 0.5,
    dashArray: p.inferred_note || uncoloured ? "3 3" : undefined,
    opacity: 0.8,
    fillColor: uncoloured ? "#FFFFFF" : colour,
    fillOpacity: uncoloured ? 0.2 : 0.45,
  };
}

function viewBbox(map: ReturnType<typeof useMap>): Bbox {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
}

type LayerKey = keyof PlanningToggles;
type LayerData = Partial<Record<LayerKey, GeoJSON.FeatureCollection>>;

/** Map layers; mounts inside <MapContainer>. */
export function PlanningMapLayers({
  toggles, onStatus,
}: {
  toggles: PlanningToggles;
  onStatus: (s: string) => void;
}) {
  const map = useMap();
  const [data, setData] = useState<LayerData>({});
  const [version, setVersion] = useState(0);
  const ctrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const active = (Object.keys(toggles) as LayerKey[]).filter((k) => toggles[k]);
    async function load() {
      ctrlRef.current?.abort();
      if (!active.length) { setData({}); onStatus(""); return; }
      const tiles = splitBbox(viewBbox(map));
      if (tiles.length > MAX_TILES) {
        setData({});
        onStatus("Zoom in to load RMP 2031 layers");
        return;
      }
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const simplify = simplifyForZoom(map.getZoom());
      onStatus("Loading…");
      try {
        const next: LayerData = {};
        for (const key of active) {
          const parts = await Promise.all(tiles.map((t) =>
            key === "zones" ? fetchZones(t, simplify, ctrl.signal) : fetchOverlays(t, key, simplify, ctrl.signal),
          ));
          const seen = new Set<string>();
          const features: GeoJSON.Feature[] = [];
          for (const fc of parts) {
            for (const f of fc.features) {
              const p = f.properties as unknown as Record<string, string>;
              const uid = p.zone_uid ?? p.overlay_uid;
              if (!seen.has(uid)) { seen.add(uid); features.push(f as GeoJSON.Feature); }
            }
          }
          next[key] = { type: "FeatureCollection", features };
        }
        if (ctrl.signal.aborted) return;
        setData(next);
        setVersion((v) => v + 1);
        onStatus("");
      } catch (err) {
        if (!ctrl.signal.aborted) onStatus(`Planning layers unavailable: ${(err as Error).message}`);
      }
    }
    load();
    map.on("moveend", load);
    return () => { map.off("moveend", load); ctrlRef.current?.abort(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, toggles.zones, toggles.ngt_buffer, toggles.forest_symbol, toggles.stream_centreline]);

  return (
    <>
      {toggles.zones && data.zones && (
        <GeoJSON key={`pz-${version}`} data={data.zones} style={zoneStyle} interactive={false} />
      )}
      {OVERLAY_KINDS.map((k) =>
        toggles[k] && data[k] ? (
          <GeoJSON key={`po-${k}-${version}`} data={data[k]!} style={() => OVERLAY_UI[k].style} interactive={false} />
        ) : null,
      )}
    </>
  );
}

function Switch({
  on, label, onClick, isMobile, indent = false,
}: { on: boolean; label: string; onClick: () => void; isMobile: boolean; indent?: boolean }) {
  return (
    <button
      role="switch" aria-checked={on} onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
        padding: isMobile ? "9px 11px" : "6px 10px", paddingLeft: indent ? (isMobile ? 22 : 20) : undefined,
        fontSize: isMobile ? 12 : 11, fontWeight: indent ? 500 : 700, fontFamily: "inherit",
        cursor: "pointer", border: "none", background: "#FDFCFB", color: on ? "#306223" : "#7B8F83",
      }}
    >
      <span style={{
        position: "relative", width: 26, height: 14, borderRadius: 7, flexShrink: 0,
        background: on ? "#306223" : "#CFD6C4", transition: "background 0.15s",
      }}>
        <span style={{
          position: "absolute", top: 2, left: on ? 14 : 2, width: 10, height: 10, borderRadius: 5,
          background: "#FDFCFB", transition: "left 0.15s",
        }} />
      </span>
      <span>{label}</span>
    </button>
  );
}

/** One switch for the RMP 2031 zone layer; the overlay switches appear only while it is on. */
export function PlanningControls({
  toggles, setToggles, isMobile,
}: {
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  isMobile: boolean;
}) {
  const flip = (k: LayerKey) => setToggles({ ...toggles, [k]: !toggles[k] });
  return (
    <div style={{
      position: "absolute", top: 112, right: 10, zIndex: 1000, display: "flex", flexDirection: "column",
      borderRadius: 6, overflow: "hidden", border: "1px solid #CFD6C4", boxShadow: "0 2px 8px rgba(58,63,59,0.14)",
      background: "#FDFCFB",
    }}>
      <Switch on={toggles.zones} label="RMP 2031 zones (Draft)" onClick={() => flip("zones")} isMobile={isMobile} />
      {toggles.zones && OVERLAY_KINDS.map((k) => (
        <div key={k} style={{ borderTop: "1px solid #E8EEE4" }}>
          <Switch on={toggles[k]} label={OVERLAY_UI[k].label} onClick={() => flip(k)} isMobile={isMobile} indent />
        </div>
      ))}
    </div>
  );
}

function DraftBadge() {
  return (
    <span style={{
      display: "inline-block", background: "#FFF4E5", color: "#9A4F00", border: "1px solid #F5C58A",
      borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700, whiteSpace: "nowrap",
    }}>{DRAFT_LABEL}</span>
  );
}

/** Legend with native labels and the Draft badge; shown only while the zone switch is on. */
export function PlanningLegend({ toggles, status }: { toggles: PlanningToggles; status: string }) {
  if (!toggles.zones) return null;
  const overlays = OVERLAY_KINDS.filter((k) => toggles[k]);
  return (
    <div style={{
      position: "absolute", left: 10, bottom: 24, zIndex: 1000, maxWidth: 250,
      background: "rgba(253,252,251,0.96)", border: "1px solid #CFD6C4", borderRadius: 8,
      padding: "8px 10px", fontSize: 11, color: "#3A3F3B", boxShadow: "0 2px 8px rgba(58,63,59,0.14)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 800, color: "#306223" }}>BDA RMP 2031</span>
        <DraftBadge />
      </div>
      {PLANNING_LEGEND.map((e) => (
        <div key={e.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
          <span style={{
            width: 12, height: 10, background: e.colour, border: e.classNorm === "uncoloured" ? "1px dashed #9E9E9E" : "1px solid rgba(0,0,0,0.15)",
            ...(e.classNorm === "road_space" ? { background: "#E0E0E0" } : {}),
            display: "inline-block", flexShrink: 0,
          }} />
          <span>{e.label}</span>
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
        <span style={{ width: 12, height: 10, border: "1px dashed #8E24AA", display: "inline-block" }} />
        <span>Zone inferred under a map symbol</span>
      </div>
      {overlays.map((k) => (
        <div key={k} style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
          <span style={{
            width: 12, height: k === "stream_centreline" ? 2 : 10, display: "inline-block",
            background: k === "stream_centreline" ? "#0084a8" : "transparent",
            border: k === "stream_centreline" ? "none" : `1px dashed ${OVERLAY_UI[k].style.color}`,
          }} />
          <span>{OVERLAY_UI[k].legend}</span>
        </div>
      ))}
      {status && <div style={{ marginTop: 6, color: "#9A4F00", fontWeight: 600 }}>{status}</div>}
    </div>
  );
}

function hitColour(label: string): string {
  return COLOUR_BY_LABEL.get(label) ?? "#999999";
}

/** Parcel-card section: facts from /zones/at, no answer or confidence. */
export function PlanningCardSection({ result }: { result: ZonesAtResult | "loading" | { error: string } }) {
  const box = { marginTop: 8, borderTop: "1px solid #E8EEE4", paddingTop: 8 } as const;
  if (result === "loading") return <div style={{ ...box, color: "#7B8F83", fontSize: 11 }}>Loading RMP 2031 zones…</div>;
  if ("error" in result) return <div style={{ ...box, color: "#9A4F00", fontSize: 11 }}>RMP 2031 zones unavailable: {result.error}</div>;
  const near = result.overlays_nearby;
  return (
    <div style={box}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 11, color: "#306223" }}>RMP 2031 zones</span>
        <DraftBadge />
      </div>
      {result.zones.length === 0 && <div style={{ color: "#7B8F83", fontSize: 11 }}>No RMP 2031 zone on this parcel</div>}
      {result.zones.map((z) => (
        <div key={z.zone_label_native} style={{ marginBottom: 4, fontSize: 11 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, background: hitColour(z.zone_label_native), border: "1px solid rgba(0,0,0,0.2)", flexShrink: 0 }} />
            <span style={{ fontWeight: 600 }}>{z.zone_label_native}</span>
            <span style={{ color: "#7B8F83", marginLeft: "auto" }}>{z.overlap_pct.toFixed(1)}%</span>
          </div>
          {z.cartographic && (
            <div style={{ color: "#7B8F83", marginLeft: 16 }}>Road corridor as drawn on the sheet, not a zone decision</div>
          )}
          {z.near_edge && (
            <div style={{ color: "#9A4F00", marginLeft: 16 }}>
              Near a zone edge (within {Math.round(z.position_uncertainty_m)} m map accuracy)
            </div>
          )}
          {z.inferred && (
            <div style={{ color: "#8E24AA", marginLeft: 16 }}>
              Inferred: {z.inferred_notes.join("; ")} ({z.inferred_share_pct.toFixed(0)}% of this part)
            </div>
          )}
        </div>
      ))}
      {result.trace_hits.length > 0 && (
        <div style={{ color: "#7B8F83", fontSize: 10, marginBottom: 4 }}>
          Also touches (under 1%): {result.trace_hits.map((z) => z.zone_label_native).join(", ")}
        </div>
      )}
      {(near.ngt_buffer.length > 0 || near.forest_symbol.length > 0 || near.nearest_stream_centreline) && (
        <div style={{ fontSize: 11, marginTop: 4 }}>
          <div style={{ fontWeight: 700, color: "#306223", marginBottom: 2 }}>Map symbols nearby</div>
          {near.ngt_buffer.map((o) => (
            <div key={o.overlay_uid}>NGT buffer symbol: {o.overlap_pct.toFixed(1)}% of parcel</div>
          ))}
          {near.forest_symbol.map((o) => (
            <div key={o.overlay_uid}>Forest symbol area: {o.overlap_pct.toFixed(1)}% of parcel</div>
          ))}
          {near.nearest_stream_centreline && (
            <div>Stream centreline: {near.nearest_stream_centreline.distance_m.toFixed(0)} m away</div>
          )}
          <div style={{ color: "#7B8F83", fontSize: 10 }}>Drawn map symbols, not measured buffers</div>
        </div>
      )}
      {result.note && <div style={{ color: "#7B8F83", fontSize: 10, marginTop: 5 }}>{result.note}</div>}
    </div>
  );
}
