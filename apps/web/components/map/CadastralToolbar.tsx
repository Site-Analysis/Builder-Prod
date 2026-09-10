// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1D — survey search added (debounced, /search endpoint).

"use client";

import { useEffect, useRef, useState } from "react";
import {
  fetchDistricts, fetchTaluks, fetchHoblis, fetchVillages, fetchParcelData,
  searchBySurveyNo, fetchVillageSearch,
  type HierarchyItem, type SearchResult, type VillageSearchResult,
} from "@/lib/api/cadastral_records";
import { useAuthStore } from "@/lib/stores/auth";
import { useIsMobile } from "@/lib/useIsMobile";

interface VillageCoords { dist: string; taluk: string; hobli: string; vlg: string; }

interface Props {
  onLoad: (fc: GeoJSON.FeatureCollection | null, label: string, hier?: VillageCoords) => void;
  onSearch?: (result: SearchResult) => void;
  onHighlight?: (result: SearchResult) => void;
  onFlyTo?: (coords: { lat: number; lon: number }) => void;
  onLocBounds?: (bbox: [[number, number], [number, number]]) => void;
  onCoordGo?: (coords: { lat: number; lon: number }) => void;
  onVillageSelect?: (hier: VillageCoords) => void;
  autoSelect?: VillageCoords | null;
  autoStatus?: string;
  loadedSurveyNos?: Set<string>;
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

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg"
      style={{ flexShrink: 0, opacity: 0.85 }}>
      <circle cx="5.5" cy="5.5" r="4" stroke="#306223" strokeWidth="1.5"/>
      <line x1="8.7" y1="8.7" x2="13" y2="13" stroke="#306223" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

function SearchDropdown({ results, loadedSurveyNos, loadedVillage, onSelect }: {
  results: SearchResult[];
  loadedSurveyNos?: Set<string>;
  loadedVillage?: VillageCoords;
  onSelect: (r: SearchResult, isCurrent: boolean) => void;
}) {
  const { isMobile } = useIsMobile();
  const inCurrent = loadedSurveyNos?.size
    ? results.filter(r => {
        if (!loadedSurveyNos.has(r.survey_no)) return false;
        if (!loadedVillage) return true;
        if (r.dist !== loadedVillage.dist) return false;
        if (loadedVillage.taluk && r.taluk !== loadedVillage.taluk) return false;
        if (loadedVillage.hobli && r.hobli !== loadedVillage.hobli) return false;
        if (loadedVillage.vlg && r.vlg !== loadedVillage.vlg) return false;
        return true;
      })
    : [];
  const seen = new Set<string>();
  const inCurrentDeduped = inCurrent.filter(r => {
    const key = r.survey_no + r.dist + r.taluk + r.hobli + r.vlg;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const inCurrentKeys = new Set(inCurrentDeduped.map(r => r.survey_no + r.dist + r.taluk + r.hobli + r.vlg));
  const others = results.filter(r => !inCurrentKeys.has(r.survey_no + r.dist + r.taluk + r.hobli + r.vlg));

  function Row({ r, highlight }: { r: SearchResult; highlight: boolean }) {
    const base = highlight ? "rgba(48,98,35,0.07)" : "transparent";
    const hover = highlight ? "rgba(48,98,35,0.14)" : "#F0EDE8";
    return (
      <div
        onMouseDown={() => onSelect(r, highlight)}
        style={{ padding: "6px 10px", fontSize: 12, cursor: "pointer", color: "#3A3F3B", borderBottom: "1px solid #EEE", background: base }}
        onMouseEnter={(e) => { e.currentTarget.style.background = hover; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = base; }}
      >
        <span style={{ fontWeight: 700 }}>{r.survey_no}</span>
        {highlight && (
          <span style={{ fontSize: 10, color: "#306223", fontWeight: 700, marginLeft: 6, background: "rgba(48,98,35,0.12)", padding: "1px 5px", borderRadius: 3 }}>
            current area
          </span>
        )}
        <span style={{ color: "#7B8F83", marginLeft: 6 }}>{r.village_name || `Vlg ${r.vlg}`}</span>
      </div>
    );
  }

  const containerStyle: React.CSSProperties = {
    position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 9999,
    background: "#FDFCFB", border: "1px solid #CFD6C4", borderRadius: 5,
    boxShadow: "0 4px 16px rgba(58,63,59,0.14)",
    minWidth: 220, maxWidth: isMobile ? "calc(100vw - 24px)" : undefined,
    maxHeight: 240, overflowY: "auto",
  };

  if (loadedSurveyNos?.size) {
    if (inCurrentDeduped.length === 0) {
      return (
        <div style={containerStyle}>
          <div style={{ padding: "8px 10px", fontSize: 11, color: "#9EAD98" }}>Not found in loaded area</div>
        </div>
      );
    }
    return (
      <div style={containerStyle}>
        {inCurrentDeduped.map((r, i) => <Row key={i} r={r} highlight={true} />)}
      </div>
    );
  }

  const seenAll = new Set<string>();
  const allDeduped = results.filter(r => {
    const key = r.survey_no + r.dist + r.taluk + r.hobli + r.vlg;
    if (seenAll.has(key)) return false;
    seenAll.add(key);
    return true;
  });
  return (
    <div style={containerStyle}>
      {allDeduped.map((r, i) => <Row key={i} r={r} highlight={false} />)}
    </div>
  );
}

export function CadastralToolbar({ onLoad, onSearch, onHighlight, onFlyTo, onLocBounds, onCoordGo, onVillageSelect, autoSelect, autoStatus, loadedSurveyNos }: Props) {
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
  const [loadedVillage, setLoadedVillage] = useState<VillageCoords | null>(null);
  const { isMobile } = useIsMobile();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [searchQ, setSearchQ]           = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [showResults, setShowResults]   = useState(false);
  const [searchErr, setSearchErr]       = useState("");
  const [coordMode, setCoordMode]       = useState(false);
  const [latQ, setLatQ]                 = useState("");
  const [lonQ, setLonQ]                 = useState("");
  const [locQ, setLocQ]                 = useState("");
  const [locResults, setLocResults]     = useState<VillageSearchResult[]>([]);
  const [locLoading, setLocLoading]     = useState(false);
  const [showLocResults, setShowLocResults] = useState(false);
  const debounceRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const locDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchWrapRef  = useRef<HTMLDivElement | null>(null);
  const locWrapRef     = useRef<HTMLDivElement | null>(null);

  const isBypass = process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1";
  useEffect(() => {
    if (isBypass || isAuthenticated) {
      fetchDistricts().then(setDistricts);
    }
  }, [isAuthenticated, isBypass]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (searchQ.length < 2) { setSearchResults([]); setShowResults(false); return; }
    debounceRef.current = setTimeout(async () => {
      if (loadedSurveyNos?.size && loadedVillage) {
        const qNorm = searchQ.split("/")[0].trim();
        if (qNorm.length < 2) { setSearchResults([]); setShowResults(false); return; }
        const matching: SearchResult[] = Array.from(loadedSurveyNos)
          .filter(no => no.split("/")[0].trim().startsWith(qNorm))
          .sort()
          .map(no => ({
            survey_no: no,
            village_name: "",
            dist: loadedVillage.dist,
            taluk: loadedVillage.taluk,
            hobli: loadedVillage.hobli,
            vlg: loadedVillage.vlg,
          }));
        setSearchResults(matching);
        setShowResults(matching.length > 0);
        setSearchErr(matching.length === 0 ? "No results" : "");
      } else {
        setSearchErr("");
        try {
          const results = await searchBySurveyNo(searchQ);
          setSearchResults(results);
          setShowResults(results.length > 0);
          if (results.length === 0) setSearchErr("No results");
        } catch (e) {
          setSearchErr(e instanceof Error ? e.message : "Search failed");
          setSearchResults([]);
          setShowResults(false);
        }
      }
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [searchQ, loadedSurveyNos, loadedVillage]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (searchWrapRef.current && !searchWrapRef.current.contains(e.target as Node)) {
        setShowResults(false);
      }
      if (locWrapRef.current && !locWrapRef.current.contains(e.target as Node)) {
        setShowLocResults(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Location search — cadastral village name prefix search
  useEffect(() => {
    if (locDebounceRef.current) clearTimeout(locDebounceRef.current);
    if (locQ.length < 2) { setLocResults([]); setShowLocResults(false); return; }
    locDebounceRef.current = setTimeout(async () => {
      setLocLoading(true);
      const data = await fetchVillageSearch(locQ);
      setLocResults(data);
      setShowLocResults(true);
      setLocLoading(false);
    }, 300);
    return () => { if (locDebounceRef.current) clearTimeout(locDebounceRef.current); };
  }, [locQ]);

  // autoSelect: when MapView sets this after coord-go + /nearby, auto-populate + load
  useEffect(() => {
    if (!autoSelect) return;
    async function run() {
      setDist(autoSelect!.dist);
      setTaluk(autoSelect!.taluk);
      setHobli(autoSelect!.hobli);
      setVlg(autoSelect!.vlg);
      const [t, h, v] = await Promise.all([
        fetchTaluks(autoSelect!.dist),
        fetchHoblis(autoSelect!.dist, autoSelect!.taluk),
        fetchVillages(autoSelect!.dist, autoSelect!.taluk, autoSelect!.hobli),
      ]);
      setTaluks(t); setHoblis(h); setVillages(v);
      setStatus("Village pre-selected — click Load");
    }
    run();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSelect?.dist, autoSelect?.taluk, autoSelect?.hobli, autoSelect?.vlg]);

  useEffect(() => {
    if (autoStatus) setStatus(autoStatus);
  }, [autoStatus]);

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

  function handleCoordFly() {
    const lat = parseFloat(latQ);
    const lon = parseFloat(lonQ);
    if (isNaN(lat) || isNaN(lon)) return;
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
    if (onCoordGo) {
      onCoordGo({ lat, lon });
    } else {
      onFlyTo?.({ lat, lon });
    }
  }

  function toggleCoordMode() {
    setCoordMode(m => !m);
    setLatQ(""); setLonQ("");
    setSearchQ(""); setSearchResults([]); setShowResults(false); setSearchErr("");
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
    setLoadedVillage({ dist, taluk, hobli, vlg });
    onLoad(fc, label, { dist, taluk, hobli, vlg });
  }

  const mSel: React.CSSProperties = isMobile
    ? { ...SEL_STYLE, minHeight: 36, fontSize: 13, padding: "5px 8px" }
    : SEL_STYLE;
  const mBtn: React.CSSProperties = isMobile
    ? { ...BTN_STYLE, padding: "8px 16px", minHeight: 36 }
    : BTN_STYLE;

  return (
    <div style={{
      position: "relative", zIndex: 10,
      display: "flex", alignItems: "center", gap: 8, padding: isMobile ? "8px 10px" : "6px 14px",
      background: "rgba(253,252,251,0.55)",
      backdropFilter: "blur(14px) saturate(160%)",
      WebkitBackdropFilter: "blur(14px) saturate(160%)",
      borderBottom: "1px solid rgba(255,255,255,0.6)",
      flexWrap: "wrap", minHeight: isMobile ? 48 : 42,
      boxShadow: "0 6px 26px rgba(58,63,59,0.18), inset 0 1px 0 rgba(255,255,255,0.45)",
    }}>
      <span style={{ color: "#306223", fontWeight: 800, fontSize: 13, whiteSpace: "nowrap", letterSpacing: "0.01em" }}>
        Karnataka Cadastral
      </span>
      <span style={{ color: "#CFD6C4", fontSize: 16 }}>|</span>

      {/* Location search — always visible */}
      {onSearch && (
        <div ref={locWrapRef} style={{ position: "relative" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "2px 7px", border: "1px solid #CFD6C4",
            borderRadius: 5, background: "#FDFCFB",
          }}>
            <SearchIcon />
            <input
              type="text"
              placeholder="Search location…"
              value={locQ}
              onChange={(e) => setLocQ(e.target.value)}
              onFocus={() => locResults.length > 0 && setShowLocResults(true)}
              style={{
                padding: "1px 0", fontSize: 12, border: "none",
                background: "transparent", outline: "none",
                flex: 1, minWidth: 0, width: isMobile ? 120 : 160, color: "#3A3F3B", fontFamily: "inherit",
              }}
            />
            {locLoading && <span style={{ fontSize: 10, color: "#9EAD98" }}>…</span>}
          </div>
          {showLocResults && locResults.length > 0 && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 9999,
              background: "#FDFCFB", border: "1px solid #CFD6C4", borderRadius: 5,
              boxShadow: "0 4px 16px rgba(58,63,59,0.14)",
              minWidth: 260, maxHeight: 220, overflowY: "auto",
            }}>
              {locResults.map((r, i) => (
                <div
                  key={`${r.dist}-${r.taluk}-${r.hobli}-${r.vlg}-${i}`}
                  onMouseDown={() => {
                    setShowLocResults(false);
                    setLocQ(r.village_name);
                    onVillageSelect?.({ dist: r.dist, taluk: r.taluk, hobli: r.hobli, vlg: r.vlg });
                  }}
                  style={{ padding: "6px 10px", fontSize: 12, cursor: "pointer", color: "#3A3F3B", borderBottom: "1px solid #EEE" }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "#F0EDE8"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontWeight: 600 }}>{r.village_name}</span>
                    <span style={{ fontSize: 10, color: "#16A34A", fontWeight: 700, background: "#DCFCE7", borderRadius: 4, padding: "1px 5px" }}>
                      cadastral
                    </span>
                  </div>
                  <div style={{ fontSize: 10, color: "#9EAD98", marginTop: 1 }}>
                    {r.dist_name} · {r.taluk_name}
                  </div>
                </div>
              ))}
            </div>
          )}
          {showLocResults && locResults.length === 0 && !locLoading && locQ.length >= 2 && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 9999,
              background: "#FDFCFB", border: "1px solid #CFD6C4", borderRadius: 5,
              padding: "10px 12px", fontSize: 12, color: "#9EAD98",
              boxShadow: "0 4px 16px rgba(58,63,59,0.14)", minWidth: 260,
            }}>
              No cadastral data found for &quot;{locQ}&quot;
            </div>
          )}
        </div>
      )}

      <span style={{ color: "#CFD6C4", fontSize: 16 }}>|</span>

      <select value={dist} onChange={(e) => handleDistChange(e.target.value)} style={mSel}>
        <option value="">District</option>
        {districts.map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
      </select>

      <select value={taluk} onChange={(e) => handleTalukChange(e.target.value)} style={{ ...mSel, opacity: taluks.length ? 1 : 0.45 }}>
        <option value="">{taluks.length ? "Taluk" : dist ? "Loading…" : "— Taluk —"}</option>
        {taluks.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
      </select>

      <select value={hobli} onChange={(e) => handleHobliChange(e.target.value)} style={{ ...mSel, opacity: hoblis.length ? 1 : 0.45 }}>
        <option value="">{hoblis.length ? "Hobli" : taluk ? "Loading…" : "— Hobli —"}</option>
        {hoblis.map((h) => <option key={h.code} value={h.code}>{h.name}</option>)}
      </select>

      <select value={vlg} onChange={(e) => setVlg(e.target.value)} style={{ ...mSel, opacity: villages.length ? 1 : 0.45 }}>
        <option value="">{villages.length ? "All villages" : hobli ? "Loading…" : "— Village —"}</option>
        {villages.map((v) => <option key={v.code} value={v.code}>{v.name}</option>)}
      </select>

      <button onClick={handleLoad} disabled={loading} style={mBtn}>
        {loading ? "Loading…" : "Load"}
      </button>

      {onSearch && (
        <>
          <span style={{ color: "#CFD6C4", fontSize: 16 }}>|</span>

          <button onClick={toggleCoordMode} style={{
            padding: "3px 9px", border: "1px solid #CFD6C4", borderRadius: 9999,
            fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
            background: coordMode ? "#306223" : "#FDFCFB",
            color: coordMode ? "#FDFCFB" : "#7B8F83",
            whiteSpace: "nowrap",
          }}>
            Coords
          </button>

          {coordMode ? (
            /* Lat / Lon split inputs */
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <div style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 7px", border: "1px solid #CFD6C4",
                borderRadius: 5, background: "#FDFCFB",
              }}>
                <SearchIcon />
                <input
                  type="number"
                  placeholder="Lat"
                  value={latQ}
                  onChange={(e) => setLatQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleCoordFly()}
                  style={{
                    padding: "1px 0", fontSize: 12, border: "none",
                    background: "transparent", outline: "none",
                    flex: 1, minWidth: 0, color: "#3A3F3B", fontFamily: "inherit",
                  }}
                />
                <span style={{ color: "#CFD6C4", fontSize: 13, lineHeight: 1 }}>,</span>
                <input
                  type="number"
                  placeholder="Lon"
                  value={lonQ}
                  onChange={(e) => setLonQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleCoordFly()}
                  style={{
                    padding: "1px 0", fontSize: 12, border: "none",
                    background: "transparent", outline: "none",
                    flex: 1, minWidth: 0, color: "#3A3F3B", fontFamily: "inherit",
                  }}
                />
              </div>
              <button onClick={handleCoordFly} style={{
                ...BTN_STYLE, padding: "4px 10px", fontSize: 11,
              }}>Go →</button>
            </div>
          ) : (
            /* Survey search */
            <div ref={searchWrapRef} style={{ position: "relative" }}>
              <div style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "2px 7px",
                border: `1px solid ${searchErr && searchErr !== "No results" ? "#c0392b" : "#CFD6C4"}`,
                borderRadius: 5, background: "#FDFCFB",
              }}>
                <SearchIcon />
                <input
                  type="text"
                  placeholder="Survey No…"
                  value={searchQ}
                  onChange={(e) => { setSearchQ(e.target.value); setSearchErr(""); }}
                  onFocus={() => searchResults.length > 0 && setShowResults(true)}
                  style={{
                    padding: "1px 0", fontSize: 12, border: "none",
                    background: "transparent", outline: "none",
                    flex: 1, minWidth: 0, maxWidth: 160, color: "#3A3F3B", fontFamily: "inherit",
                  }}
                />
              </div>
              {searchErr && (
                <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 9999,
                  background: "#fff", border: "1px solid #CFD6C4", borderRadius: 5,
                  padding: "6px 10px", fontSize: 11, color: searchErr === "No results" ? "#7B8F83" : "#c0392b",
                  whiteSpace: "nowrap", boxShadow: "0 2px 8px rgba(0,0,0,0.1)" }}>
                  {searchErr}
                </div>
              )}
              {showResults && <SearchDropdown results={searchResults} loadedSurveyNos={loadedSurveyNos} loadedVillage={loadedVillage ?? undefined} onSelect={(r, isCurrent) => { setShowResults(false); setSearchQ(r.survey_no); if (isCurrent) { onHighlight?.(r); } else { onSearch?.(r); } }} />}
            </div>
          )}
        </>
      )}

      {status && (
        <span style={{ fontSize: 11, color: "#9EAD98", marginLeft: "auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: isMobile ? "100%" : 220 }}>
          {status}
        </span>
      )}
    </div>
  );
}
