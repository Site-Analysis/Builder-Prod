// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Planning service client (contracts/planning.yaml 1.12.0): RMP 2031 zone and map-symbol
// overlay layers, and zones touching a parcel. Every record carries its document status.

import { getSession } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

const BASE = process.env.NEXT_PUBLIC_PLANNING_API_URL ?? "http://localhost:8012";
const TIMEOUT_MS = 30_000;

export const PLAN_ID = "BDA-RMP2031";
export const MAX_BBOX_DEG = 0.05;

async function getToken(): Promise<string | null> {
  if (process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1") return null;
  const storeToken = useAuthStore.getState().accessToken;
  if (storeToken) return storeToken;
  const session = await getSession();
  return (session?.accessToken as string | undefined) ?? null;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  signal?.addEventListener("abort", () => ctrl.abort(), { once: true });
  const token = await getToken();
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const detail = await res.json().then((b) => b?.detail ?? `HTTP ${res.status}`).catch(() => `HTTP ${res.status}`);
      throw new Error(String(detail));
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ─── Types (contracts/planning.yaml) ─────────────────────────────────────────

export type DocStatus = "final" | "draft" | "superseded" | "reference";
export type SimplifyM = 2 | 8 | 25;
export type OverlayKind = "ngt_buffer" | "forest_symbol" | "stream_centreline";

export interface ZoneProperties {
  zone_uid: string;
  plan_id: string;
  doc_id: string;
  zone_label_native: string;
  class_norm: string | null;
  cartographic?: boolean; // road space: how the sheet is drawn, not a zoning decision
  status: DocStatus;
  status_label: string;
  inferred_note: string | null;
}

export interface OverlayProperties {
  overlay_uid: string;
  plan_id: string;
  kind: OverlayKind;
  overlay_label_native: string;
  status: DocStatus;
  status_label: string;
  note: string;
}

export interface ZoneHit {
  plan_id: string;
  zone_label_native: string;
  class_norm: string | null;
  cartographic?: boolean;
  status: DocStatus;
  status_label: string;
  overlap_pct: number;
  edge_distance_m: number;
  position_uncertainty_m: number;
  near_edge: boolean;
  inferred: boolean;
  inferred_share_pct: number;
  inferred_notes: string[];
}

export interface OverlayTouch {
  overlay_uid: string;
  kind: OverlayKind;
  status: DocStatus;
  status_label: string;
  note: string;
  overlap_pct: number;
}

export interface StreamNearby {
  overlay_uid: string;
  status: DocStatus;
  status_label: string;
  note: string;
  distance_m: number;
}

export interface ZonesAtResult {
  parcel: { survey: string; village_name: string | null; area_sqm: number };
  zones: ZoneHit[];
  trace_hits: ZoneHit[];
  plans_skipped: string[];
  overlays_nearby: {
    ngt_buffer: OverlayTouch[];
    forest_symbol: OverlayTouch[];
    nearest_stream_centreline: StreamNearby | null;
  };
  note: string | null;
}

// ─── Calls ───────────────────────────────────────────────────────────────────

export type Bbox = [number, number, number, number]; // minLng, minLat, maxLng, maxLat

function bboxParam(b: Bbox): string {
  return b.map((v) => v.toFixed(6)).join(",");
}

export function fetchZones(bbox: Bbox, simplify: SimplifyM, signal?: AbortSignal) {
  return get<GeoJSON.FeatureCollection<GeoJSON.Geometry, ZoneProperties>>(
    `/zones?plan_id=${PLAN_ID}&bbox=${bboxParam(bbox)}&simplify_m=${simplify}`,
    signal,
  );
}

export function fetchOverlays(bbox: Bbox, kind: OverlayKind, simplify: SimplifyM, signal?: AbortSignal) {
  return get<GeoJSON.FeatureCollection<GeoJSON.Geometry, OverlayProperties>>(
    `/overlays?plan_id=${PLAN_ID}&bbox=${bboxParam(bbox)}&kind=${kind}&simplify_m=${simplify}`,
    signal,
  );
}

export function fetchZonesAt(
  dist: string, taluk: string, hobli: string, vlg: string, survey: string,
  signal?: AbortSignal,
): Promise<ZonesAtResult> {
  const q = new URLSearchParams({ dist, taluk, hobli, vlg, survey });
  return get<ZonesAtResult>(`/zones/at?${q.toString()}`, signal);
}

/** simplify_m for a map zoom (contract: 2 at >= 16, 8 at 13-15, 25 at <= 12). */
export function simplifyForZoom(zoom: number): SimplifyM {
  if (zoom >= 16) return 2;
  if (zoom >= 13) return 8;
  return 25;
}

/** Split a view box into tiles of at most MAX_BBOX_DEG per side (the service cap). */
export function splitBbox(b: Bbox): Bbox[] {
  const [x0, y0, x1, y1] = b;
  const nx = Math.ceil((x1 - x0) / MAX_BBOX_DEG);
  const ny = Math.ceil((y1 - y0) / MAX_BBOX_DEG);
  const out: Bbox[] = [];
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      out.push([
        x0 + ((x1 - x0) * i) / nx,
        y0 + ((y1 - y0) * j) / ny,
        x0 + ((x1 - x0) * (i + 1)) / nx,
        y0 + ((y1 - y0) * (j + 1)) / ny,
      ]);
    }
  }
  return out;
}
