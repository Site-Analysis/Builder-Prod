// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// 2031 plan zone layers (one sub-switch per plan, each with its status badge), RMP 2031
// map-symbol overlays, legend and the parcel-card section. Rendered only when
// NEXT_PUBLIC_ENABLE_PLANNING_LAYERS is on. Facts only: no answer or confidence wording.

"use client";

import { useEffect, useRef, useState } from "react";
import { GeoJSON, useMap } from "react-leaflet";
import type { PathOptions } from "leaflet";
import {
  PLAN_ID, WEB_PLANS, fetchOverlays, fetchPlans, fetchZones, simplifyForZoom, splitBbox, statusBadge,
  type Bbox, type DocStatus, type OverlayKind, type PlanInfo, type ZoneHit, type ZonesAtResult,
  type ZoneProperties,
} from "@/lib/api/planning";
import { PLANNING_LEGEND } from "@/lib/planning/legend.generated";

const MAX_TILES = 4; // 4 service boxes of 0.05 deg; beyond that ask the user to zoom in
const COLOUR_BY_LABEL = new Map(PLANNING_LEGEND.map((e) => [e.label, e.colour]));
// LPA plans: one colour per normalised class (their native labels differ per plan)
const CLASS_COLOUR: Record<string, { colour: string; label: string }> = {
  residential: { colour: "#FFF34D", label: "Residential" },
  commercial: { colour: "#2F7FE0", label: "Commercial" },
  industrial: { colour: "#A020C0", label: "Industrial" },
  public_semi_public: { colour: "#F03020", label: "Public & semi-public" },
  open_space: { colour: "#7ACB4A", label: "Park & open space" },
  public_utility: { colour: "#FFA500", label: "Public utility" },
  transport: { colour: "#A0A0A0", label: "Transportation" },
  unclassified: { colour: "#E1E1E1", label: "Unclassified" },
  agriculture: { colour: "#D4FCC0", label: "Agriculture" },
  water: { colour: "#98DCF0", label: "Water body" },
  forest: { colour: "#3E8E2E", label: "Forest" },
  hillock: { colour: "#959899", label: "Hillocks / quarries" },
  uncoloured: { colour: "#FFFFFF", label: "Not coloured on the plan" },
};

export interface PlanningToggles {
  zones: boolean; // master switch: nothing is drawn while it is off
  plans: Record<string, boolean>; // one per plan_id
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
const STORAGE_KEY = "planning.toggles.v2";
const NO_PLANS = Object.fromEntries(WEB_PLANS.map((p) => [p.plan_id, false]));
const ALL_OFF: PlanningToggles = {
  zones: false, plans: { ...NO_PLANS }, ngt_buffer: false, forest_symbol: false, stream_centreline: false,
};

/** Saved switch state; all off when nothing is saved or storage is unavailable. */
export function loadPlanningToggles(): PlanningToggles {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return ALL_OFF;
    const saved = JSON.parse(raw) as Partial<PlanningToggles>;
    const plans = { ...NO_PLANS };
    for (const p of WEB_PLANS) plans[p.plan_id] = saved.plans?.[p.plan_id] === true;
    return {
      zones: saved.zones === true,
      plans,
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

/** The master switch gates everything; RMP 2031 overlays need the BDA plan switch too. */
export function effectiveToggles(t: PlanningToggles): PlanningToggles {
  if (!t.zones) return ALL_OFF;
  const bda = t.plans[PLAN_ID] === true;
  return {
    ...t,
    ngt_buffer: bda && t.ngt_buffer,
    forest_symbol: bda && t.forest_symbol,
    stream_centreline: bda && t.stream_centreline,
  };
}

function zoneColour(p: ZoneProperties): string {
  if (p.plan_id === PLAN_ID) return COLOUR_BY_LABEL.get(p.zone_label_native) ?? "#999999";
  return CLASS_COLOUR[p.class_norm ?? ""]?.colour ?? "#999999";
}

function zoneStyle(f?: GeoJSON.Feature): PathOptions {
  const p = (f?.properties ?? {}) as ZoneProperties;
  const colour = zoneColour(p);
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

function dedupe(parts: GeoJSON.FeatureCollection[]): GeoJSON.FeatureCollection {
  const seen = new Set<string>();
  const features: GeoJSON.Feature[] = [];
  for (const fc of parts) {
    for (const f of fc.features) {
      const p = f.properties as unknown as Record<string, string>;
      const uid = p.zone_uid ?? p.overlay_uid;
      if (!seen.has(uid)) { seen.add(uid); features.push(f); }
    }
  }
  return { type: "FeatureCollection", features };
}

type LayerData = { zones: Record<string, GeoJSON.FeatureCollection> } & Partial<Record<OverlayKind, GeoJSON.FeatureCollection>>;

/** Map layers; mounts inside <MapContainer>. */
export function PlanningMapLayers({
  toggles, onStatus,
}: {
  toggles: PlanningToggles;
  onStatus: (s: string) => void;
}) {
  const map = useMap();
  const [data, setData] = useState<LayerData>({ zones: {} });
  const [version, setVersion] = useState(0);
  const ctrlRef = useRef<AbortController | null>(null);
  const planKey = WEB_PLANS.map((p) => (toggles.plans[p.plan_id] ? "1" : "0")).join("");

  useEffect(() => {
    const plans = toggles.zones ? WEB_PLANS.map((p) => p.plan_id).filter((id) => toggles.plans[id]) : [];
    const overlays = OVERLAY_KINDS.filter((k) => toggles[k]);
    async function load() {
      ctrlRef.current?.abort();
      if (!plans.length && !overlays.length) { setData({ zones: {} }); onStatus(""); return; }
      const tiles = splitBbox(viewBbox(map));
      if (tiles.length > MAX_TILES) {
        setData({ zones: {} });
        onStatus("Zoom in to load the 2031 plan layers");
        return;
      }
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const simplify = simplifyForZoom(map.getZoom());
      onStatus("Loading…");
      try {
        const next: LayerData = { zones: {} };
        for (const id of plans) {
          next.zones[id] = dedupe(await Promise.all(tiles.map((t) => fetchZones(id, t, simplify, ctrl.signal))));
        }
        for (const k of overlays) {
          next[k] = dedupe(await Promise.all(tiles.map((t) => fetchOverlays(t, k, simplify, ctrl.signal))));
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
  }, [map, toggles.zones, planKey, toggles.ngt_buffer, toggles.forest_symbol, toggles.stream_centreline]);

  return (
    <>
      {Object.entries(data.zones).map(([id, fc]) => (
        <GeoJSON key={`pz-${id}-${version}`} data={fc} style={zoneStyle} interactive={false} />
      ))}
      {OVERLAY_KINDS.map((k) =>
        toggles[k] && data[k] ? (
          <GeoJSON key={`po-${k}-${version}`} data={data[k]!} style={() => OVERLAY_UI[k].style} interactive={false} />
        ) : null,
      )}
    </>
  );
}

function StatusBadge({ status, condition }: { status: DocStatus; condition?: string | null }) {
  const text = statusBadge(status, condition);
  const draft = status !== "final";
  return (
    <span title={condition ?? undefined} style={{
      display: "inline-block", whiteSpace: "nowrap", borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700,
      background: draft ? "#FFF4E5" : condition ? "#FFF8E1" : "#E8F5E9",
      color: draft ? "#9A4F00" : condition ? "#8D6E00" : "#2E7D32",
      border: `1px solid ${draft ? "#F5C58A" : condition ? "#E6C85A" : "#A5D6A7"}`,
    }}>{text}</span>
  );
}

function Switch({
  on, label, onClick, isMobile, indent = false, disabled = false, badge,
}: {
  on: boolean; label: string; onClick: () => void; isMobile: boolean; indent?: boolean; disabled?: boolean;
  badge?: React.ReactNode;
}) {
  return (
    <button
      role="switch" aria-checked={on} aria-disabled={disabled} disabled={disabled} onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
        padding: isMobile ? "9px 11px" : "6px 10px", paddingLeft: indent ? (isMobile ? 22 : 20) : undefined,
        fontSize: isMobile ? 12 : 11, fontWeight: indent ? 500 : 700, fontFamily: "inherit",
        cursor: disabled ? "not-allowed" : "pointer", border: "none", background: "#FDFCFB",
        color: disabled ? "#AAB4AC" : on ? "#306223" : "#7B8F83",
      }}
    >
      <span style={{
        position: "relative", width: 26, height: 14, borderRadius: 7, flexShrink: 0,
        background: on && !disabled ? "#306223" : "#CFD6C4", transition: "background 0.15s",
      }}>
        <span style={{
          position: "absolute", top: 2, left: on && !disabled ? 14 : 2, width: 10, height: 10, borderRadius: 5,
          background: "#FDFCFB", transition: "left 0.15s",
        }} />
      </span>
      <span>{label}</span>
      {badge && <span style={{ marginLeft: "auto" }}>{badge}</span>}
    </button>
  );
}

/** Plan registry for the switches (status, condition, loaded); null until fetched. */
function usePlans(): Record<string, PlanInfo> | null {
  const [plans, setPlans] = useState<Record<string, PlanInfo> | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    fetchPlans(ctrl.signal)
      .then((ps) => setPlans(Object.fromEntries(ps.map((p) => [p.plan_id, p]))))
      .catch(() => setPlans({}));
    return () => ctrl.abort();
  }, []);
  return plans;
}

/** Master switch, then one sub-switch per 2031 plan with its status badge (all off by
 * default); the RMP 2031 map-symbol switches sit under the BDA plan. */
export function PlanningControls({
  toggles, setToggles, isMobile,
}: {
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  isMobile: boolean;
}) {
  const plans = usePlans();
  const flip = (k: "zones" | OverlayKind) => setToggles({ ...toggles, [k]: !toggles[k] });
  const flipPlan = (id: string) => setToggles({ ...toggles, plans: { ...toggles.plans, [id]: !toggles.plans[id] } });
  return (
    <div style={{
      position: "absolute", top: 112, right: 10, zIndex: 1000, display: "flex", flexDirection: "column",
      borderRadius: 6, overflow: "hidden", border: "1px solid #CFD6C4", boxShadow: "0 2px 8px rgba(58,63,59,0.14)",
      background: "#FDFCFB", minWidth: isMobile ? 220 : 240,
    }}>
      <Switch on={toggles.zones} label="2031 plan zones" onClick={() => flip("zones")} isMobile={isMobile} />
      {toggles.zones && WEB_PLANS.map((wp) => {
        const info = plans?.[wp.plan_id];
        const notLoaded = info !== undefined && info.loaded === false;
        return (
          <div key={wp.plan_id} style={{ borderTop: "1px solid #E8EEE4" }}>
            <Switch
              on={toggles.plans[wp.plan_id] === true && !notLoaded}
              label={notLoaded ? `${wp.label} (zones not loaded)` : wp.label}
              onClick={() => flipPlan(wp.plan_id)} isMobile={isMobile} indent disabled={notLoaded}
              badge={info ? <StatusBadge status={info.status} condition={info.status_condition} /> : undefined}
            />
            {wp.plan_id === PLAN_ID && toggles.plans[PLAN_ID] && OVERLAY_KINDS.map((k) => (
              <div key={k} style={{ borderTop: "1px solid #F1F4EE", paddingLeft: 12 }}>
                <Switch on={toggles[k]} label={OVERLAY_UI[k].label} onClick={() => flip(k)} isMobile={isMobile} indent />
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Swatch({ colour, dashed = false }: { colour: string; dashed?: boolean }) {
  return (
    <span style={{
      width: 12, height: 10, background: colour, display: "inline-block", flexShrink: 0,
      border: dashed ? "1px dashed #9E9E9E" : "1px solid rgba(0,0,0,0.15)",
    }} />
  );
}

/** Legend per switched-on plan, with its status badge; shown only while the master switch is on. */
export function PlanningLegend({ toggles, status }: { toggles: PlanningToggles; status: string }) {
  const plans = usePlans();
  if (!toggles.zones) return null;
  const on = WEB_PLANS.filter((p) => toggles.plans[p.plan_id]);
  const overlays = OVERLAY_KINDS.filter((k) => toggles[k]);
  const lpaOn = on.some((p) => p.plan_id !== PLAN_ID);
  return (
    <div style={{
      position: "absolute", left: 10, bottom: 24, zIndex: 1000, maxWidth: 260, maxHeight: "50vh", overflowY: "auto",
      background: "rgba(253,252,251,0.96)", border: "1px solid #CFD6C4", borderRadius: 8,
      padding: "8px 10px", fontSize: 11, color: "#3A3F3B", boxShadow: "0 2px 8px rgba(58,63,59,0.14)",
    }}>
      {on.length === 0 && <div style={{ color: "#7B8F83" }}>Switch on a plan to show its zones</div>}
      {on.map((p) => {
        const info = plans?.[p.plan_id];
        return (
          <div key={p.plan_id} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 800, color: "#306223" }}>{p.label}</span>
            {info && <StatusBadge status={info.status} condition={info.status_condition} />}
          </div>
        );
      })}
      {toggles.plans[PLAN_ID] && PLANNING_LEGEND.map((e) => (
        <div key={e.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
          <Swatch colour={e.classNorm === "road_space" ? "#E0E0E0" : e.colour} dashed={e.classNorm === "uncoloured"} />
          <span>{e.label}</span>
        </div>
      ))}
      {lpaOn && (
        <>
          {toggles.plans[PLAN_ID] && <div style={{ fontWeight: 700, marginTop: 6, color: "#306223" }}>LPA plans</div>}
          {Object.entries(CLASS_COLOUR).map(([k, v]) => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
              <Swatch colour={v.colour} dashed={k === "uncoloured"} />
              <span>{v.label}</span>
            </div>
          ))}
        </>
      )}
      {toggles.plans[PLAN_ID] && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
          <span style={{ width: 12, height: 10, border: "1px dashed #8E24AA", display: "inline-block" }} />
          <span>Zone inferred under a map symbol</span>
        </div>
      )}
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

function hitColour(z: ZoneHit): string {
  if (z.plan_id === PLAN_ID) return COLOUR_BY_LABEL.get(z.zone_label_native) ?? "#999999";
  return CLASS_COLOUR[z.class_norm ?? ""]?.colour ?? "#999999";
}

const PLAN_LABEL = new Map(WEB_PLANS.map((p) => [p.plan_id, p.label]));

/** Parcel-card section: every plan hit with its own status and condition; facts only. */
export function PlanningCardSection({ result }: { result: ZonesAtResult | "loading" | { error: string } }) {
  const box = { marginTop: 8, borderTop: "1px solid #E8EEE4", paddingTop: 8 } as const;
  if (result === "loading") return <div style={{ ...box, color: "#7B8F83", fontSize: 11 }}>Loading 2031 plan zones…</div>;
  if ("error" in result) return <div style={{ ...box, color: "#9A4F00", fontSize: 11 }}>2031 plan zones unavailable: {result.error}</div>;
  const near = result.overlays_nearby;
  const byPlan = new Map<string, ZoneHit[]>();
  for (const z of result.zones) byPlan.set(z.plan_id, [...(byPlan.get(z.plan_id) ?? []), z]);
  return (
    <div style={box}>
      <div style={{ fontWeight: 700, fontSize: 11, color: "#306223", marginBottom: 5 }}>2031 plan zones</div>
      {result.zones.length === 0 && <div style={{ color: "#7B8F83", fontSize: 11 }}>No 2031 plan zone on this parcel</div>}
      {[...byPlan.entries()].map(([planId, hits]) => (
        <div key={planId} style={{ marginBottom: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3, flexWrap: "wrap", fontSize: 11 }}>
            <span style={{ fontWeight: 700 }}>{PLAN_LABEL.get(planId) ?? planId}</span>
            <StatusBadge status={hits[0].status} condition={hits[0].status_condition} />
          </div>
          {hits[0].status_condition && (
            <div style={{ color: "#8D6E00", fontSize: 10, marginBottom: 3 }}>{hits[0].status_condition}</div>
          )}
          {hits.map((z) => (
            <div key={`${planId}-${z.zone_label_native}`} style={{ marginBottom: 4, fontSize: 11 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 10, height: 10, background: hitColour(z), border: "1px solid rgba(0,0,0,0.2)", flexShrink: 0 }} />
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
              {z.sheet && (
                <div style={{ color: "#7B8F83", marginLeft: 16, fontSize: 10 }}>
                  {z.sheet}{z.mixed_source_layers ? " (sheets of different scales meet here)" : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
      {result.trace_hits.length > 0 && (
        <div style={{ color: "#7B8F83", fontSize: 10, marginBottom: 4 }}>
          Also touches (under 1%): {result.trace_hits.map((z) => z.zone_label_native).join(", ")}
        </div>
      )}
      {(near.ngt_buffer.length > 0 || near.forest_symbol.length > 0 || near.nearest_stream_centreline) && (
        <div style={{ fontSize: 11, marginTop: 4 }}>
          <div style={{ fontWeight: 700, color: "#306223", marginBottom: 2 }}>Map symbols nearby (RMP 2031)</div>
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
