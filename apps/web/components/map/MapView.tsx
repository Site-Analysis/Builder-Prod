// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1D — parcel click popup + survey search fly-to added.
// Phase 2  — village boundary overlay (green, auto) + nearby hobli boundaries (red, toggle).

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L, { type Map as LeafletMap, type Layer, type GeoJSONOptions } from "leaflet";
import { CadastralToolbar } from "./CadastralToolbar";
import {
  fetchParcelData, fetchVillageBoundary, fetchHobliBoundaries,
  type SearchResult,
} from "@/lib/api/cadastral_records";
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

function VillageBoundaryLayer({
  fc,
  color,
  colorFn,
  weight = 2,
  labelPermanent = false,
}: {
  fc: GeoJSON.FeatureCollection;
  color?: string;
  colorFn?: (feature: GeoJSON.Feature) => string;
  weight?: number;
  labelPermanent?: boolean;
}) {
  const options: GeoJSONOptions = {
    style: (feature) => {
      const c = colorFn ? colorFn(feature!) : (color ?? "#306223");
      return {
        color: c,
        weight,
        opacity: 0.9,
        fillColor: c,
        fillOpacity: weight >= 3 ? 0.10 : 0.04,
        dashArray: weight < 3 ? "6 4" : undefined,
      };
    },
    onEachFeature: (feature: GeoJSON.Feature, layer: Layer) => {
      const name = (feature.properties as Record<string, string>)?.village_name;
      if (name) {
        layer.bindTooltip(name, {
          permanent: labelPermanent,
          sticky: !labelPermanent,
          direction: "center",
          className: labelPermanent ? "village-boundary-label" : "cadastral-tooltip",
        });
      }
    },
  };
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

interface VillageCoords { dist: string; taluk: string; hobli: string; vlg: string; }

export function MapView() {
  const [parcelFc, setParcelFc]             = useState<GeoJSON.FeatureCollection | null>(null);
  const [loadKey, setLoadKey]               = useState(0);
  const [clickedSurveyNo, setClickedSurveyNo] = useState<string | null>(null);
  const [mapLayer, setMapLayer]             = useState<"base" | "satellite">("base");
  const [villageBoundaryFc, setVillageBoundaryFc] = useState<GeoJSON.FeatureCollection | null>(null);
  const [hobliBoundaryFc, setHobliBoundaryFc]     = useState<GeoJSON.FeatureCollection | null>(null);
  const [showNearby, setShowNearby]         = useState(false);
  const [villageBoundaryKey, setVillageBoundaryKey] = useState(0);
  const [hobliBoundaryKey, setHobliBoundaryKey]     = useState(0);
  const mapRef        = useRef<LeafletMap | null>(null);
  const hobliKeyRef   = useRef<string | null>(null);
  const currentHierRef = useRef<VillageCoords | null>(null);
  const { isMobile }  = useIsMobile();

  const loadedSurveyNos = useMemo<Set<string>>(() => {
    if (!parcelFc) return new Set();
    const s = new Set<string>();
    for (const f of parcelFc.features) {
      const no = (f.properties as Record<string, string>)?.survey_no;
      if (no) s.add(no);
    }
    return s;
  }, [parcelFc]);

  async function loadBoundaries(hier: VillageCoords, nearby: boolean) {
    currentHierRef.current = hier;
    const bf = await fetchVillageBoundary(hier.dist, hier.taluk, hier.hobli, hier.vlg);
    setVillageBoundaryFc(bf);
    setVillageBoundaryKey((k) => k + 1);
    if (nearby) {
      const hkey = `${hier.dist}-${hier.taluk}-${hier.hobli}`;
      if (hobliKeyRef.current !== hkey) {
        hobliKeyRef.current = hkey;
        const hb = await fetchHobliBoundaries(hier.dist, hier.taluk, hier.hobli);
        setHobliBoundaryFc(hb);
        setHobliBoundaryKey((k) => k + 1);
      }
    }
  }

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
    loadBoundaries({ dist: result.dist, taluk: result.taluk, hobli: result.hobli, vlg: result.vlg }, showNearby);
    // fly happens after react-leaflet re-renders; small delay lets the layer mount
    setTimeout(() => {
      if (mapRef.current) flyToBounds(mapRef.current, fc, result.survey_no);
    }, 80);
  }

  async function handleNearbyToggle() {
    const next = !showNearby;
    setShowNearby(next);
    const h = currentHierRef.current;
    if (next && h) {
      const hkey = `${h.dist}-${h.taluk}-${h.hobli}`;
      if (hobliKeyRef.current !== hkey) {
        hobliKeyRef.current = hkey;
        const hb = await fetchHobliBoundaries(h.dist, h.taluk, h.hobli);
        setHobliBoundaryFc(hb);
        setHobliBoundaryKey((k) => k + 1);
      }
    }
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Cadastral toolbar sits above the map */}
      <CadastralToolbar
        onLoad={(fc, _label, hier) => {
          setParcelFc(fc);
          setLoadKey((k) => k + 1);
          setClickedSurveyNo(null);
          if (fc && hier) loadBoundaries(hier, showNearby);
        }}
        onSearch={handleSearchResult}
        onHighlight={handleHighlight}
        onFlyTo={handleFlyTo}
        loadedSurveyNos={loadedSurveyNos}
      />

      {/* Map fills remaining height */}
      <div style={{ flex: 1, position: "relative", zIndex: 1 }}>
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
          <button onClick={handleNearbyToggle} style={{
            padding: isMobile ? "9px 13px" : "5px 10px", fontSize: isMobile ? 12 : 11, fontWeight: 600, cursor: "pointer",
            border: "none", fontFamily: "inherit",
            background: showNearby ? "#7B8F83" : "#FDFCFB",
            color: showNearby ? "#FDFCFB" : "#7B8F83",
          }}>
            Nearby
          </button>
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
          {/* Nearby hobli boundaries — green if LGD name exists, red if not */}
          {showNearby && hobliBoundaryFc && (
            <VillageBoundaryLayer
              key={`hb-${hobliBoundaryKey}`}
              fc={hobliBoundaryFc}
              colorFn={(f) => (f.properties as Record<string, string>)?.village_name ? "#16A34A" : "#e53e3e"}
              weight={1.5}
            />
          )}
          {parcelFc && <ParcelLayer key={loadKey} fc={parcelFc} onParcelClick={setClickedSurveyNo} mapLayer={mapLayer} />}
          {/* Loaded village boundary — blue, distinct from nearby, hover tooltip */}
          {villageBoundaryFc && (
            <VillageBoundaryLayer key={`vb-${villageBoundaryKey}`} fc={villageBoundaryFc} color="#2563EB" weight={3} />
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
