// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Planning service client (contracts/planning.yaml 1.19.0): 2031 plan zone layers (one per
// indexed plan), RMP 2031 map-symbol overlays, and zones touching a parcel. Every record
// carries its document status. Sheets load on demand: answers list `pending_sheets` until
// every sheet they need is downloaded and extracted.

import { getSession } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

const BASE = process.env.NEXT_PUBLIC_PLANNING_API_URL ?? "http://localhost:8012";
const TIMEOUT_MS = 120_000; // a cold sheet can take a minute (L1); the map retries meanwhile

export const PLAN_ID = "BDA-RMP2031"; // map-symbol overlays exist for this plan only

/** Switch order and labels of the 2031 plans; the switches list only those /plans reports
 * as loaded. */
export const WEB_PLANS: { plan_id: string; label: string }[] = [
  { plan_id: "BDA-RMP2031", label: "BDA RMP 2031" },
  { plan_id: "BMRDA-HSK-MP2031", label: "Hoskote MP 2031" },
  { plan_id: "BMRDA-ANK-MP2031", label: "Anekal MP 2031" },
];

/** Display names for every registered plan (switches use WEB_PLANS; the card names any). */
export const PLAN_NAMES: Record<string, string> = {
  ...Object.fromEntries(WEB_PLANS.map((p) => [p.plan_id, p.label])),
  "BIAAPA-MP2021": "BIAAPA Master Plan 2021",
  "BMICAPA-ODP2004": "BMICAPA ODP 2004",
  "MAGADI-MP2031": "Magadi MP 2031",
  "KPA-MP2031": "Kanakapura MP 2031",
};

/** Badge text for a plan status: "Draft", "Final", or "Final, subject to court case". */
export function statusBadge(status: DocStatus, condition?: string | null): string {
  if (status === "final") return condition ? "Final, subject to court case" : "Final";
  if (status === "draft") return "Draft";
  return status.charAt(0).toUpperCase() + status.slice(1);
}
export const MAX_BBOX_DEG = 0.05;
export const MAX_BBOX_COARSE_DEG = 0.25; // with simplify_m 25 (zoom 10-12, 1.19)

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
  status_condition?: string | null; // e.g. final approval subject to a court case
  inferred_note: string | null;
  source_layer?: SourceLayer;
  sheet?: string | null;
  qa?: SheetQA;
}

/** Sheet-level QA every zone of a sheet carries (subset used by the web). */
export interface SheetQA {
  doc_id: string;
  sheet?: string | null;
  warnings?: string[];
  placement_confirmed?: boolean; // 1.18, default true: false is drawn dashed, "(placement unconfirmed)"
  position_uncertainty_m?: number | null;
}

export type SourceLayer = "detail" | "hobli" | "lpa_map" | "composite";

/** A sheet an answer needs that is not ready yet (1.18). */
export interface PendingSheet {
  sheet: string;
  plan_id: string;
  doc_id: string;
  state: "downloading" | "extracting" | "source_changed" | "failed";
  source?: string | null;
  message: string;
  retry_after_s?: number | null;
}

/** Zone / overlay FeatureCollection as served (1.18 adds build_id and pending_sheets). */
export type LayerCollection<P> = GeoJSON.FeatureCollection<GeoJSON.Geometry, P> & {
  build_id?: string;
  pending_sheets?: PendingSheet[];
};

export interface SheetState {
  plan_id: string;
  doc_id: string;
  sheet: string;
  source_layer: SourceLayer;
  state: "ready" | "not_loaded" | "downloading" | "extracting" | "source_changed" | "failed";
  placement_confirmed: boolean;
  extent?: Bbox;
  source_url?: string | null;
}

export interface PlanInfo {
  plan_id: string;
  name: string;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null;
  go_ref?: string | null;
  go_date?: string | null;
  enabled: boolean;
  loaded?: boolean;
  extent?: Bbox | null; // 1.18
  sheets?: SheetState[]; // 1.18
}

export interface PlanRefInfo {
  plan_id: string;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null;
  coverage: "full" | "partial";
  loaded?: boolean;
}

export interface SourceCheck { source: string; url?: string | null; checked_on: string; finding: string }

/** /authority answer (subset used by the parcel card). */
export interface AuthorityInfo {
  authority: string | null;
  lpa: string | null;
  plan_coverage: PlanCoverage;
  operative_plan: PlanRefInfo | null;
  draft_plans: PlanRefInfo[];
  note: string | null;
  sources_checked: SourceCheck[];
}

/** Coverage status layer (1.19). */
export type CoverageCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, {
  dist: string; taluk: string; hobli: string; vlg: string;
  village_name?: string | null; authority: string | null; plan_coverage: PlanCoverage;
}> & { state: "loading" | "ready" | "unavailable" };

export interface OverlayProperties {
  overlay_uid: string;
  plan_id: string;
  kind: OverlayKind;
  overlay_label_native: string;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null; // e.g. final approval subject to a court case
  note: string;
}

export interface ZoneHit {
  plan_id: string;
  zone_label_native: string;
  class_norm: string | null;
  cartographic?: boolean;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null; // e.g. final approval subject to a court case
  overlap_pct: number;
  edge_distance_m: number;
  position_uncertainty_m: number;
  near_edge: boolean;
  inferred: boolean;
  inferred_share_pct: number;
  inferred_notes: string[];
  source_layer?: SourceLayer;
  sheet?: string | null;
  mixed_source_layers?: boolean;
  sheets_qa?: SheetQA[];
}

export interface OverlayTouch {
  overlay_uid: string;
  kind: OverlayKind;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null; // e.g. final approval subject to a court case
  note: string;
  overlap_pct: number;
}

export interface StreamNearby {
  overlay_uid: string;
  status: DocStatus;
  status_label: string;
  status_condition?: string | null; // e.g. final approval subject to a court case
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
  build_id?: string;
  pending_sheets?: PendingSheet[];
  village_summary?: VillageSummary | null; // 1.18
  disagreement_note?: string | null; // 1.18
  abutting_roads?: AbuttingRoad[] | null; // 1.20 (null: feature.planning.roads off)
}

export type RoadBand = "upto_9" | "over_9_to_12" | "over_12_to_18" | "over_18_to_24" | "over_24";

/** Existing width from the cadastral gap across the road (1.20). */
export interface ExistingWidthEstimate {
  value_m: number;
  tier: RoadBand;
  method: "cadastral_gap" | "plan_drawn"; // plan_drawn: grey band on the plan (1.21)
  confidence: "MEDIUM" | "LOW";
  samples: number;
  iqr_m?: number[] | null;
  note: string;
}

/** A Zonal Regulations row keyed on road width, with its page (1.20). */
export interface ZrRoadRule {
  doc_id: string; table: string; use: string; rule: string; value: string;
  pdf_page: number; printed_page: number;
}

/** A plan road next to the parcel (1.20). */
export interface AbuttingRoad {
  plan_id: string;
  doc_id: string;
  doc_status: "final" | "draft" | "superseded" | "reference";
  road_name: string | null;
  row_m: number;
  status: "to_be_widened" | "proposed" | "existing_row_stated" | "ring_proposed" | "existing_drawn" | "plan_row_stated";
  status_text: string;
  width_source: "label_and_drawn" | "label" | "drawn" | "legend" | "drawn_band";
  drawn_band_m?: number | null; // 1.21: existing road as drawn on the plan
  plan_confidence: "HIGH" | "MEDIUM" | "LOW";
  distance_m: number;
  frontage_m: number;
  widening_area_sqm: number;
  existing_width: ExistingWidthEstimate | null;
  declared_width_m: number | null;
  width_used_m: number | null;
  width_used_source: "declared" | "estimate" | "none";
  tier: RoadBand | null;
  zr_rules: ZrRoadRule[];
  warnings: string[];
}

export const ROAD_BAND_LABEL: Record<RoadBand, string> = {
  upto_9: "up to 9 m",
  over_9_to_12: "over 9 to 12 m",
  over_12_to_18: "over 12 to 18 m",
  over_18_to_24: "over 18 to 24 m",
  over_24: "over 24 m",
};

export type PlanCoverage =
  | "plan_loaded" | "plan_registered_not_loaded" | "lpa_no_zone_map"
  | "authority_no_master_plan" | "no_master_plan_found";

/** Village-table view of the parcel's village (1.18). */
export interface VillageSummary {
  dist: string; taluk: string; hobli: string; vlg: string;
  village_name?: string | null;
  authority: string | null;
  share_pct?: number | null;
  plan_coverage: PlanCoverage;
}

export const COVERAGE_TEXT: Record<PlanCoverage, string> = {
  plan_loaded: "2031 plan zones loaded",
  plan_registered_not_loaded: "Plan registered; its zones are not loaded",
  lpa_no_zone_map: "The plan has no zone map here",
  authority_no_master_plan: "The authority has no master plan",
  no_master_plan_found: "No master plan found",
};

// ─── Calls ───────────────────────────────────────────────────────────────────

export type Bbox = [number, number, number, number]; // minLng, minLat, maxLng, maxLat

function bboxParam(b: Bbox): string {
  return b.map((v) => v.toFixed(6)).join(",");
}

export function fetchPlans(signal?: AbortSignal) {
  return get<PlanInfo[]>("/plans", signal);
}

export function fetchZones(planId: string, bbox: Bbox, simplify: SimplifyM, signal?: AbortSignal) {
  return get<LayerCollection<ZoneProperties>>(
    `/zones?plan_id=${encodeURIComponent(planId)}&bbox=${bboxParam(bbox)}&simplify_m=${simplify}`,
    signal,
  );
}

export function fetchOverlays(bbox: Bbox, kind: OverlayKind, simplify: SimplifyM, signal?: AbortSignal) {
  return get<LayerCollection<OverlayProperties>>(
    `/overlays?plan_id=${PLAN_ID}&bbox=${bboxParam(bbox)}&kind=${kind}&simplify_m=${simplify}`,
    signal,
  );
}

export function fetchZonesAt(
  dist: string, taluk: string, hobli: string, vlg: string, survey: string,
  roadWidthM?: number | null, // 1.20: the user's measured width of the road the parcel fronts
  signal?: AbortSignal,
): Promise<ZonesAtResult> {
  const q = new URLSearchParams({ dist, taluk, hobli, vlg, survey });
  if (roadWidthM && roadWidthM > 0) q.set("road_width_m", String(roadWidthM));
  return get<ZonesAtResult>(`/zones/at?${q.toString()}`, signal);
}

/** The village (and authority) at a point; `village_summary` is null until the service's
 * village outlines have loaded (they start on the first point query, a few minutes). */
export function fetchVillageAt(lat: number, lng: number, signal?: AbortSignal) {
  const q = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  return get<{ village_summary?: VillageSummary | null }>(`/authority?${q.toString()}`, signal);
}

export function fetchVillageAuthority(dist: string, taluk: string, hobli: string, vlg: string, signal?: AbortSignal) {
  const q = new URLSearchParams({ dist, taluk, hobli, vlg });
  return get<AuthorityInfo>(`/authority?${q.toString()}`, signal);
}

export function fetchCoverage(bbox: Bbox, signal?: AbortSignal) {
  return get<CoverageCollection>(`/coverage?bbox=${bboxParam(bbox)}`, signal);
}

/** simplify_m for a map zoom (contract: 2 at >= 16, 8 at 13-15, 25 at <= 12). */
export function simplifyForZoom(zoom: number): SimplifyM {
  if (zoom >= 16) return 2;
  if (zoom >= 13) return 8;
  return 25;
}

/** Split a view box into tiles of at most MAX_BBOX_DEG per side (the service cap). */
export function splitBbox(b: Bbox, deg = MAX_BBOX_DEG): Bbox[] {
  const [x0, y0, x1, y1] = b;
  const nx = Math.ceil((x1 - x0) / deg);
  const ny = Math.ceil((y1 - y0) / deg);
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
