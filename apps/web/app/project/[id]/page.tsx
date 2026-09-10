// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";
import { useProjectStore } from "@/lib/stores/project";
import { getProject } from "@/lib/api/projects";
import { useIsMobile } from "@/lib/useIsMobile";
import dynamic from "next/dynamic";
const MapView = dynamic(() => import("@/components/map/MapView").then((m) => m.MapView), { ssr: false });

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isBypass = process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1";
  const { isMobile } = useIsMobile();
  const { currentProject, setCurrentProject } = useProjectStore();
  const [loading, setLoading] = useState(!currentProject || currentProject.id !== id);
  const fetched = useRef(false);

  useEffect(() => {
    if (!isBypass && !isAuthenticated) { router.replace("/"); return; }
    if (fetched.current) return;
    if (currentProject?.id === id) { setLoading(false); return; }
    fetched.current = true;
    getProject(id)
      .then((p) => { setCurrentProject(p); setLoading(false); })
      .catch(() => setLoading(false));
  }, [id, isAuthenticated, router, currentProject, setCurrentProject]);

  if (loading) {
    return (
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#F2EDE8" }}>
        <span style={{ color: "#7B8F83", fontSize: 14 }}>Loading…</span>
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#F2EDE8" }}>
      {/* Top bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: isMobile ? "0 12px" : "0 20px", height: isMobile ? 52 : 48,
        background: "rgba(253,252,251,0.9)",
        backdropFilter: "blur(14px)",
        borderBottom: "1px solid #CFD6C4",
        flexShrink: 0, zIndex: 200,
      }}>
        <button
          onClick={() => router.push("/dashboard")}
          style={{ background: "none", border: "none", cursor: "pointer", color: "#7B8F83", display: "flex", alignItems: "center", gap: 4, fontSize: 12, minHeight: 44, padding: "0 4px" }}
        >
          <ArrowLeft size={14} />{!isMobile && " Dashboard"}
        </button>
        <span style={{ color: "#CFD6C4" }}>|</span>
        <span style={{ fontWeight: 700, fontSize: 14, color: "#3A3F3B", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {currentProject?.name ?? id}
        </span>
        {currentProject?.location && !isMobile && (
          <span style={{ fontSize: 12, color: "#7B8F83", flexShrink: 0 }}>— {currentProject.location}</span>
        )}
      </div>

      {/* Map fills remaining height */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <MapView />
      </div>
    </div>
  );
}
