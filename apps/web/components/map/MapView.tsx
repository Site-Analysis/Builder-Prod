// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1D — parcel click popup + survey search fly-to added.
// Phase 2  — village boundary overlay (green, auto) + nearby hobli boundaries (red, toggle).

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap, useMapEvents } from "react-leaflet";
import { type Map as LeafletMap, type Layer, type GeoJSONOptions } from "leaflet";
import { CadastralToolbar } from "./CadastralToolbar";
import {
  fetchParcelData, fetchVillageBoundary, fetchNearbyBoundaries,
  fetchRtcData, type SearchResult, type RtcData,
} from "@/lib/api/cadastral_records";
import { useIsMobile } from "@/lib/useIsMobile";
import "leaflet/dist/leaflet.css";

// Karnataka centroid — default map center
const KA_CENTER: [number, number] = [15.3173, 75.7139];
const KA_ZOOM = 7;

const TOOLTIP_THRESHOLD = 500;
const PERMANENT_LABEL_THRESHOLD = 1500;

const PROP_LABELS: Record<string, string> = {
  owner_name:     "Owner",
  khatedar_name:  "Owner",
  extent:         "Extent",
  area:           "Area",
  classification: "Type",
  land_type:      "Land Type",
  village_name:   "Village",
  taluk:          "Taluk",
  hobli:          "Hobli",
};

function ParcelLayer({
  fc,
  mapLayer,
}: {
  fc: GeoJSON.FeatureCollection;
  mapLayer: "base" | "satellite";
}) {
  const map = useMap();
  const filteredFc: GeoJSON.FeatureCollection = {
    ...fc,
    features: fc.features.filter(
      (f) => f.geometry.type === "Polygon" || f.geometry.type === "MultiPolygon",
    ),
  };
  const showPermanent = filteredFc.features.length <= PERMANENT_LABEL_THRESHOLD;
  const showTooltips  = filteredFc.features.length <= TOOLTIP_THRESHOLD;
  const isSat = mapLayer === "satellite";

  const options = {
    style: () => ({
      color:       isSat ? "#FFFFFF" : "#306223",
      weight:      isSat ? 1.5 : 1,
      opacity:     0.9,
      fillColor:   isSat ? "#FFFFFF" : "#306223",
      fillOpacity: isSat ? 0.10 : 0.08,
    }),
    onEachFeature: (feature: GeoJSON.Feature, layer: Layer) => {
      const surveyNo = (feature.properties as Record<string, string>)?.survey_no;
      if (!surveyNo) return;
      if (showPermanent) {
        layer.bindTooltip(surveyNo, {
          permanent: true,
          direction: "center",
          className: "cadastral-label",
          offset: [0, 0],
        });
      } else if (showTooltips) {
        layer.bindTooltip(surveyNo, {
          permanent: false,
          direction: "top",
          sticky: true,
          className: "cadastral-tooltip",
          offset: [0, -4],
        });
      }
    },
  };

  // Fly to bounds whenever this component mounts (keyed per load in MapView).
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        const coords = filteredFc.features.flatMap((f) => {
          if (f.geometry.type === "Polygon") return f.geometry.coordinates[0];
          if (f.geometry.type === "MultiPolygon") return f.geometry.coordinates.flatMap((r) => r[0]);
          return [];
        }) as [number, number][];
        if (coords.length) {
          const lats = coords.map((c) => c[1]);
          const lngs = coords.map((c) => c[0]);
          map.fitBounds(
            [[Math.min(...lats), Math.min(...lngs)], [Math.max(...lats), Math.max(...lngs)]],
            { padding: [40, 40], maxZoom: 16 },
          );
        }
      } catch { /* ignore */ }
    }, 50);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <GeoJSON
      data={filteredFc}
      style={options.style}
      onEachFeature={options.onEachFeature}
    />
  );
}

function VillageBoundaryLayer({
  fc,
  color,
  colorFn,
  weight = 2,
  labelPermanent = false,
  fillOpacity: fillOpacityProp,
  showLabel = true,
}: {
  fc: GeoJSON.FeatureCollection;
  color?: string;
  colorFn?: (feature: GeoJSON.Feature) => string;
  weight?: number;
  labelPermanent?: boolean;
  fillOpacity?: number;
  showLabel?: boolean;
}) {
  const options: GeoJSONOptions = {
    style: (feature) => {
      const c = colorFn ? colorFn(feature!) : (color ?? "#306223");
      const fo = fillOpacityProp ?? (weight >= 3 ? 0.10 : 0.04);
      return {
        color: c,
        weight,
        opacity: 0.9,
        fillColor: c,
        fillOpacity: fo,
        dashArray: weight < 3 ? "6 4" : undefined,
      };
    },
    onEachFeature: (feature: GeoJSON.Feature, layer: Layer) => {
      if (!showLabel) return;
      const name = (feature.properties as Record<string, string>)?.village_name;
      if (name) {
        layer.bindTooltip(name, {
          permanent: labelPermanent,
          sticky: false,
          direction: "center",
          className: labelPermanent ? "village-boundary-label" : "cadastral-tooltip",
        });
      }
    },
  };
  return <GeoJSON data={fc} {...options} />;
}

// Ray-cast point-in-polygon for one GeoJSON ring ([lng, lat][] pairs).
function pipRing(lat: number, lng: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function MapClickHandler({
  parcelFc,
  onParcelClick,
}: {
  parcelFc: GeoJSON.FeatureCollection;
  onParcelClick: (props: Record<string, unknown>, pos: { x: number; y: number }, latlng: { lat: number; lng: number }) => void;
}) {
  const map = useMap();
  useMapEvents({
    click(e) {
      const { lat, lng } = e.latlng;
      let bestFeature: GeoJSON.Feature | null = null;
      let bestArea = Infinity;
      for (const feature of parcelFc.features) {
        const g = feature.geometry;
        let hit = false;
        if (g.type === "Polygon") {
          hit = pipRing(lat, lng, g.coordinates[0] as number[][]);
        } else if (g.type === "MultiPolygon") {
          hit = (g.coordinates as number[][][][]).some((poly) => pipRing(lat, lng, poly[0]));
        }
        if (hit) {
          const coords: number[][] = g.type === "Polygon"
            ? (g as GeoJSON.Polygon).coordinates[0]
            : (g as GeoJSON.MultiPolygon).coordinates.flat(2);
          const lngs = coords.map((c) => c[0]);
          const lats = coords.map((c) => c[1]);
          const area = (Math.max(...lngs) - Math.min(...lngs)) * (Math.max(...lats) - Math.min(...lats));
          if (area < bestArea) { bestArea = area; bestFeature = feature; }
        }
      }
      if (bestFeature) {
        const { x, y } = e.containerPoint;
        const size = map.getSize();
        const cardW = 284;
        const cardH = 220;
        const left = x + cardW > size.x ? Math.max(4, x - cardW) : x + 14;
        const top  = y + cardH > size.y ? Math.max(4, y - cardH) : y + 14;
        onParcelClick((bestFeature.properties ?? {}) as Record<string, unknown>, { x: left, y: top }, e.latlng);
      }
    },
  });
  return null;
}

function ZoomLabelController() {
  const map = useMap();
  useEffect(() => {
    function update() {
      const z = map.getZoom();
      let el = document.getElementById("zoom-label-ctrl") as HTMLStyleElement | null;
      if (!el) {
        el = document.createElement("style");
        el.id = "zoom-label-ctrl";
        document.head.appendChild(el);
      }
      if (z < 14)      el.textContent = ".cadastral-label { display: none !important; }";
      else if (z < 16) el.textContent = ".cadastral-label { opacity: 0.3 !important; font-size: 7px !important; }";
      else             el.textContent = "";
    }
    update();
    map.on("zoomend", update);
    return () => {
      map.off("zoomend", update);
      document.getElementById("zoom-label-ctrl")?.remove();
    };
  }, [map]);
  return null;
}

function flyToBounds(map: LeafletMap, fc: GeoJSON.FeatureCollection, surveyNo?: string) {
  const target = surveyNo
    ? fc.features.find((f) => (f.properties as Record<string, string>)?.survey_no?.split("/")[0] === surveyNo.split("/")[0])
    : null;
  const features = target ? [target] : fc.features;
  const coords = features.flatMap((f) => {
    if (f.geometry.type === "Polygon") return f.geometry.coordinates[0];
    if (f.geometry.type === "MultiPolygon") return f.geometry.coordinates.flatMap((r) => r[0]);
    return [];
  }) as [number, number][];
  if (!coords.length) return;
  const lats = coords.map((c) => c[1]);
  const lngs = coords.map((c) => c[0]);
  map.fitBounds(
    [[Math.min(...lats), Math.min(...lngs)], [Math.max(...lats), Math.max(...lngs)]],
    { padding: [40, 40], maxZoom: surveyNo ? 20 : 16 },
  );
}

const TILES = {
  base: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 21,
    maxNativeZoom: 19,
    detectRetina: true,
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
    maxZoom: 21,
    maxNativeZoom: 18,
    detectRetina: false,
  },
};

interface VillageCoords { dist: string; taluk: string; hobli: string; vlg: string; }

export function MapView() {
  const [parcelFc, setParcelFc]             = useState<GeoJSON.FeatureCollection | null>(null);
  const [loadKey, setLoadKey]               = useState(0);
  const [clickedParcelProps, setClickedParcelProps] = useState<Record<string, unknown> | null>(null);
  const [clickPos, setClickPos] = useState<{ x: number; y: number } | null>(null);
  const [highlightedSurveyNo, setHighlightedSurveyNo] = useState<string | null>(null);
  const [mapLayer, setMapLayer]             = useState<"base" | "satellite">("base");
  const [villageBoundaryFc, setVillageBoundaryFc] = useState<GeoJSON.FeatureCollection | null>(null);
  const [hobliBoundaryFc, setHobliBoundaryFc]     = useState<GeoJSON.FeatureCollection | null>(null);
  const [showNearby, setShowNearby]         = useState(false);
  const [villageBoundaryKey, setVillageBoundaryKey] = useState(0);
  const [hobliBoundaryKey, setHobliBoundaryKey]     = useState(0);
  const [autoSelect, setAutoSelect]                 = useState<VillageCoords | null>(null);
  const [autoStatus, setAutoStatus]                 = useState<string>("");
  const [loadedVillage, setLoadedVillage]           = useState<VillageCoords | null>(null);
  const [rtcData, setRtcData]                       = useState<RtcData | null | "loading">(null);
  const [clickedLatLng, setClickedLatLng]           = useState<{ lat: number; lng: number } | null>(null);
  const mapRef        = useRef<LeafletMap | null>(null);

  useEffect(() => {
    if (!autoSelect) return;
    const ctrl = new AbortController();
    fetchVillageBoundary(autoSelect.dist, autoSelect.taluk, autoSelect.hobli, autoSelect.vlg, ctrl.signal)
      .then((bf) => {
        if (bf && mapRef.current) flyToBounds(mapRef.current, bf);
      });
    return () => ctrl.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSelect?.dist, autoSelect?.taluk, autoSelect?.hobli, autoSelect?.vlg]);
  const hobliKeyRef   = useRef<string | null>(null);
  const currentHierRef = useRef<VillageCoords | null>(null);
  const { isMobile }  = useIsMobile();

  const loadedSurveyNos = useMemo<Set<string>>(() => {
    if (!parcelFc) return new Set();
    const s = new Set<string>();
    for (const f of parcelFc.features) {
      const no = (f.properties as Record<string, string>)?.survey_no;
      if (no) s.add(no.split("/")[0]);
    }
    return s;
  }, [parcelFc]);

  async function loadBoundaries(hier: VillageCoords, nearby: boolean) {
    currentHierRef.current = hier;
    const bf = await fetchVillageBoundary(hier.dist, hier.taluk, hier.hobli, hier.vlg);
    setVillageBoundaryFc(bf);
    setVillageBoundaryKey((k) => k + 1);
    if (nearby && bf?.features?.length) {
      const allCoords: number[][] = [];
      for (const f of bf.features) {
        const g = f.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
        if (!g) continue;
        if (g.type === "Polygon") allCoords.push(...g.coordinates.flat());
        else if (g.type === "MultiPolygon") allCoords.push(...g.coordinates.flat(2));
      }
      if (allCoords.length) {
        const lat = allCoords.reduce((s, c) => s + c[1], 0) / allCoords.length;
        const lng = allCoords.reduce((s, c) => s + c[0], 0) / allCoords.length;
        const nkey = `${lat.toFixed(2)}-${lng.toFixed(2)}`;
        if (hobliKeyRef.current !== nkey) {
          hobliKeyRef.current = nkey;
          const hb = await fetchNearbyBoundaries(lat, lng, 10);
          setHobliBoundaryFc(hb);
          setHobliBoundaryKey((k) => k + 1);
        }
      }
    }
  }

  function handleFlyTo(coords: { lat: number; lon: number }) {
    mapRef.current?.setView([coords.lat, coords.lon], 16);
  }

  function handleLocBounds(bbox: [[number, number], [number, number]]) {
    mapRef.current?.fitBounds(bbox, { padding: [40, 40], maxZoom: 16 });
  }

  async function handleCoordGo(coords: { lat: number; lon: number }) {
    mapRef.current?.setView([coords.lat, coords.lon], 16);
    const fc = await fetchNearbyBoundaries(coords.lat, coords.lon, 5);
    if (!fc?.features?.length) {
      setAutoStatus("No cadastral data found near this location — select district manually");
      return;
    }
    const nearest = fc.features.find((f) => f.properties?.has_data && f.properties?.dist)
      ?? fc.features.find((f) => f.properties?.dist);
    if (!nearest?.properties) {
      setAutoStatus("No cadastral data found near this location — select district manually");
      return;
    }
    setAutoStatus("");
    const { dist, taluk, hobli, vlg } = nearest.properties as Record<string, string>;
    setAutoSelect(null);
    setTimeout(() => setAutoSelect({ dist, taluk, hobli, vlg }), 0);
  }

  function handleHighlight(result: SearchResult) {
    setHighlightedSurveyNo(result.survey_no);
    if (mapRef.current && parcelFc) {
      flyToBounds(mapRef.current, parcelFc, result.survey_no);
    }
  }

  async function handleSearchResult(result: SearchResult) {
    const fc = await fetchParcelData(result.dist, result.taluk, result.hobli, result.vlg);
    if (!fc) return;
    setParcelFc(fc);
    setLoadKey((k) => k + 1);
    setClickedParcelProps(null); setClickPos(null); setRtcData(null); setClickedLatLng(null);
    setHighlightedSurveyNo(result.survey_no);
    setLoadedVillage({ dist: result.dist, taluk: result.taluk, hobli: result.hobli, vlg: result.vlg });
    loadBoundaries({ dist: result.dist, taluk: result.taluk, hobli: result.hobli, vlg: result.vlg }, showNearby);
    setTimeout(() => {
      if (mapRef.current) flyToBounds(mapRef.current, fc, result.survey_no);
    }, 80);
  }

  async function handleNearbyToggle() {
    const next = !showNearby;
    setShowNearby(next);
    if (!next) {
      setHobliBoundaryFc(null);
      hobliKeyRef.current = null;
      return;
    }
    const center = mapRef.current?.getCenter();
    if (!center) return;
    const nkey = `${center.lat.toFixed(2)}-${center.lng.toFixed(2)}`;
    if (hobliKeyRef.current !== nkey) {
      hobliKeyRef.current = nkey;
      const fc = await fetchNearbyBoundaries(center.lat, center.lng, 10);
      setHobliBoundaryFc(fc);
      setHobliBoundaryKey((k) => k + 1);
    }
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Cadastral toolbar sits above the map */}
      <CadastralToolbar
        onLoad={(fc, _label, hier) => {
          setParcelFc(fc);
          setLoadKey((k) => k + 1);
          setClickedParcelProps(null); setClickPos(null); setRtcData(null); setClickedLatLng(null);
          setHighlightedSurveyNo(null);
          if (fc && hier) { loadBoundaries(hier, showNearby); setLoadedVillage(hier); }
        }}
        onSearch={handleSearchResult}
        onHighlight={handleHighlight}
        onFlyTo={handleFlyTo}
        onLocBounds={handleLocBounds}
        onCoordGo={handleCoordGo}
        onVillageSelect={(hier) => {
          setAutoSelect(null);
          setTimeout(() => setAutoSelect(hier), 0);
        }}
        autoSelect={autoSelect}
        autoStatus={autoStatus}
        loadedSurveyNos={loadedSurveyNos}
      />

      {/* Map fills remaining height */}
      <div style={{ flex: 1, minHeight: 0, position: "relative", zIndex: 1 }}>
        {/* Map layer + nearby toggle */}
        <div style={{
          position: "absolute", top: 10, right: 10, zIndex: 1000,
          display: "flex", borderRadius: 6, overflow: "hidden",
          border: "1px solid #CFD6C4",
          boxShadow: "0 2px 8px rgba(58,63,59,0.14)",
        }}>
          {(["base", "satellite"] as const).map(layer => (
            <button key={layer} onClick={() => setMapLayer(layer)} style={{
              padding: isMobile ? "9px 13px" : "5px 10px", fontSize: isMobile ? 12 : 11, fontWeight: 600, cursor: "pointer",
              border: "none", fontFamily: "inherit",
              background: mapLayer === layer ? "#306223" : "#FDFCFB",
              color: mapLayer === layer ? "#FDFCFB" : "#7B8F83",
            }}>
              {layer === "base" ? "Map" : "Satellite"}
            </button>
          ))}
          <div style={{ width: 1, background: "#CFD6C4", alignSelf: "stretch" }} />
          <button onClick={handleNearbyToggle} title="LGD village boundaries — approximate administrative boundaries, may not align exactly with survey parcel edges" style={{
            padding: isMobile ? "9px 13px" : "5px 10px", fontSize: isMobile ? 12 : 11, fontWeight: 600, cursor: "pointer",
            border: "none", fontFamily: "inherit",
            background: showNearby ? "#7B8F83" : "#FDFCFB",
            color: showNearby ? "#FDFCFB" : "#7B8F83",
          }}>
            Nearby
          </button>
        </div>


        {/* Compass */}
        <div style={{
          position: "absolute", top: 58, right: 10, zIndex: 1000,
          background: "rgba(253,252,251,0.92)", borderRadius: "50%",
          width: 44, height: 44, boxShadow: "0 2px 8px rgba(58,63,59,0.18)",
          border: "1px solid #CFD6C4", display: "flex", alignItems: "center",
          justifyContent: "center", pointerEvents: "none", userSelect: "none",
        }}>
          <div style={{ position: "relative", width: 32, height: 32, fontSize: 8, fontWeight: 700, color: "#3A3F3B" }}>
            <span style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", color: "#306223" }}>N</span>
            <span style={{ position: "absolute", bottom: 0, left: "50%", transform: "translateX(-50%)" }}>S</span>
            <span style={{ position: "absolute", left: 0, top: "50%", transform: "translateY(-50%)" }}>W</span>
            <span style={{ position: "absolute", right: 0, top: "50%", transform: "translateY(-50%)" }}>E</span>
            <div style={{ position: "absolute", left: "50%", top: 8, bottom: 8, width: 1, background: "#CFD6C4", transform: "translateX(-50%)" }} />
            <div style={{ position: "absolute", top: "50%", left: 8, right: 8, height: 1, background: "#CFD6C4", transform: "translateY(-50%)" }} />
          </div>
        </div>

        {/* Parcel info card — appears near click cursor */}
        {clickedParcelProps && clickPos && (
          <div style={{
            position: "absolute", left: clickPos.x, top: clickPos.y, zIndex: 1000,
            background: "rgba(253,252,251,0.97)", color: "#3A3F3B",
            padding: "10px 14px", borderRadius: 8, fontSize: 12,
            boxShadow: "0 4px 16px rgba(0,0,0,0.18)", border: "1px solid #CFD6C4",
            minWidth: 180, maxWidth: 260,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontWeight: 800, fontSize: 13, color: "#306223" }}>
                {String(clickedParcelProps.village_name ?? "—")} · {String(clickedParcelProps.survey_no ?? "—")}
              </span>
              <span
                onClick={() => { setClickedParcelProps(null); setClickPos(null); setRtcData(null); setClickedLatLng(null); }}
                style={{ cursor: "pointer", opacity: 0.45, fontSize: 18, lineHeight: 1, fontWeight: 300, marginLeft: 12 }}
              >×</span>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {Object.entries(clickedParcelProps)
                  .filter(([k, v]) => k !== "survey_no" && k !== "geometry" && v != null && String(v).trim() !== "" && String(v) !== "nan")
                  .map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ color: "#7B8F83", paddingRight: 8, paddingBottom: 3, fontWeight: 500, whiteSpace: "nowrap", verticalAlign: "top", fontSize: 11 }}>
                        {PROP_LABELS[k] ?? k.replace(/_/g, " ")}
                      </td>
                      <td style={{ fontWeight: 600, paddingBottom: 3, wordBreak: "break-word", fontSize: 11 }}>
                        {String(v)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {clickedLatLng && (
              <div style={{ marginTop: 6, fontSize: 11 }}>
                <span style={{ color: "#7B8F83" }}>{clickedLatLng.lat.toFixed(6)}, {clickedLatLng.lng.toFixed(6)}</span>
                {" · "}
                <a
                  href={`https://www.google.com/maps?q=${clickedLatLng.lat},${clickedLatLng.lng}`}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "#1A73E8", fontWeight: 700 }}
                >Google Maps</a>
              </div>
            )}
            {rtcData === "loading" && (
              <div style={{ marginTop: 8, color: "#7B8F83", fontSize: 11 }}>Loading court cases…</div>
            )}
            {rtcData && rtcData !== "loading" && (
              <div style={{ marginTop: 8, borderTop: "1px solid #E8EEE4", paddingTop: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 11, color: "#306223", marginBottom: 4 }}>Active Court Cases (RCCMS)</div>
                {rtcData.owners.length === 0
                  ? <div style={{ color: "#7B8F83", fontSize: 11 }}>No active cases</div>
                  : rtcData.owners.map((o, i) => (
                      <div key={i} style={{ marginBottom: 3, fontSize: 11 }}>
                        <span style={{ fontWeight: 600 }}>{o.owner_name || "—"}</span>
                        {o.case_status && <span style={{ color: "#7B8F83", marginLeft: 6 }}>{o.case_status}</span>}
                      </div>
                    ))
                }
                {rtcData.mutations.length > 0 && (
                  <>
                    <div style={{ fontWeight: 700, fontSize: 11, color: "#306223", marginTop: 6, marginBottom: 3 }}>Mutations</div>
                    {rtcData.mutations.map((m, i) => (
                      <div key={i} style={{ fontSize: 11, marginBottom: 3 }}>
                        <span style={{ fontWeight: 600 }}>{m.transaction_type || "—"}</span>
                        {m.mr_number && <span style={{ color: "#7B8F83", marginLeft: 6 }}>MR {m.mr_number}</span>}
                        {m.status && <span style={{ color: "#7B8F83", marginLeft: 6 }}>{m.status}</span>}
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        )}
        <MapContainer
          center={KA_CENTER}
          zoom={KA_ZOOM}
          style={{ position: "absolute", inset: 0 }}
          ref={mapRef}
        >
          <ZoomLabelController />
          <TileLayer key={mapLayer} {...TILES[mapLayer]} />
          {/* Nearby hobli boundaries — green if LGD name exists, red if not */}
          {showNearby && hobliBoundaryFc && (
            <VillageBoundaryLayer
              key={`hb-${hobliBoundaryKey}`}
              fc={loadedVillage ? {
                ...hobliBoundaryFc,
                features: hobliBoundaryFc.features.filter(
                  (f) => !(f.properties?.dist === loadedVillage.dist && f.properties?.vlg === loadedVillage.vlg),
                ),
              } : hobliBoundaryFc}
              colorFn={(f) => (f.properties as Record<string, unknown>)?.has_data ? "#4caf50" : "#ef5350"}
              weight={2}
              fillOpacity={0.10}
            />
          )}
          {parcelFc && (
            <>
              <ParcelLayer key={loadKey} fc={parcelFc} mapLayer={mapLayer} />
              <MapClickHandler parcelFc={parcelFc} onParcelClick={(props, pos, latlng) => {
                setClickedParcelProps(props); setClickPos(pos); setClickedLatLng(latlng);
                setRtcData("loading");
                if (loadedVillage) {
                  fetchRtcData(
                    loadedVillage.dist, loadedVillage.taluk, loadedVillage.hobli, loadedVillage.vlg,
                    String(props.village_code ?? ""), String(props.survey_no ?? ""),
                  ).then(setRtcData);
                }
              }} />
              {highlightedSurveyNo && (() => {
                const hlFc: GeoJSON.FeatureCollection = {
                  type: "FeatureCollection",
                  features: parcelFc.features.filter(
                    (f) => (f.properties as Record<string, string>)?.survey_no?.split("/")[0] === highlightedSurveyNo?.split("/")[0],
                  ),
                };
                if (!hlFc.features.length) return null;
                return (
                  <GeoJSON
                    key={`hl-${highlightedSurveyNo}`}
                    data={hlFc}
                    style={() => ({ color: "#F59E0B", weight: 2.5, fillColor: "#FBBF24", fillOpacity: 0.65, opacity: 1 })}
                    onEachFeature={(_f, layer) => { layer.off(); }}
                  />
                );
              })()}
            </>
          )}
          {/* Loaded village boundary — blue, no label (label followed cursor, removed per SME feedback) */}
          {villageBoundaryFc && (
            <VillageBoundaryLayer key={`vb-${villageBoundaryKey}`} fc={villageBoundaryFc} color="#2563EB" weight={3} showLabel={false} />
          )}
        </MapContainer>
      </div>

      <style>{`
        .cadastral-tooltip {
          background: rgba(253,252,251,0.92);
          border: 1px solid #CFD6C4;
          border-radius: 4px;
          font-size: 11px;
          color: #3A3F3B;
          padding: 2px 6px;
          box-shadow: 0 2px 8px rgba(58,63,59,0.12);
        }
        .cadastral-popup .leaflet-popup-content-wrapper {
          background: rgba(253,252,251,0.96);
          border: 1px solid #CFD6C4;
          border-radius: 6px;
          box-shadow: 0 4px 16px rgba(58,63,59,0.16);
          font-family: inherit;
        }
        .cadastral-popup .leaflet-popup-tip {
          background: rgba(253,252,251,0.96);
        }
        .leaflet-tooltip-top:before,
        .leaflet-tooltip-bottom:before,
        .leaflet-tooltip-left:before,
        .leaflet-tooltip-right:before {
          border-color: transparent;
        }
        .leaflet-interactive:focus {
          outline: none;
        }
        .cadastral-label {
          background: rgba(255,255,255,0.82) !important;
          border: none !important;
          box-shadow: none !important;
          font-size: 9px;
          font-weight: 700;
          color: #306223;
          padding: 1px 4px;
          border-radius: 3px;
          pointer-events: none;
          white-space: nowrap;
        }
        .cadastral-label::before {
          display: none !important;
        }
        .village-boundary-label {
          background: rgba(37,99,235,0.88) !important;
          border: none !important;
          box-shadow: 0 2px 6px rgba(0,0,0,0.20) !important;
          font-size: 11px;
          font-weight: 700;
          color: #fff;
          padding: 3px 8px;
          border-radius: 4px;
          pointer-events: none;
          white-space: nowrap;
        }
        .village-boundary-label::before { display: none !important; }
      `}</style>
    </div>
  );
}
