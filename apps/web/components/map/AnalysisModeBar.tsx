// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// First header row: which analysis the map is for. "Cadastral" shows the land-records toolbar;
// "Zones" shows the 2031 plan toolbar (US-02: zones, roads, place search). The choice is kept
// in this browser.

"use client";

import { useIsMobile } from "@/lib/useIsMobile";

export type AnalysisMode = "cadastral" | "zones";

const KEY = "map.analysisMode.v1";

export function loadAnalysisMode(): AnalysisMode {
  try {
    return window.localStorage.getItem(KEY) === "zones" ? "zones" : "cadastral";
  } catch {
    return "cadastral";
  }
}

export function saveAnalysisMode(m: AnalysisMode): void {
  try {
    window.localStorage.setItem(KEY, m);
  } catch {
    // storage blocked: the choice still applies to this page
  }
}

export function AnalysisModeBar({
  mode, setMode, zonesAvailable,
}: {
  mode: AnalysisMode;
  setMode: (m: AnalysisMode) => void;
  zonesAvailable: boolean;
}) {
  const { isMobile } = useIsMobile();
  const btn = (m: AnalysisMode, label: string, disabled = false) => {
    const on = mode === m;
    return (
      <button
        role="tab" aria-selected={on} disabled={disabled}
        onClick={() => setMode(m)}
        style={{
          padding: isMobile ? "6px 14px" : "4px 16px", border: "none", borderRadius: 9999,
          fontSize: 12, fontWeight: 700, fontFamily: "inherit", cursor: disabled ? "not-allowed" : "pointer",
          background: on ? "#306223" : "transparent", color: on ? "#FDFCFB" : disabled ? "#B8C2B4" : "#306223",
          transition: "background 120ms",
        }}
      >{label}</button>
    );
  };
  return (
    <div style={{
      position: "relative", zIndex: 11, display: "flex", alignItems: "center", gap: 10,
      padding: isMobile ? "6px 10px" : "5px 14px", background: "#F3F6F0", borderBottom: "1px solid #E1E8DC",
      flexWrap: "wrap",
    }}>
      <span style={{ fontSize: 12, fontWeight: 700, color: "#3A3F3B" }}>Choose which analysis:</span>
      <div role="tablist" aria-label="Analysis" style={{
        display: "flex", gap: 2, padding: 2, border: "1px solid #CFD6C4", borderRadius: 9999, background: "#FDFCFB",
      }}>
        {btn("cadastral", "Cadastral")}
        {btn("zones", "Zones", !zonesAvailable)}
      </div>
    </div>
  );
}
