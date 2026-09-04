// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MapPin, Plus } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";
import { signOut } from "next-auth/react";
import { useProjectStore } from "@/lib/stores/project";
import { getProjects } from "@/lib/api/projects";
import type { Project } from "@/lib/stores/project";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric", month: "short", year: "numeric",
  });
}

function getInitials(user: { email?: string; name?: string }) {
  if (user.name) {
    const parts = user.name.trim().split(/\s+/);
    return (parts[0][0] + (parts[1]?.[0] ?? "")).toUpperCase();
  }
  return user.email?.[0]?.toUpperCase() ?? "U";
}

export default function DashboardPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const { projects, stats, setProjects } = useProjectStore();
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newLocation, setNewLocation] = useState("");
  const hasFetched = useRef(false);

  useEffect(() => {
    if (!isAuthenticated) { router.replace("/"); return; }
    if (hasFetched.current) return;
    hasFetched.current = true;
    getProjects()
      .then(({ projects: p, stats: s }) => { setProjects(p, s); setLoading(false); })
      .catch(() => setLoading(false));
  }, [isAuthenticated, router, setProjects]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name: newName.trim(), location: newLocation.trim() || null }),
    });
    if (res.ok) {
      const proj = await res.json();
      router.push(`/project/${proj.id}`);
    }
  }

  const initials = user ? getInitials({ email: user.email, name: user.name }) : "U";

  return (
    <div style={{ minHeight: "100vh", background: "#F2EDE8", fontFamily: "var(--font-inter)" }}>
      {/* Top nav */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 32px", height: 56,
        background: "rgba(253,252,251,0.85)",
        backdropFilter: "blur(14px)",
        borderBottom: "1px solid #CFD6C4",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <span style={{ fontFamily: "var(--font-space-grotesk)", fontWeight: 800, fontSize: 18, color: "#306223" }}>
          Qnit Builders
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: "50%",
            background: "#306223", color: "#FDFCFB",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 12, fontWeight: 700,
          }}>{initials}</div>
          <button
            onClick={() => signOut({ callbackUrl: "/" })}
            style={{ background: "none", border: "1px solid #CFD6C4", borderRadius: 6, padding: "4px 12px", fontSize: 12, color: "#7B8F83", cursor: "pointer" }}
          >
            Sign out
          </button>
        </div>
      </div>

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "40px 24px" }}>
        {/* Stats */}
        {stats && (
          <div style={{ display: "flex", gap: 16, marginBottom: 40 }}>
            {[
              { label: "Total projects", value: stats.total },
              { label: "Analysed",       value: stats.fully_analysed },
              { label: "Needs review",   value: stats.needs_review },
              { label: "This month",     value: stats.this_month },
            ].map((s) => (
              <div key={s.label} style={{
                flex: 1, background: "rgba(253,252,251,0.8)", border: "1px solid #CFD6C4",
                borderRadius: 12, padding: "20px 24px",
              }}>
                <div style={{ fontSize: 28, fontWeight: 800, color: "#3A3F3B" }}>{s.value}</div>
                <div style={{ fontSize: 12, color: "#7B8F83", marginTop: 4 }}>{s.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* New project form */}
        <div style={{
          background: "rgba(253,252,251,0.8)", border: "1px solid #CFD6C4",
          borderRadius: 12, padding: "24px", marginBottom: 32,
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#3A3F3B", marginBottom: 16 }}>New project</div>
          <form onSubmit={handleCreate} style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Project name"
              required
              style={{
                flex: "1 1 200px", padding: "8px 12px", border: "1px solid #CFD6C4",
                borderRadius: 7, fontSize: 13, background: "#FDFCFB", color: "#3A3F3B",
              }}
            />
            <input
              value={newLocation}
              onChange={(e) => setNewLocation(e.target.value)}
              placeholder="Location (optional)"
              style={{
                flex: "1 1 200px", padding: "8px 12px", border: "1px solid #CFD6C4",
                borderRadius: 7, fontSize: 13, background: "#FDFCFB", color: "#3A3F3B",
              }}
            />
            <button
              type="submit"
              style={{
                background: "#306223", color: "#FDFCFB", border: "none",
                borderRadius: 7, padding: "8px 20px", fontWeight: 700,
                fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
              }}
            >
              <Plus size={14} /> Create
            </button>
          </form>
        </div>

        {/* Project list */}
        {loading ? (
          <div style={{ color: "#7B8F83", fontSize: 14 }}>Loading projects…</div>
        ) : projects.length === 0 ? (
          <div style={{ color: "#7B8F83", fontSize: 14 }}>No projects yet. Create one above.</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
            {projects.map((p: Project) => (
              <div
                key={p.id}
                onClick={() => router.push(`/project/${p.id}`)}
                style={{
                  background: "rgba(253,252,251,0.85)", border: "1px solid #CFD6C4",
                  borderRadius: 12, padding: "20px 22px", cursor: "pointer",
                  transition: "box-shadow 0.15s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 4px 16px rgba(58,63,59,0.12)")}
                onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
              >
                <div style={{ fontWeight: 700, fontSize: 15, color: "#3A3F3B", marginBottom: 6 }}>{p.name}</div>
                {p.location && (
                  <div style={{ display: "flex", alignItems: "center", gap: 4, color: "#7B8F83", fontSize: 12, marginBottom: 8 }}>
                    <MapPin size={11} />{p.location}
                  </div>
                )}
                <div style={{ fontSize: 11, color: "#B8C4BB" }}>{formatDate(p.created_at)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
