// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// 2031 plan layers: the side panel (plan dropdown, per-plan switch / opacity / legend /
// overlays / zoom / sources / sheet states), the map layers and the
// parcel-card section. Rendered only when NEXT_PUBLIC_ENABLE_PLANNING_LAYERS is on. Facts
// only: conditional wording ("The plan shows..."), never an answer or a confidence.

"use client";

import { useEffect, useRef, useState } from "react";
import { GeoJSON, Rectangle, useMap } from "react-leaflet";
import type { PathOptions } from "leaflet";
import {
  MAX_BBOX_COARSE_DEG, PLAN_ID, PLAN_NAMES, WEB_PLANS, fetchCoverage, fetchOverlays, fetchPlans,
  fetchVillageAuthority, fetchZones, simplifyForZoom, splitBbox, statusBadge,
  type AuthorityInfo, type Bbox, type CoverageCollection, type DocStatus, type OverlayKind,
  type PendingSheet, type PlanCoverage, type PlanInfo, type ZoneHit, type ZonesAtResult,
  type ZoneProperties,
} from "@/lib/api/planning";
import { PLANNING_LEGEND } from "@/lib/planning/legend.generated";
import { PLAN_SUBAREAS, type SubArea } from "@/lib/planning/subareas";
import { PrebuiltPlanLayer, type PrebuiltManifest } from "./PrebuiltPlanLayers";

const MIN_ZOOM = 10; // L2: plan layers show from zoom 10 (0.25 deg boxes at simplify 25)
const MAX_TILES = 16; // service boxes per plan per view, after dropping those off the plan
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

// Coverage status layer: one colour and one plain-language line per plan_coverage value
export const COVERAGE_UI: Record<PlanCoverage, { colour: string; legend: string }> = {
  plan_loaded: { colour: "#2E7D32", legend: "A 2031 plan's zones are on the map" },
  plan_registered_not_loaded: { colour: "#1E88E5", legend: "A plan covers it; its zone map isn't on Qnit yet" },
  lpa_no_zone_map: { colour: "#F9A825", legend: "The plan has no zone map for this village" },
  authority_no_master_plan: { colour: "#8D6E63", legend: "Planning authority, but no master plan" },
  no_master_plan_found: { colour: "#E57373", legend: "No authority or plan found" },
};

export interface PlanningToggles {
  zones: boolean; // derived: any plan switched on (kept for the parcel layer)
  plans: Record<string, boolean>; // one per plan_id
  opacity: Record<string, number>; // 0.1-1 per plan
  ngt_buffer: boolean;
  forest_symbol: boolean;
  stream_centreline: boolean;
  coverage: boolean; // Coverage status layer
  open: boolean; // panel expanded
}

export const OVERLAY_UI: Record<"ngt_buffer" | "forest_symbol" | "stream_centreline", { label: string; legend: string; style: PathOptions }> = {
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

const OVERLAY_KINDS = ["ngt_buffer", "forest_symbol", "stream_centreline"] as const;
const STORAGE_KEY = "planning.toggles.v3";
const NO_PLANS = Object.fromEntries(WEB_PLANS.map((p) => [p.plan_id, false]));
const FULL = Object.fromEntries(WEB_PLANS.map((p) => [p.plan_id, 1]));
const ALL_OFF: PlanningToggles = {
  zones: false, plans: { ...NO_PLANS }, opacity: { ...FULL }, ngt_buffer: false, forest_symbol: false,
  stream_centreline: false, coverage: false, open: false,
};

/** Saved switch state (browser storage only); all off when nothing is saved. */
export function loadPlanningToggles(): PlanningToggles {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return ALL_OFF;
    const saved = JSON.parse(raw) as Partial<PlanningToggles>;
    const plans = { ...NO_PLANS };
    const opacity = { ...FULL };
    for (const p of WEB_PLANS) {
      plans[p.plan_id] = saved.plans?.[p.plan_id] === true;
      const o = saved.opacity?.[p.plan_id];
      if (typeof o === "number" && o >= 0.1 && o <= 1) opacity[p.plan_id] = o;
    }
    return {
      zones: Object.values(plans).some(Boolean), plans, opacity,
      ngt_buffer: saved.ngt_buffer === true, forest_symbol: saved.forest_symbol === true,
      stream_centreline: saved.stream_centreline === true, coverage: false, // layer removed from the panel (Tanmay, 3 Oct)
      open: saved.open === true,
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

/** RMP 2031 overlays need the BDA plan on; `zones` is true when any plan is on. */
export function effectiveToggles(t: PlanningToggles): PlanningToggles {
  const bda = t.plans[PLAN_ID] === true;
  return {
    ...t,
    zones: Object.values(t.plans).some(Boolean),
    ngt_buffer: bda && t.ngt_buffer,
    forest_symbol: bda && t.forest_symbol,
    stream_centreline: bda && t.stream_centreline,
  };
}

function zoneColour(p: { plan_id: string; zone_label_native: string; class_norm: string | null }): string {
  if (p.plan_id === PLAN_ID) return COLOUR_BY_LABEL.get(p.zone_label_native) ?? "#999999";
  return CLASS_COLOUR[p.class_norm ?? ""]?.colour ?? "#999999";
}

function zoneStyle(f: GeoJSON.Feature | undefined, opacity: number): PathOptions {
  const p = (f?.properties ?? {}) as ZoneProperties;
  const colour = zoneColour(p);
  const uncoloured = p.class_norm === "uncoloured";
  if (p.class_norm === "road_space") {
    // cartographic class: road corridors drawn as lines over white on the sheet
    return { color: "#BDBDBD", weight: 0, fillColor: "#E0E0E0", fillOpacity: 0.35 * opacity };
  }
  if (p.qa?.placement_confirmed === false) {
    // placed without an independent check (may be 100 m or more off): dashed dark outline
    return {
      color: "#5D4037", weight: 1.5, dashArray: "6 4", opacity: 0.9,
      fillColor: uncoloured ? "#FFFFFF" : colour, fillOpacity: (uncoloured ? 0.15 : 0.35) * opacity,
    };
  }
  return {
    color: p.inferred_note ? "#8E24AA" : uncoloured ? "#9E9E9E" : colour,
    weight: p.inferred_note ? 1 : 0.5,
    dashArray: p.inferred_note || uncoloured ? "3 3" : undefined,
    opacity: 0.8,
    fillColor: uncoloured ? "#FFFFFF" : colour,
    fillOpacity: (uncoloured ? 0.2 : 0.45) * opacity,
  };
}

function viewBbox(map: ReturnType<typeof useMap>): Bbox {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
}

function meets(a: Bbox, b: Bbox): boolean {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
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

/** What the layers in view say, for the panel: legend entries and sheet warnings per plan. */
export interface PlanningView {
  zoom: number;
  legend: Record<string, { label: string; cnorm: string | null; colour: string }[]>;
  warnings: Record<string, string[]>;
}

type LayerData = { zones: Record<string, GeoJSON.FeatureCollection>; coverage?: CoverageCollection } &
  Partial<Record<OverlayKind, GeoJSON.FeatureCollection>>;

function summarise(zones: Record<string, GeoJSON.FeatureCollection>, zoom: number): PlanningView {
  const legend: PlanningView["legend"] = {};
  const warnings: PlanningView["warnings"] = {};
  for (const [id, fc] of Object.entries(zones)) {
    const seen = new Map<string, { label: string; cnorm: string | null; colour: string }>();
    const w = new Set<string>();
    for (const f of fc.features) {
      const p = f.properties as ZoneProperties;
      if (!seen.has(p.zone_label_native)) {
        seen.set(p.zone_label_native, { label: p.zone_label_native, cnorm: p.class_norm, colour: zoneColour(p) });
      }
      for (const x of p.qa?.warnings ?? []) w.add(x);
    }
    legend[id] = [...seen.values()].sort((a, b) => a.label.localeCompare(b.label));
    warnings[id] = [...w];
  }
  return { zoom, legend, warnings };
}

/** Map layers; mounts inside <MapContainer>. */
export function PlanningMapLayers({
  toggles, plans, prebuilt, onStatus, onView,
}: {
  toggles: PlanningToggles;
  plans: Record<string, PlanInfo> | null;
  prebuilt: PrebuiltManifest; // plans drawn from pre-built tiles (no /zones calls)
  onStatus: (s: string) => void;
  onView: (v: PlanningView) => void;
}) {
  const map = useMap();
  const [data, setData] = useState<LayerData>({ zones: {} });
  const [version, setVersion] = useState(0);
  const ctrlRef = useRef<AbortController | null>(null);
  const planKey = WEB_PLANS.map((p) => (toggles.plans[p.plan_id] ? "1" : "0")).join("");
  const extentKey = plans ? Object.keys(plans).length : 0;
  const preKey = Object.keys(prebuilt).sort().join(",");

  useEffect(() => {
    const on = WEB_PLANS.map((p) => p.plan_id).filter((id) => toggles.plans[id]);
    const pre = on.filter((id) => prebuilt[id]);
    const ids = on.filter((id) => !prebuilt[id]); // the rest load on demand from the service
    // pre-drawn plans: legend and sheet warnings come from the tile manifest
    const withPre = (v: PlanningView): PlanningView => ({
      zoom: v.zoom,
      legend: { ...v.legend, ...Object.fromEntries(pre.map((id) => [id, prebuilt[id].legend])) },
      warnings: { ...v.warnings, ...Object.fromEntries(pre.map((id) => [id, prebuilt[id].warnings])) },
    });
    const overlays = OVERLAY_KINDS.filter((k) => toggles[k]);
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    // sheets load on demand: draw what is ready, say which sheets are still coming, and ask
    // again with backoff (5 s doubling to 60 s) until nothing is pending
    function again(seconds: number, status: string) {
      const wait = Math.min(60, Math.max(5, seconds) * 2 ** Math.min(attempt, 4));
      attempt += 1;
      onStatus(`${status} (checking again in ${Math.round(wait)} s)`);
      retry = setTimeout(() => load(false), wait * 1000);
    }
    async function load(fresh = true) {
      ctrlRef.current?.abort();
      if (retry) { clearTimeout(retry); retry = null; }
      if (fresh) attempt = 0;
      const zoom = map.getZoom();
      if (!ids.length && !overlays.length && !toggles.coverage) {
        setData({ zones: {} });
        onStatus(pre.length && zoom < MIN_ZOOM ? "Zoom in to level 10 or closer to see the 2031 plan layers" : "");
        onView(withPre({ zoom, legend: {}, warnings: {} }));
        return;
      }
      if (zoom < MIN_ZOOM) {
        setData({ zones: {} });
        onStatus("Zoom in to level 10 or closer to see the 2031 plan layers");
        onView(withPre({ zoom, legend: {}, warnings: {} }));
        return;
      }
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const simplify = simplifyForZoom(zoom);
      const view = viewBbox(map);
      const tiles = splitBbox(view, simplify === 25 ? MAX_BBOX_COARSE_DEG : undefined);
      if (fresh) onStatus("Loading…");
      try {
        const next: LayerData = { zones: {} };
        const pending: PendingSheet[] = [];
        let tooMany = false;
        for (const id of ids) {
          const ext = plans?.[id]?.extent;
          const mine = ext ? tiles.filter((t) => meets(t, ext)) : tiles;
          if (mine.length > MAX_TILES) { tooMany = true; continue; }
          const parts = await Promise.all(mine.map((t) => fetchZones(id, t, simplify, ctrl.signal)));
          parts.forEach((p) => pending.push(...(p.pending_sheets ?? [])));
          next.zones[id] = dedupe(parts);
        }
        for (const k of overlays) {
          if (tiles.length > MAX_TILES) { tooMany = true; continue; }
          const parts = await Promise.all(tiles.map((t) => fetchOverlays(t, k, simplify, ctrl.signal)));
          parts.forEach((p) => pending.push(...(p.pending_sheets ?? [])));
          next[k] = dedupe(parts);
        }
        let coverageLoading = false;
        if (toggles.coverage) {
          const parts = await Promise.all(splitBbox(view, 0.5).slice(0, 9).map((t) => fetchCoverage(t, ctrl.signal)));
          coverageLoading = parts.some((p) => p.state === "loading");
          const seen = new Set<string>();
          const feats = parts.flatMap((p) => p.features).filter((f) => {
            const k = `${f.properties.dist}/${f.properties.taluk}/${f.properties.hobli}/${f.properties.vlg}`;
            if (seen.has(k)) return false;
            seen.add(k); return true;
          });
          next.coverage = { type: "FeatureCollection", features: feats, state: coverageLoading ? "loading" : "ready" };
        }
        if (ctrl.signal.aborted) return;
        setData(next);
        setVersion((v) => v + 1);
        onView(withPre(summarise(next.zones, zoom)));
        const uniq = [...new Map(pending.map((p) => [`${p.plan_id}|${p.doc_id}|${p.sheet}`, p])).values()];
        const extra = [
          tooMany ? "Zoom in to load every plan in this view" : "",
          coverageLoading ? "Village outlines for the coverage layer are loading" : "",
        ].filter(Boolean);
        if (uniq.length === 0 && !coverageLoading) { attempt = 0; onStatus(extra.join(" · ")); return; }
        const waiting = uniq.filter((p) => p.state !== "source_changed");
        const msg = [...uniq.map((p) => p.message), ...extra].join(" · ");
        if (waiting.length === 0 && !coverageLoading) { onStatus(msg); return; }
        again(Math.max(5, ...waiting.map((p) => p.retry_after_s ?? 5)), msg);
      } catch {
        // a timeout or a dropped connection while sheets load: never show the raw error
        if (!ctrl.signal.aborted) again(5, "Planning service is busy loading plan sheets");
      }
    }
    const onMove = () => load(true);
    load();
    map.on("moveend", onMove);
    return () => {
      map.off("moveend", onMove);
      if (retry) clearTimeout(retry);
      ctrlRef.current?.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, planKey, extentKey, preKey, toggles.ngt_buffer, toggles.forest_symbol, toggles.stream_centreline, toggles.coverage]);

  const unconfirmed = WEB_PLANS.filter((p) => toggles.plans[p.plan_id] && !NO_BOX_PLANS.has(p.plan_id)).flatMap((p) =>
    (plans?.[p.plan_id]?.sheets ?? []).filter((sh) => !sh.placement_confirmed && sh.extent),
  );
  return (
    <>
      {unconfirmed.map((sh) => (
        <Rectangle
          key={`unconf-${sh.plan_id}-${sh.sheet}`}
          bounds={[[sh.extent![1], sh.extent![0]], [sh.extent![3], sh.extent![2]]]}
          interactive={false}
          pathOptions={{ color: "#5D4037", weight: 2, dashArray: "8 6", fill: false, opacity: 0.85 }}
        />
      ))}
      {data.coverage && toggles.coverage && (
        <GeoJSON
          key={`pcov-${version}`} data={data.coverage} interactive={false}
          style={(f) => {
            const c = COVERAGE_UI[(f?.properties as { plan_coverage: PlanCoverage }).plan_coverage];
            return { color: c?.colour ?? "#999", weight: 1, opacity: 0.9, fillColor: c?.colour ?? "#999", fillOpacity: 0.25 };
          }}
        />
      )}
      {WEB_PLANS.filter((p) => toggles.plans[p.plan_id] && prebuilt[p.plan_id]).map((p) => (
        <PrebuiltPlanLayer key={`pre-${p.plan_id}`} plan={prebuilt[p.plan_id]} opacity={toggles.opacity[p.plan_id] ?? 1} />
      ))}
      {Object.entries(data.zones).map(([id, fc]) => (
        <GeoJSON
          key={`pz-${id}-${version}-${toggles.opacity[id] ?? 1}`} data={fc} interactive={false}
          style={(f) => zoneStyle(f, toggles.opacity[id] ?? 1)}
        />
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
  on, label, onClick, disabled = false, badge,
}: {
  on: boolean; label: string; onClick: () => void; disabled?: boolean; badge?: React.ReactNode;
}) {
  return (
    <button
      role="switch" aria-checked={on} aria-label={label} aria-disabled={disabled} disabled={disabled} onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", padding: "6px 4px",
        fontSize: 11, fontWeight: 600, fontFamily: "inherit", cursor: disabled ? "not-allowed" : "pointer",
        border: "none", background: "transparent", color: disabled ? "#AAB4AC" : on ? "#306223" : "#5B6B60",
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

/** Plan registry with sheet states; polls every 15 s while a sheet of an indexed plan is
 * loading. null until fetched. */
export function usePlanningPlans(active: boolean): Record<string, PlanInfo> | null {
  const [plans, setPlans] = useState<Record<string, PlanInfo> | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = () => {
      fetchPlans(ctrl.signal)
        .then((ps) => {
          setPlans(Object.fromEntries(ps.map((p) => [p.plan_id, p])));
          const busy = ps.some((p) => (p.sheets ?? []).some((s) => s.state === "downloading" || s.state === "extracting"));
          if (active && busy) timer = setTimeout(load, 15000);
        })
        .catch(() => setPlans((p) => p ?? {}));
    };
    load();
    return () => { ctrl.abort(); if (timer) clearTimeout(timer); };
  }, [active]);
  return plans;
}

function Swatch({ colour, dashed = false }: { colour: string; dashed?: boolean }) {
  return (
    <span style={{
      width: 12, height: 10, background: colour, display: "inline-block", flexShrink: 0,
      border: dashed ? "1px dashed #9E9E9E" : "1px solid rgba(0,0,0,0.15)",
    }} />
  );
}

const SHEET_STATE: Record<string, string> = {
  ready: "ready", not_loaded: "not loaded yet", downloading: "downloading", extracting: "extracting",
  source_changed: "Source changed; needs re-indexing", failed: "failed (retrying)",
};

function PlanSection({
  plan, info, toggles, setToggles, view, onZoomTo, onShow, onFly,
}: {
  plan: { plan_id: string; label: string };
  info: PlanInfo | undefined;
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  view: PlanningView | null;
  onZoomTo: (b: Bbox) => void;
  onShow: (b: Bbox) => void;
  onFly: (s: SubArea) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = plan.plan_id;
  const on = toggles.plans[id] === true;
  const sheets = info?.sheets ?? [];
  const counts = sheets.reduce<Record<string, number>>((a, s) => { a[s.state] = (a[s.state] ?? 0) + 1; return a; }, {});
  const unconfirmed = sheets.filter((s) => !s.placement_confirmed);
  const docs = [...new Map(sheets.map((s) => [s.doc_id, s.source_url])).entries()];
  const legend = view?.legend[id] ?? [];
  const warnings = view?.warnings[id] ?? [];
  const set = (patch: Partial<PlanningToggles>) => setToggles({ ...toggles, ...patch });
  return (
    <div style={{ borderTop: "1px solid #E8EEE4", padding: "4px 0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button
          aria-expanded={expanded} aria-label={`${plan.label} details`} onClick={() => setExpanded(!expanded)}
          style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 11, width: 18, color: "#5B6B60" }}
        >{expanded ? "▾" : "▸"}</button>
        <div style={{ flex: 1 }}>
          <Switch
            on={on} label={plan.label}
            onClick={() => {
              set({ plans: { ...toggles.plans, [id]: !on } });
              // switching a plan on brings it into view at a zoom where its zones load
              if (!on && info?.extent) onShow(info.extent);
            }}
            badge={info ? <StatusBadge status={info.status} condition={info.status_condition} /> : undefined}
          />
        </div>
      </div>
      {expanded && (
        <div style={{ paddingLeft: 22, fontSize: 11, color: "#3A3F3B" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, margin: "4px 0" }}>
            <span>Opacity</span>
            <input
              type="range" min={0.1} max={1} step={0.05} aria-label={`${plan.label} opacity`}
              value={toggles.opacity[id] ?? 1}
              onChange={(e) => set({ opacity: { ...toggles.opacity, [id]: Number(e.target.value) } })}
              style={{ flex: 1 }}
            />
          </label>
          {info?.extent && (
            <button
              onClick={() => { if (!on) set({ plans: { ...toggles.plans, [id]: true } }); onZoomTo(info.extent!); }}
              style={{ fontSize: 11, border: "1px solid #CFD6C4", borderRadius: 4, background: "#FDFCFB", padding: "2px 8px", cursor: "pointer", marginBottom: 4 }}
            >Zoom to plan</button>
          )}
          {id === PLAN_ID && OVERLAY_KINDS.map((k) => (
            <Switch key={k} on={toggles[k]} label={OVERLAY_UI[k].label} disabled={!on} onClick={() => set({ [k]: !toggles[k] })} />
          ))}
          {(PLAN_SUBAREAS[id] ?? []).length > 0 && (
            <>
              <div style={{ fontWeight: 700, marginTop: 4 }}>Sub-areas</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, margin: "3px 0" }}>
                {(PLAN_SUBAREAS[id] ?? []).map((sa) => (
                  <button
                    key={sa.name}
                    onClick={() => { if (!on) set({ plans: { ...toggles.plans, [id]: true } }); onFly(sa); }}
                    style={{ fontSize: 10, border: "1px solid #CFD6C4", borderRadius: 9999, background: "#FDFCFB", padding: "1px 7px", cursor: "pointer", color: "#306223" }}
                  >{sa.name}</button>
                ))}
              </div>
            </>
          )}
          <div style={{ fontWeight: 700, marginTop: 4 }}>Legend (in view)</div>
          {legend.length === 0 && <div style={{ color: "#7B8F83" }}>{on ? "Nothing of this plan in view" : "Switch the plan on"}</div>}
          {legend.map((e) => (
            <div key={e.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
              <Swatch colour={e.colour} dashed={e.cnorm === "uncoloured"} />
              <span>{e.label}</span>
              {e.cnorm && <span style={{ color: "#7B8F83" }}>· {CLASS_COLOUR[e.cnorm]?.label ?? e.cnorm}</span>}
            </div>
          ))}
          {unconfirmed.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
              <span style={{ width: 12, height: 10, border: "1.5px dashed #5D4037", display: "inline-block" }} />
              <span>(placement unconfirmed): {unconfirmed.map((s) => s.sheet).join(", ")}</span>
            </div>
          )}
          {warnings.map((w) => <div key={w} style={{ color: "#9A4F00", marginTop: 3 }}>{w}</div>)}
          <div style={{ fontWeight: 700, marginTop: 4 }}>Status</div>
          <div>{info?.status_label ?? "—"}{info?.go_ref ? ` · ${info.go_ref}` : ""}{info?.go_date ? ` (${info.go_date})` : ""}</div>
          {info?.status_condition && <div style={{ color: "#8D6E00" }}>{info.status_condition}</div>}
          <div style={{ fontWeight: 700, marginTop: 4 }}>Sources</div>
          {docs.map(([doc, url]) => (
            <div key={doc}>{url ? <a href={url} target="_blank" rel="noreferrer">{doc}</a> : doc}</div>
          ))}
          <div style={{ fontWeight: 700, marginTop: 4 }}>Sheets ({sheets.length})</div>
          <div style={{ color: "#5B6B60" }}>
            {Object.entries(counts).map(([k, n]) => `${n} ${SHEET_STATE[k] ?? k}`).join(" · ") || "—"}
          </div>
          {sheets.filter((s) => s.state !== "ready" && s.state !== "not_loaded").slice(0, 8).map((s) => (
            <div key={`${s.doc_id}-${s.sheet}`} style={{ color: s.state === "source_changed" ? "#C62828" : "#5B6B60" }}>
              {s.sheet}: {SHEET_STATE[s.state] ?? s.state}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Collapsible side panel (right; a bottom sheet on mobile) for every 2031 plan layer and the
 * Everything is off by default; the choice is kept in browser storage. */
export function PlanningPanel({
  toggles, setToggles, status, view, plans, isMobile, onZoomTo, onShow, onFly,
}: {
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  status: string;
  view: PlanningView | null;
  plans: Record<string, PlanInfo> | null;
  isMobile: boolean;
  onZoomTo: (b: Bbox) => void;
  onShow: (b: Bbox) => void; // bring a plan into view only when it is out of view or too far out
  onFly: (s: SubArea) => void;
}) {
  const loaded = plans ? WEB_PLANS.filter((wp) => plans[wp.plan_id]?.loaded === true) : [];
  const onIds = loaded.filter((p) => toggles.plans[p.plan_id]).map((p) => p.plan_id);
  const choice = onIds.length === 0 ? "" : onIds.length === loaded.length && loaded.length > 1 ? "__all" : onIds.length === 1 ? onIds[0] : "__some";
  const pick = (v: string) => {
    const plansOn = { ...NO_PLANS };
    if (v === "__all") for (const p of loaded) plansOn[p.plan_id] = true;
    else if (v && v !== "__some") plansOn[v] = true;
    setToggles({ ...toggles, plans: plansOn });
    const exts = Object.keys(plansOn).filter((k) => plansOn[k]).map((k) => plans?.[k]?.extent).filter(Boolean) as Bbox[];
    if (exts.length) {
      onShow([
        Math.min(...exts.map((e) => e[0])), Math.min(...exts.map((e) => e[1])),
        Math.max(...exts.map((e) => e[2])), Math.max(...exts.map((e) => e[3])),
      ]);
    }
  };
  const box: React.CSSProperties = isMobile
    ? { position: "fixed", left: 0, right: 0, bottom: 0, maxHeight: toggles.open ? "55vh" : undefined, borderRadius: "10px 10px 0 0" }
    : { position: "absolute", top: 112, right: 10, width: 300, maxHeight: "calc(100% - 140px)", borderRadius: 8 };
  return (
    <aside aria-label="2031 plan layers" style={{
      ...box, zIndex: 1000, display: "flex", flexDirection: "column", overflow: "hidden",
      border: "1px solid #CFD6C4", boxShadow: "0 2px 8px rgba(58,63,59,0.14)", background: "rgba(253,252,251,0.98)",
    }}>
      <button
        aria-expanded={toggles.open} onClick={() => setToggles({ ...toggles, open: !toggles.open })}
        style={{
          display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", border: "none", cursor: "pointer",
          background: "#F3F6F0", fontFamily: "inherit", fontSize: 12, fontWeight: 800, color: "#306223", textAlign: "left",
        }}
      >
        <span>2031 plan layers</span>
        {onIds.length > 0 && <span style={{ fontWeight: 600, color: "#5B6B60" }}>· {onIds.length} on</span>}
        <span style={{ marginLeft: "auto" }}>{toggles.open ? "▾" : "▸"}</span>
      </button>
      {status && (
        <div role="status" style={{ padding: "4px 10px", fontSize: 11, fontWeight: 600, color: "#7A4F00", background: "#FFF8E1", borderTop: "1px solid #F1E3B0" }}>
          {status}
        </div>
      )}
      {toggles.open && (
        <div style={{ overflowY: "auto", padding: "6px 10px 10px" }}>
          <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: "#3A3F3B" }}>
            Show plan
            <select
              aria-label="Show plan" value={choice} onChange={(e) => pick(e.target.value)}
              style={{ display: "block", width: "100%", marginTop: 3, fontSize: 12, padding: "3px 4px" }}
            >
              <option value="">None</option>
              {loaded.map((p) => {
                const i = plans?.[p.plan_id];
                return <option key={p.plan_id} value={p.plan_id}>{p.label}{i ? ` (${statusBadge(i.status, i.status_condition)})` : ""}</option>;
              })}
              {loaded.length > 1 && <option value="__all">All plans</option>}
              {choice === "__some" && <option value="__some">Several plans</option>}
            </select>
          </label>
          {plans === null && <div style={{ color: "#7B8F83", fontSize: 11, marginTop: 6 }}>Loading the plan list…</div>}
          {plans !== null && loaded.length === 0 && <div style={{ color: "#7B8F83", fontSize: 11, marginTop: 6 }}>No 2031 plan is loaded on the planning service</div>}
          <div style={{ marginTop: 6 }}>
            {loaded.map((p) => (
              <PlanSection
                key={p.plan_id} plan={p} info={plans?.[p.plan_id]} toggles={toggles} setToggles={setToggles}
                view={view} onZoomTo={onZoomTo} onShow={onShow} onFly={onFly}
              />
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function hitColour(z: ZoneHit): string {
  return zoneColour(z);
}

function planName(id: string): string {
  return PLAN_NAMES[id] ?? id;
}

/** F8: one plain line for what the plans can say about the site (conditional wording). */
function coverageLine(a: AuthorityInfo): string {
  const plan = a.operative_plan ?? a.draft_plans[0] ?? null;
  const name = plan ? planName(plan.plan_id) : null;
  const st = plan ? statusBadge(plan.status, plan.status_condition) : "";
  const auth = a.lpa ?? a.authority ?? "The planning authority";
  switch (a.plan_coverage) {
    case "plan_loaded":
      return name ? `The ${name} (${st}) shows the zones below.` : "A 2031 plan shows the zones below.";
    case "plan_registered_not_loaded":
      return `${name ?? "A plan"} (${st}) covers this site. Its zone map isn't on Qnit yet.`;
    case "lpa_no_zone_map":
      return `${auth} covers this site, but its plan has no zone map here.`;
    case "authority_no_master_plan":
      return `${auth} covers this site. No master plan has been found for it.`;
    default:
      return `No planning authority or plan was found for this site after checking: ${
        a.sources_checked.map((s) => s.source).join("; ") || "the registered sources"}.`;
  }
}

/** Parcel-card section: the coverage line, then every plan hit grouped per plan with its own
 * status, condition, warnings and markers; facts only. */
export function PlanningCardSection({ result }: { result: ZonesAtResult | "loading" | { error: string } }) {
  const [auth, setAuth] = useState<AuthorityInfo | null>(null);
  const p = typeof result === "object" && "parcel" in result ? (result.parcel as unknown as Record<string, string>) : null;
  const key = p ? `${p.dist}/${p.taluk}/${p.hobli}/${p.vlg}` : "";
  useEffect(() => {
    if (!p) { setAuth(null); return; }
    const ctrl = new AbortController();
    fetchVillageAuthority(p.dist, p.taluk, p.hobli, p.vlg, ctrl.signal).then(setAuth).catch(() => setAuth(null));
    return () => ctrl.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const box = { marginTop: 8, borderTop: "1px solid #E8EEE4", paddingTop: 8 } as const;
  if (result === "loading") return <div style={{ ...box, color: "#7B8F83", fontSize: 11 }}>Loading 2031 plan zones…</div>;
  if ("error" in result) return <div style={{ ...box, color: "#9A4F00", fontSize: 11 }}>2031 plan zones unavailable: {result.error}</div>;
  const near = result.overlays_nearby;
  const byPlan = new Map<string, ZoneHit[]>();
  for (const z of result.zones) byPlan.set(z.plan_id, [...(byPlan.get(z.plan_id) ?? []), z]);
  // the parcel's own geometry answer first: a zone hit means plan_loaded for that plan
  const line = byPlan.size > 0
    ? [...byPlan.entries()].map(([id, hits]) => `The ${planName(id)} (${statusBadge(hits[0].status, hits[0].status_condition)}) shows the zones below.`).join(" ")
    : auth ? coverageLine(auth) : null;
  return (
    <div style={box}>
      <div style={{ fontWeight: 800, fontSize: 12, color: "#306223", marginBottom: 4 }}>2031 plan zones</div>
      {auth && (
        <div style={{ fontSize: 11, marginBottom: 3 }}>
          <span style={{ fontWeight: 700 }}>{auth.lpa ?? auth.authority ?? "No planning authority"}</span>
        </div>
      )}
      {line && <div data-testid="coverage-line" style={{ fontSize: 11, marginBottom: 4 }}>{line}</div>}
      {result.disagreement_note && (
        <div style={{ color: "#9A4F00", fontSize: 10, marginBottom: 4 }}>{result.disagreement_note}</div>
      )}
      {(result.pending_sheets ?? []).length > 0 && (
        <div style={{ color: "#9A4F00", fontSize: 10, marginBottom: 4 }}>
          {(result.pending_sheets ?? []).map((ps) => ps.message).join(" · ")}
        </div>
      )}
      {result.zones.length === 0 && <div style={{ color: "#7B8F83", fontSize: 11 }}>No 2031 plan zone on this parcel</div>}
      {[...byPlan.entries()].map(([planId, hits]) => (
        <div key={planId} style={{ marginBottom: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3, flexWrap: "wrap", fontSize: 11 }}>
            <span style={{ fontWeight: 700 }}>{planName(planId)}</span>
            <StatusBadge status={hits[0].status} condition={hits[0].status_condition} />
          </div>
          {hits[0].status_condition && (
            <div style={{ color: "#8D6E00", fontSize: 10, marginBottom: 3 }}>{hits[0].status_condition}</div>
          )}
          {[...new Set(hits.flatMap((z) => (z.sheets_qa ?? []).flatMap((q) => q.warnings ?? [])))].map((w) => (
            <div key={w} style={{ color: "#9A4F00", fontSize: 10, marginBottom: 3 }}>{w}</div>
          ))}
          {hits.map((z) => (
            <div key={`${planId}-${z.zone_label_native}`} style={{ marginBottom: 4, fontSize: 11 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 10, height: 10, background: hitColour(z), border: "1px solid rgba(0,0,0,0.2)", flexShrink: 0 }} />
                <span style={{ fontWeight: 600 }}>
                  {z.zone_label_native}{z.source_layer === "lpa_map" ? " (coarse map)" : ""}
                  {(z.sheets_qa ?? []).some((q) => q.placement_confirmed === false) ? " (placement unconfirmed)" : ""}
                </span>
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

/** Toolbar picker: an area (a plan whose zones are on the map), then a sub-area (a named
 * place checked in the US-02 layer test). Picking a sub-area flies the map there at street
 * zoom and switches that plan's zones on, so they load; the switch turns them off again. */
export function PlanningAreaPicker({
  plans, toggles, setToggles, onFit, onFly,
}: {
  plans: Record<string, PlanInfo> | null;
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  onFit: (b: Bbox) => void;
  onFly: (s: SubArea) => void;
}) {
  const [area, setArea] = useState("");
  const [sub, setSub] = useState("");
  const loaded = plans ? WEB_PLANS.filter((wp) => plans[wp.plan_id]?.loaded === true) : [];
  const subs = area ? PLAN_SUBAREAS[area] ?? [] : [];
  const on = area ? toggles.plans[area] === true : false;
  const sel: React.CSSProperties = {
    fontSize: 12, padding: "3px 6px", border: "1px solid #CFD6C4", borderRadius: 5, background: "#FDFCFB",
    color: "#3A3F3B", fontFamily: "inherit", maxWidth: 220,
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <select
        aria-label="Plan area" value={area} style={sel}
        onChange={(e) => {
          const id = e.target.value;
          setArea(id); setSub("");
          const ext = plans?.[id]?.extent;
          if (ext) onFit(ext);
        }}
      >
        <option value="">2031 plan area…</option>
        {loaded.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.label}</option>)}
      </select>
      {area && (
        <select
          aria-label="Plan sub-area" value={sub} style={sel}
          onChange={(e) => {
            setSub(e.target.value);
            const sa = subs.find((x) => x.name === e.target.value);
            if (!sa) return;
            setToggles({ ...toggles, plans: { ...toggles.plans, [area]: true } });
            onFly(sa);
          }}
        >
          <option value="">Sub-area…</option>
          {subs.map((sa) => <option key={sa.name} value={sa.name}>{sa.name}</option>)}
        </select>
      )}
      {area && (
        <button
          role="switch" aria-checked={on} aria-label="Show zones of this plan"
          onClick={() => {
            setToggles({ ...toggles, plans: { ...toggles.plans, [area]: !on } });
            if (!on && !sub && plans?.[area]?.extent) onFit(plans[area].extent!);
          }}
          style={{
            display: "flex", alignItems: "center", gap: 6, padding: "3px 9px", border: "1px solid #CFD6C4",
            borderRadius: 9999, fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
            background: on ? "#306223" : "#FDFCFB", color: on ? "#FDFCFB" : "#7B8F83", whiteSpace: "nowrap",
          }}
        >{on ? "Zones on" : "Show zones"}</button>
      )}
    </div>
  );
}

// Plans whose unconfirmed sheet gets no dashed box on the map (Tanmay, 3 Oct: Anekal's one sheet
// covers the whole LPA, so the box only framed the plan); the legend card says it instead (#69)
const NO_BOX_PLANS = new Set(["BMRDA-ANK-MP2031"]);

// Plain-language meaning of each normalised class (what the plan shows, not what may be built)
export const CLASS_MEANING: Record<string, string> = {
  residential: "Housing and residential areas",
  commercial: "Shops, offices, markets",
  industrial: "Factories and industrial estates",
  public_semi_public: "Government offices, schools, hospitals, institutions",
  open_space: "Parks, playgrounds, open spaces",
  public_utility: "Water, power, sewage and other utility works",
  transport: "Roads, railways, bus and truck terminals",
  unclassified: "No use assigned on the plan",
  agriculture: "Farmland: agricultural zone",
  water: "Lakes, tanks and other water bodies",
  forest: "Forest",
  hillock: "Hillocks and quarries",
  uncoloured: "The plan sheet leaves this area blank",
  road_space: "Road space drawn on the sheet (not a zone)",
  ngt_buffer: "NGT buffer around lakes and drains (map symbol)",
  special_development_zone: "Special development zone",
  stream: "Streams and valleys",
};

/** Floating legend (bottom-left): every colour of the plans switched on, with the plan's own
 * label and what it means, plus the keys for unconfirmed placement and uncoloured areas. */
export function PlanningLegendCard({
  toggles, view, plans,
}: {
  toggles: PlanningToggles;
  view: PlanningView | null;
  plans: Record<string, PlanInfo> | null;
}) {
  const [open, setOpen] = useState(true);
  const on = WEB_PLANS.filter((p) => toggles.plans[p.plan_id]);
  if (!on.length) return null;
  const unconf = (id: string) => (plans?.[id]?.sheets ?? []).filter((sh) => !sh.placement_confirmed);
  const anyBox = on.some((p) => !NO_BOX_PLANS.has(p.plan_id) && unconf(p.plan_id).length > 0);
  return (
    <div role="region" aria-label="Zone legend" style={{
      position: "absolute", left: 10, bottom: 24, zIndex: 1000, width: 290, maxHeight: "55vh", overflowY: "auto",
      background: "rgba(253,252,251,0.97)", border: "1px solid #CFD6C4", borderRadius: 8,
      boxShadow: "0 2px 8px rgba(58,63,59,0.14)", fontSize: 11, color: "#3A3F3B",
    }}>
      <button
        aria-expanded={open} onClick={() => setOpen(!open)}
        style={{
          display: "flex", width: "100%", alignItems: "center", gap: 6, padding: "6px 10px", border: "none",
          background: "#F3F6F0", cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 800, color: "#306223",
        }}
      >
        <span>Zone legend</span>
        <span style={{ marginLeft: "auto" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div style={{ padding: "6px 10px 8px" }}>
          {on.map((p) => {
            const info = plans?.[p.plan_id];
            const entries = (view?.legend[p.plan_id] ?? []).filter((e) => e.cnorm !== "uncoloured");
            return (
              <div key={p.plan_id} style={{ marginBottom: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 800 }}>{p.label}</span>
                  {info && <StatusBadge status={info.status} condition={info.status_condition} />}
                </div>
                {unconf(p.plan_id).length > 0 && (
                  <div style={{ color: "#9A4F00", marginBottom: 3 }}>
                    Placement unconfirmed{NO_BOX_PLANS.has(p.plan_id) ? "" : ` (${unconf(p.plan_id).map((sh) => sh.sheet).join(", ")})`}:
                    zones may be 100 m or more off. Verify on site.
                  </div>
                )}
                {entries.length === 0 && <div style={{ color: "#7B8F83" }}>No zones of this plan in view</div>}
                {entries.map((e) => (
                  <div key={e.label} style={{ display: "flex", alignItems: "flex-start", gap: 6, marginBottom: 3 }}>
                    <span style={{ width: 14, height: 11, marginTop: 1, background: e.colour, opacity: 0.75, border: "1px solid rgba(0,0,0,0.2)", flexShrink: 0 }} />
                    <span>
                      <span style={{ fontWeight: 600 }}>{e.label}</span>
                      {e.cnorm && CLASS_MEANING[e.cnorm] && <span style={{ color: "#5B6B60" }}>: {CLASS_MEANING[e.cnorm]}</span>}
                    </span>
                  </div>
                ))}
              </div>
            );
          })}
          <div style={{ borderTop: "1px solid #E8EEE4", paddingTop: 5, color: "#5B6B60" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
              <span style={{ width: 14, height: 11, background: "#FFFFFF", border: "1px dashed #9E9E9E", flexShrink: 0 }} />
              <span>Not coloured on the plan: the sheet leaves it blank</span>
            </div>
            {anyBox && (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 14, height: 11, border: "2px dashed #5D4037", flexShrink: 0 }} />
                <span>Dashed box: placement unconfirmed; zones inside may be 100 m or more off. Verify on site.</span>
              </div>
            )}
            <div style={{ marginTop: 4, fontSize: 10 }}>Colours show what each plan draws; drafts are shown for context only.</div>
          </div>
        </div>
      )}
    </div>
  );
}
