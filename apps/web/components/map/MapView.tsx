// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

// Phase 1A — Leaflet map with Karnataka Cadastral toolbar.
// Renders loaded GeoJSON parcel layer on top of OSM base tiles.
// No parcel click handler, no overlays, no analysis cards yet.

"use client";

import { useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import type { Map as LeafletMap, Layer, GeoJSONOptions } from "leaflet";
import { CadastralToolbar } from "./CadastralToolbar";
import "leaflet/dist/leaflet.css";

// Karnataka centroid — default map center
const KA_CENTER: [number, number] = [15.3173, 75.7139];
const KA_ZOOM = 7;

function ParcelLayer({ fc }: { fc: GeoJSON.FeatureCollection }) {
  const map = useMap();

  const options: GeoJSONOptions = {
    style: () => ({
      color: "#306223",
      weight: 1,
      opacity: 0.8,
      fillColor: "#306223",
      fillOpacity: 0.08,
    }),
    onEachFeature: (feature, layer: Layer) => {
      const surveyNo = (feature.properties as Record<string, string>)?.survey_no;
      if (surveyNo) {
        layer.bindTooltip(surveyNo, { permanent: false, sticky: true, className: "cadastral-tooltip" });
      }
    },
  };

  // Fly to bounds of loaded data
  const ref = useRef(false);
  if (!ref.current) {
    ref.current = true;
    // defer so the component mounts first
    setTimeout(() => {
      try {
        // Compute bounding box from features
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
  }

  return <GeoJSON key={JSON.stringify(fc.bbox ?? fc.features.length)} data={fc} {...options} />;
}

export function MapView() {
  const [parcelFc, setParcelFc] = useState<GeoJSON.FeatureCollection | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Cadastral toolbar sits above the map */}
      <CadastralToolbar onLoad={(fc) => setParcelFc(fc)} />

      {/* Map fills remaining height */}
      <div style={{ flex: 1, position: "relative" }}>
        <MapContainer
          center={KA_CENTER}
          zoom={KA_ZOOM}
          style={{ height: "100%", width: "100%" }}
          ref={mapRef}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            maxZoom={19}
          />
          {parcelFc && <ParcelLayer fc={parcelFc} />}
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
        .leaflet-tooltip-top:before,
        .leaflet-tooltip-bottom:before,
        .leaflet-tooltip-left:before,
        .leaflet-tooltip-right:before {
          border-color: transparent;
        }
      `}</style>
    </div>
  );
}
