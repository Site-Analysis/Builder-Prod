// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Phase 1A — district / taluk / hobli / village cascade + parcel GeoJSON fetch.
// Survey search, RCCMS, mutations, overlays added in later phases.

import { getSession, signOut } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

const _parcelCache = new Map<string, GeoJSON.FeatureCollection>();

const BASE = process.env.NEXT_PUBLIC_CADASTRAL_API_URL ?? "https://api.builder.qnit.site/cadastral";

const TIMEOUT_MS = 20_000;

async function getToken(): Promise<string | null> {
  if (process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1") return null;
  for (let i = 0; i < 8; i++) {
    const storeToken = useAuthStore.getState().accessToken;
    if (storeToken) return storeToken;
    const session = await getSession();
    if (session?.accessToken) return session.accessToken as string;
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  // Propagate caller's abort into our controller so both timeout and external cancel work.
  signal?.addEventListener("abort", () => ctrl.abort(), { once: true });
  const token = await getToken();
  const authHeader: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { ...authHeader },
      signal: ctrl.signal,
    });
    if (res.status === 401) {
      signOut({ redirect: true, callbackUrl: "/" });
      throw new Error("Session expired — signing out");
    }
    if (!res.ok) {
      const detail = await res.json().then((b) => b?.detail ?? `HTTP ${res.status}`).catch(() => `HTTP ${res.status}`);
      throw new Error(String(detail));
    }
    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw new Error("Cadastral service timed out");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ─── Types ───────────────────────────────────────────────────────────────────

export interface HierarchyItem { code: string; name: string; }

// ─── Hierarchy cascade ───────────────────────────────────────────────────────

export async function fetchDistricts(signal?: AbortSignal): Promise<HierarchyItem[]> {
  try { return await get<HierarchyItem[]>("/districts", signal); } catch { return []; }
}

export async function fetchTaluks(dist: string, signal?: AbortSignal): Promise<HierarchyItem[]> {
  try { return await get<HierarchyItem[]>(`/taluks?dist=${encodeURIComponent(dist)}`, signal); } catch { return []; }
}

export async function fetchHoblis(dist: string, taluk: string, signal?: AbortSignal): Promise<HierarchyItem[]> {
  try { return await get<HierarchyItem[]>(`/hoblis?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}`, signal); } catch { return []; }
}

export async function fetchVillages(dist: string, taluk: string, hobli: string, signal?: AbortSignal): Promise<HierarchyItem[]> {
  try { return await get<HierarchyItem[]>(`/villages?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}&hobli=${encodeURIComponent(hobli)}`, signal); } catch { return []; }
}

// ─── Survey search ───────────────────────────────────────────────────────────

export interface SearchResult {
  survey_no: string;
  village_name: string;
  dist: string;
  taluk: string;
  hobli: string;
  vlg: string;
}

export async function searchBySurveyNo(q: string, signal?: AbortSignal): Promise<SearchResult[]> {
  if (q.length < 2) return [];
  return get<SearchResult[]>(`/search?q=${encodeURIComponent(q)}`, signal);
}

export type VillageSearchResult = {
  village_name: string;
  dist: string; taluk: string; hobli: string; vlg: string;
  dist_name: string; taluk_name: string;
};

export async function fetchVillageSearch(
  q: string, signal?: AbortSignal,
): Promise<VillageSearchResult[]> {
  try {
    return await get<VillageSearchResult[]>(
      `/village-search?q=${encodeURIComponent(q)}`, signal,
    );
  } catch { return []; }
}

// ─── RTC / RCCMS ownership ───────────────────────────────────────────────────

export interface RtcOwner { survey_no: string; owner_name: string; case_status: string; ack_no: string; }
export interface RtcMutation { mr_number: string; transaction_type: string; survey_numbers: string; status: string; applicant: string; }
export interface RtcData { owners: RtcOwner[]; mutations: RtcMutation[]; }

export async function fetchRtcData(
  dist: string, taluk: string, hobli: string, vlg: string,
  villageCode: string, surveyNo: string,
  signal?: AbortSignal,
): Promise<RtcData | null> {
  try {
    return await get<RtcData>(
      `/rtc?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}&hobli=${encodeURIComponent(hobli)}&vlg=${encodeURIComponent(vlg)}&village_code=${encodeURIComponent(villageCode)}&survey_no=${encodeURIComponent(surveyNo)}`,
      signal,
    );
  } catch { return null; }
}

// ─── Parcel GeoJSON ──────────────────────────────────────────────────────────

export async function fetchParcelData(
  dist: string, taluk: string, hobli: string, vlg: string,
  signal?: AbortSignal,
): Promise<GeoJSON.FeatureCollection | null> {
  const key = `${dist}:${taluk}:${hobli}:${vlg}`;
  const cached = _parcelCache.get(key);
  if (cached) return cached;
  try {
    const data = await get<GeoJSON.FeatureCollection>(
      `/data?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}&hobli=${encodeURIComponent(hobli)}&vlg=${encodeURIComponent(vlg)}`, signal,
    );
    if (data) _parcelCache.set(key, data);
    return data ?? null;
  } catch { return null; }
}

// ─── Village boundary overlays ───────────────────────────────────────────────

export async function fetchVillageBoundary(
  dist: string, taluk: string, hobli: string, vlg: string,
  signal?: AbortSignal,
): Promise<GeoJSON.FeatureCollection | null> {
  try {
    return await get<GeoJSON.FeatureCollection>(
      `/boundary?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}&hobli=${encodeURIComponent(hobli)}&vlg=${encodeURIComponent(vlg)}`, signal,
    );
  } catch { return null; }
}

export async function fetchHobliBoundaries(
  dist: string, taluk: string, hobli: string,
  signal?: AbortSignal,
): Promise<GeoJSON.FeatureCollection | null> {
  try {
    return await get<GeoJSON.FeatureCollection>(
      `/boundaries?dist=${encodeURIComponent(dist)}&taluk=${encodeURIComponent(taluk)}&hobli=${encodeURIComponent(hobli)}`, signal,
    );
  } catch { return null; }
}

export async function fetchNearbyBoundaries(
  lat: number, lng: number, radiusKm: number = 5,
  signal?: AbortSignal,
): Promise<GeoJSON.FeatureCollection | null> {
  try {
    return await get<GeoJSON.FeatureCollection>(
      `/nearby?lat=${lat}&lng=${lng}&radius_km=${radiusKm}`, signal,
    );
  } catch { return null; }
}
