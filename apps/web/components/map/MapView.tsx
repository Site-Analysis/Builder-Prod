// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1D — parcel click popup + survey search fly-to added.

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L, { type Map as LeafletMap, type Layer, type GeoJSONOptions } from "leaflet";
import { CadastralToolbar } from "./CadastralToolbar";
import { fetchParcelData, type SearchResult } from "@/lib/api/cadastral_records";
import { useIsMobile } from "@/lib/useIsMobile";
import "leaflet/dist/leaflet.css";

// Karnataka centroid — default map center
const KA_CENTER: [number, number] = [15.3173, 75.7139];
const KA_ZOOM = 7;

const TOOLTIP_THRESHOLD = 500;
const PERMANENT_LABEL_THRESHOLD = 1500;

function ParcelLayer({
  fc,
  onParcelClick,
  mapLayer,
}: {
  fc: GeoJSON.FeatureCollection;
  onParcelClick: (no: string) => void;
  mapLayer: "base" | "satellite";
}) {
  const map = useMap();
  const showPermanent = fc.features.length <= PERMANENT_LABEL_THRESHOLD;
  const showTooltips  = fc.features.length <= TOOLTIP_THRESHOLD;
  const renderer = useMemo(() => L.canvas({ padding: 0.5 }), []);
  const isSat = mapLayer === "satellite";

  const options = {
    renderer,
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
      layer.on("click", () => onParcelClick(surveyNo));
    },
  };

  // Fly to bounds whenever this component mounts (keyed per load in MapView).
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        const coords = fc.features.flatMap((f) => {
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

  return <GeoJSON data={fc} {...options} />;
}

function flyToBounds(map: LeafletMap, fc: GeoJSON.FeatureCollection, surveyNo?: string) {
  const target = surveyNo
    ? fc.features.find((f) => (f.properties as Record<string, string>)?.survey_no === surveyNo)
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

export function MapView() {
  const [parcelFc, setParcelFc] = useState<GeoJSON.FeatureCollection | null>(null);
  const [loadKey, setLoadKey] = useState(0);
  const [clickedSurveyNo, setClickedSurveyNo] = useState<string | null>(null);
  const [mapLayer, setMapLayer] = useState<"base" | "satellite">("base");
  const mapRef = useRef<LeafletMap | null>(null);
  const { isMobile } = useIsMobile();

  const loadedSurveyNos = useMemo<Set<string>>(() => {
    if (!parcelFc) return new Set();
    const s = new Set<string>();
    for (const f of parcelFc.features) {
      const no = (f.properties as Record<string, string>)?.survey_no;
      if (no) s.add(no);
    }
    return s;
  }, [parcelFc]);

  function handleFlyTo(coords: { lat: number; lon: number }) {
    mapRef.current?.setView([coords.lat, coords.lon], 16);
  }

  function handleHighlight(result: SearchResult) {
    if (mapRef.current && parcelFc) {
      flyToBounds(mapRef.current, parcelFc, result.survey_no);
    }
  }

  async function handleSearchResult(result: SearchResult) {
    const fc = await fetchParcelData(result.dist, result.taluk, result.hobli, result.vlg);
    if (!fc) return;
    setParcelFc(fc);
    setLoadKey((k) => k + 1);
    setClickedSurveyNo(null);
    // fly happens after react-leaflet re-renders; small delay lets the layer mount
    setTimeout(() => {
      if (mapRef.current) flyToBounds(mapRef.current, fc, result.survey_no);
    }, 80);
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Cadastral toolbar sits above the map */}
      <CadastralToolbar
        onLoad={(fc) => {
          setParcelFc(fc);
          setLoadKey((k) => k + 1);
          setClickedSurveyNo(null);
        }}
        onSearch={handleSearchResult}
        onHighlight={handleHighlight}
        onFlyTo={handleFlyTo}
        loadedSurveyNos={loadedSurveyNos}
      />

      {/* Map fills remaining height */}
      <div style={{ flex: 1, position: "relative", zIndex: 1 }}>
        {/* Map layer toggle */}
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
        </div>

        {clickedSurveyNo && (
          <div style={{
            position: "absolute", bottom: isMobile ? 12 : 48, left: 12, zIndex: 1000,
            background: "rgba(48,98,35,0.9)", color: "#FDFCFB",
            padding: isMobile ? "8px 12px 8px 14px" : "5px 10px 5px 12px", borderRadius: 6, fontSize: isMobile ? 13 : 12, fontWeight: 700,
            display: "flex", alignItems: "center", gap: 8,
            boxShadow: "0 2px 10px rgba(0,0,0,0.22)", letterSpacing: "0.01em",
          }}>
            Survey {clickedSurveyNo}
            <span
              onClick={() => setClickedSurveyNo(null)}
              style={{ cursor: "pointer", opacity: 0.65, fontSize: 16, lineHeight: 1, fontWeight: 400 }}
            >×</span>
          </div>
        )}
        <MapContainer
          center={KA_CENTER}
          zoom={KA_ZOOM}
          style={{ height: "100%", width: "100%" }}
          ref={mapRef}
        >
          <TileLayer key={mapLayer} {...TILES[mapLayer]} />
          {parcelFc && <ParcelLayer key={loadKey} fc={parcelFc} onParcelClick={setClickedSurveyNo} mapLayer={mapLayer} />}
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
      `}</style>
    </div>
  );
}
