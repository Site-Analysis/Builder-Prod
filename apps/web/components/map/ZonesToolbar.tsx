// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Header toolbar of the "Zones" analysis (US-02): place search (pan and zoom to a named area),
// the 2031 plan area / sub-area picker, the plan road layer switch, and "Parcels here" (loads
// the cadastral village under the map centre, so parcels can be clicked for their plan zones
// and roads).
//
// Place search: the plan sub-areas first (instant), then OpenStreetMap geocoding through Photon
// (type-ahead; bounded to Karnataka), with Nominatim as the fallback. No API key needed; only
// the typed text is sent.

"use client";

import { useEffect, useRef, useState } from "react";
import { useIsMobile } from "@/lib/useIsMobile";
import { PLAN_NAMES, WEB_PLANS, type Bbox, type PlanInfo } from "@/lib/api/planning";
import { PLAN_SUBAREAS, type SubArea } from "@/lib/planning/subareas";
import { PlanningAreaPicker, type PlanningToggles } from "./PlanningLayers";
import type { PrebuiltRoads } from "./PrebuiltPlanLayers";

// Karnataka (W, S, E, N): geocoder results are kept inside it
const KA_BBOX: [number, number, number, number] = [74.0, 11.5, 78.6, 18.5];

interface Place {
  key: string;
  label: string;
  detail: string;
  lat: number;
  lon: number;
  bbox?: [number, number, number, number]; // W, S, E, N
  sub?: { plan_id: string; sa: SubArea };
}

function subareaMatches(q: string): Place[] {
  const t = q.trim().toLowerCase();
  if (t.length < 2) return [];
  const out: Place[] = [];
  for (const [plan_id, list] of Object.entries(PLAN_SUBAREAS)) {
    for (const sa of list) {
      if (sa.name.toLowerCase().includes(t)) {
        out.push({
          key: `sa-${plan_id}-${sa.name}`, label: sa.name, detail: `${PLAN_NAMES[plan_id] ?? plan_id} · sub-area`,
          lat: sa.lat, lon: sa.lng, sub: { plan_id, sa },
        });
      }
    }
  }
  return out.slice(0, 5);
}

async function photon(q: string, signal: AbortSignal): Promise<Place[]> {
  const u = `https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=7&lang=en&bbox=${KA_BBOX.join(",")}`;
  const r = await fetch(u, { signal });
  if (!r.ok) throw new Error(`photon ${r.status}`);
  const j = (await r.json()) as { features: { geometry: { coordinates: [number, number] }; properties: Record<string, unknown> }[] };
  return j.features.map((f, i) => {
    const p = f.properties;
    const ext = p.extent as [number, number, number, number] | undefined; // W, N, E, S
    const detail = [p.district, p.city, p.county, p.state].filter((x, k, a) => x && a.indexOf(x) === k && x !== p.name).join(", ");
    return {
      key: `ph-${i}-${String(p.osm_id ?? i)}`, label: String(p.name ?? q), detail: String(detail || p.type || ""),
      lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0],
      bbox: ext ? [ext[0], ext[3], ext[2], ext[1]] : undefined,
    };
  });
}

async function nominatim(q: string, signal: AbortSignal): Promise<Place[]> {
  const [w, s, e, n] = KA_BBOX;
  const u = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=7&countrycodes=in&bounded=1&viewbox=${w},${n},${e},${s}&q=${encodeURIComponent(q)}`;
  const r = await fetch(u, { signal, headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`nominatim ${r.status}`);
  const j = (await r.json()) as { display_name: string; name?: string; lat: string; lon: string; boundingbox?: string[]; place_id: number }[];
  return j.map((x) => {
    const bb = x.boundingbox?.map(Number); // S, N, W, E
    return {
      key: `nm-${x.place_id}`, label: x.name || x.display_name.split(",")[0], detail: x.display_name.split(",").slice(1, 4).join(",").trim(),
      lat: Number(x.lat), lon: Number(x.lon), bbox: bb ? [bb[2], bb[0], bb[3], bb[1]] : undefined,
    };
  });
}

function PlaceSearch({ onPlace, onSubArea }: { onPlace: (p: Place) => void; onSubArea: (plan_id: string, sa: SubArea) => void }) {
  const { isMobile } = useIsMobile();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const wrap = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const t = q.trim();
    const local = subareaMatches(t);
    setItems(local);
    setErr("");
    if (t.length < 3) { setBusy(false); return; }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        let remote: Place[] = [];
        try {
          remote = await photon(t, ctrl.signal);
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
          remote = await nominatim(t, ctrl.signal);
        }
        setItems([...local, ...remote]);
        if (!local.length && !remote.length) setErr("No place found in Karnataka");
      } catch (e) {
        if ((e as Error).name !== "AbortError") setErr("Place search unavailable");
      } finally {
        setBusy(false);
      }
    }, 350);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [q]);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const pick = (p: Place) => {
    setOpen(false);
    setQ(p.label);
    if (p.sub) onSubArea(p.sub.plan_id, p.sub.sa);
    else onPlace(p);
  };

  return (
    <div ref={wrap} style={{ position: "relative" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "2px 7px", border: "1px solid #CFD6C4", borderRadius: 5, background: "#FDFCFB" }}>
        <svg width="16" height="16" viewBox="0 0 14 14" fill="none" aria-hidden="true" style={{ flexShrink: 0, opacity: 0.85 }}>
          <circle cx="5.5" cy="5.5" r="4" stroke="#306223" strokeWidth="1.5" />
          <line x1="8.7" y1="8.7" x2="13" y2="13" stroke="#306223" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          type="search" aria-label="Search an area or place" placeholder="Search an area or place…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && items.length) pick(items[0]);
            if (e.key === "Escape") setOpen(false);
          }}
          style={{ padding: "1px 0", fontSize: 12, border: "none", background: "transparent", outline: "none", width: isMobile ? 150 : 230, color: "#3A3F3B", fontFamily: "inherit" }}
        />
        {busy && <span style={{ fontSize: 10, color: "#9EAD98" }}>…</span>}
      </div>
      {open && (items.length > 0 || err) && (
        <div role="listbox" style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 9999, background: "#FDFCFB",
          border: "1px solid #CFD6C4", borderRadius: 5, boxShadow: "0 4px 16px rgba(58,63,59,0.14)",
          minWidth: 280, maxWidth: isMobile ? "calc(100vw - 24px)" : 380, maxHeight: 280, overflowY: "auto",
        }}>
          {items.map((p) => (
            <div
              key={p.key} role="option" aria-selected={false}
              onMouseDown={() => pick(p)}
              onMouseEnter={(e) => { e.currentTarget.style.background = "#F0EDE8"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              style={{ padding: "6px 10px", fontSize: 12, cursor: "pointer", color: "#3A3F3B", borderBottom: "1px solid #EEE" }}
            >
              <span style={{ fontWeight: 700 }}>{p.label}</span>
              {p.detail && <span style={{ color: "#7B8F83", marginLeft: 6 }}>{p.detail}</span>}
            </div>
          ))}
          {err && <div style={{ padding: "6px 10px", fontSize: 11, color: "#9A4F00" }}>{err}</div>}
          <div style={{ padding: "3px 10px", fontSize: 9, color: "#9EAD98" }}>Places: © OpenStreetMap contributors</div>
        </div>
      )}
    </div>
  );
}

export function ZonesToolbar({
  plans, toggles, setToggles, roads, onFit, onFly, onPlace, onParcelsHere, parcelsStatus,
}: {
  plans: Record<string, PlanInfo> | null;
  toggles: PlanningToggles;
  setToggles: (t: PlanningToggles) => void;
  roads: Record<string, PrebuiltRoads>;
  onFit: (b: Bbox) => void;
  onFly: (s: SubArea) => void;
  onPlace: (lat: number, lon: number, bbox?: [number, number, number, number]) => void;
  onParcelsHere: () => void;
  parcelsStatus: string;
}) {
  const { isMobile } = useIsMobile();
  const onIds = WEB_PLANS.map((p) => p.plan_id).filter((id) => toggles.plans[id]);
  const roadIds = onIds.filter((id) => roads[id]);
  const roadsOn = roadIds.length > 0 && roadIds.every((id) => toggles.roads?.[id]);
  const chip = (on: boolean): React.CSSProperties => ({
    display: "flex", alignItems: "center", gap: 6, padding: "3px 10px", border: "1px solid #CFD6C4",
    borderRadius: 9999, fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap",
    background: on ? "#306223" : "#FDFCFB", color: on ? "#FDFCFB" : "#306223",
  });
  return (
    <div style={{
      position: "relative", zIndex: 10, display: "flex", alignItems: "center", gap: 8,
      padding: isMobile ? "8px 10px" : "6px 14px", background: "rgba(253,252,251,0.55)",
      backdropFilter: "blur(14px) saturate(160%)", WebkitBackdropFilter: "blur(14px) saturate(160%)",
      borderBottom: "1px solid rgba(255,255,255,0.6)", flexWrap: "wrap", minHeight: isMobile ? 48 : 42,
      boxShadow: "0 6px 26px rgba(58,63,59,0.18), inset 0 1px 0 rgba(255,255,255,0.45)",
    }}>
      <span style={{ color: "#306223", fontWeight: 800, fontSize: 13, whiteSpace: "nowrap" }}>2031 Zones</span>
      <span style={{ color: "#CFD6C4", fontSize: 16 }}>|</span>
      <PlaceSearch
        onPlace={(p) => onPlace(p.lat, p.lon, p.bbox)}
        onSubArea={(plan_id, sa) => {
          if (!toggles.plans[plan_id]) setToggles({ ...toggles, plans: { ...toggles.plans, [plan_id]: true } });
          onFly(sa);
        }}
      />
      <PlanningAreaPicker plans={plans} toggles={toggles} setToggles={setToggles} onFit={onFit} onFly={onFly} />
      {roadIds.length > 0 && (
        <button
          role="switch" aria-checked={roadsOn} aria-label="Plan roads (ROW)"
          onClick={() => {
            const next = { ...toggles.roads };
            for (const id of roadIds) next[id] = !roadsOn;
            setToggles({ ...toggles, roads: next });
          }}
          style={chip(roadsOn)}
        >{roadsOn ? "Roads on" : "Show roads"}</button>
      )}
      <button onClick={onParcelsHere} title="Load the cadastral parcels of the village at the map centre" style={chip(false)}>
        Parcels here
      </button>
      {parcelsStatus && <span style={{ fontSize: 11, color: "#7B8F83" }}>{parcelsStatus}</span>}
    </div>
  );
}
