// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1A — district / taluk / hobli / village cascade + Load.
// Survey search is intentionally absent here; added in Phase 1D once the
// parcel display backend is wired.

"use client";

import { useEffect, useState } from "react";
import {
  fetchDistricts, fetchTaluks, fetchHoblis, fetchVillages, fetchParcelData,
  type HierarchyItem,
} from "@/lib/api/cadastral_records";

interface Props {
  onLoad: (fc: GeoJSON.FeatureCollection | null, label: string) => void;
}

const SEL_STYLE: React.CSSProperties = {
  padding: "3px 6px", border: "1px solid #CFD6C4", borderRadius: 5,
  fontSize: 12, background: "#FDFCFB", cursor: "pointer",
  color: "#3A3F3B", fontFamily: "inherit",
};

const BTN_STYLE: React.CSSProperties = {
  padding: "4px 14px", background: "#306223", color: "#FDFCFB",
  border: "none", borderRadius: 5, fontWeight: 700, fontSize: 12,
  cursor: "pointer", fontFamily: "inherit", letterSpacing: "0.02em",
};

export function CadastralToolbar({ onLoad }: Props) {
  const [districts, setDistricts] = useState<HierarchyItem[]>([]);
  const [taluks, setTaluks]       = useState<HierarchyItem[]>([]);
  const [hoblis, setHoblis]       = useState<HierarchyItem[]>([]);
  const [villages, setVillages]   = useState<HierarchyItem[]>([]);

  const [dist, setDist]   = useState("");
  const [taluk, setTaluk] = useState("");
  const [hobli, setHobli] = useState("");
  const [vlg, setVlg]     = useState("");

  const [loading, setLoading] = useState(false);
  const [status, setStatus]   = useState("");

  useEffect(() => { fetchDistricts().then(setDistricts); }, []);

  function handleDistChange(v: string) {
    setDist(v); setTaluk(""); setHobli(""); setVlg("");
    setTaluks([]); setHoblis([]); setVillages([]);
    if (v) fetchTaluks(v).then(setTaluks);
  }
  function handleTalukChange(v: string) {
    setTaluk(v); setHobli(""); setVlg("");
    setHoblis([]); setVillages([]);
    if (dist && v) fetchHoblis(dist, v).then(setHoblis);
  }
  function handleHobliChange(v: string) {
    setHobli(v); setVlg(""); setVillages([]);
    if (dist && taluk && v) fetchVillages(dist, taluk, v).then(setVillages);
  }

  async function handleLoad() {
    if (!dist) { setStatus("Select a district first"); return; }
    setLoading(true);
    setStatus("Loading…");
    const fc = await fetchParcelData(dist, taluk, hobli, vlg);
    setLoading(false);
    if (!fc) { setStatus("No parcel data"); onLoad(null, ""); return; }
    const n = fc.features?.length ?? 0;
    const label = n > 500
      ? `${n} parcel(s) loaded (hover tooltips hidden above 500)`
      : `${n} parcel(s) loaded`;
    setStatus(label);
    onLoad(fc, label);
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "6px 14px",
      background: "rgba(253,252,251,0.55)",
      backdropFilter: "blur(14px) saturate(160%)",
      WebkitBackdropFilter: "blur(14px) saturate(160%)",
      borderBottom: "1px solid rgba(255,255,255,0.6)",
      flexWrap: "wrap", minHeight: 42,
      boxShadow: "0 6px 26px rgba(58,63,59,0.18), inset 0 1px 0 rgba(255,255,255,0.45)",
    }}>
      <span style={{ color: "#306223", fontWeight: 800, fontSize: 13, whiteSpace: "nowrap", letterSpacing: "0.01em" }}>
        Karnataka Cadastral
      </span>
      <span style={{ color: "#CFD6C4", fontSize: 16 }}>|</span>

      <select value={dist} onChange={(e) => handleDistChange(e.target.value)} style={SEL_STYLE}>
        <option value="">District</option>
        {districts.map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
      </select>

      <select value={taluk} onChange={(e) => handleTalukChange(e.target.value)} style={{ ...SEL_STYLE, opacity: taluks.length ? 1 : 0.45 }}>
        <option value="">{taluks.length ? "Taluk" : dist ? "Loading…" : "— Taluk —"}</option>
        {taluks.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
      </select>

      <select value={hobli} onChange={(e) => handleHobliChange(e.target.value)} style={{ ...SEL_STYLE, opacity: hoblis.length ? 1 : 0.45 }}>
        <option value="">{hoblis.length ? "Hobli" : taluk ? "Loading…" : "— Hobli —"}</option>
        {hoblis.map((h) => <option key={h.code} value={h.code}>{h.name}</option>)}
      </select>

      <select value={vlg} onChange={(e) => setVlg(e.target.value)} style={{ ...SEL_STYLE, opacity: villages.length ? 1 : 0.45 }}>
        <option value="">{villages.length ? "All villages" : hobli ? "Loading…" : "— Village —"}</option>
        {villages.map((v) => <option key={v.code} value={v.code}>{v.name}</option>)}
      </select>

      <button onClick={handleLoad} disabled={loading} style={BTN_STYLE}>
        {loading ? "Loading…" : "Load"}
      </button>

      {status && (
        <span style={{ fontSize: 12, color: "#7B8F83", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {status}
        </span>
      )}
    </div>
  );
}
