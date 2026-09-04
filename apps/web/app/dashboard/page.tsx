// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MapPin, Plus, Layers, BadgeCheck, Clock, CalendarDays } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";
import { signOut } from "next-auth/react";
import { useProjectStore } from "@/lib/stores/project";
import { getProjects } from "@/lib/api/projects";
import type { Project } from "@/lib/stores/project";

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

// ─── Illustrated map thumbnails — 3 watercolour variants ─────────────────────

function CardMapSVG({ variant = 0 }: { variant?: number }) {
  const v = variant % 3;
  if (v === 0) {
    return (
      <svg viewBox="0 0 300 130" xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="xMidYMid slice"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <rect fill="#EAE5DF" width="300" height="130"/>
        <ellipse cx="22" cy="22" rx="22" ry="17" fill="#B5D8E0"/>
        <ellipse cx="10" cy="28" rx="12" ry="10" fill="#C2DFE7"/>
        <line x1="0" y1="48" x2="300" y2="52" stroke="#F3C3B2" strokeWidth="7"/>
        <line x1="0" y1="92" x2="300" y2="88" stroke="#F3C3B2" strokeWidth="9"/>
        <line x1="98" y1="0" x2="94" y2="130" stroke="#F3C3B2" strokeWidth="6"/>
        <line x1="208" y1="0" x2="212" y2="130" stroke="#F3C3B2" strokeWidth="8"/>
        <line x1="0" y1="70" x2="300" y2="72" stroke="#E8CEBE" strokeWidth="3"/>
        <rect x="106" y="4"  width="96"  height="38" rx="3" fill="#CFD6C4" opacity="0.86"/>
        <rect x="216" y="5"  width="80"  height="36" rx="3" fill="#C8D1BB" opacity="0.80"/>
        <rect x="4"   y="56" width="82"  height="26" rx="3" fill="#C8D1BB" opacity="0.76"/>
        <rect x="106" y="58" width="96"  height="26" rx="3" fill="#CFD6C4" opacity="0.82"/>
        <rect x="216" y="57" width="80"  height="26" rx="3" fill="#CFD6C4" opacity="0.78"/>
        <rect x="4"   y="98" width="88"  height="26" rx="3" fill="#CFD6C4" opacity="0.72"/>
        <rect x="106" y="97" width="186" height="26" rx="3" fill="#C8D1BB" opacity="0.74"/>
        <rect x="114" y="10" width="36"  height="22" rx="2" fill="#F0EDE8" opacity="0.92"/>
        <rect x="224" y="10" width="32"  height="20" rx="2" fill="#EDE9E4" opacity="0.88"/>
        <rect x="114" y="62" width="28"  height="16" rx="2" fill="#EDE9E4" opacity="0.85"/>
        <circle cx="158" cy="73" r="10" fill="rgba(153,205,216,0.14)"/>
        <circle cx="158" cy="73" r="5"  fill="#99CDD8"/>
        <circle cx="158" cy="73" r="2"  fill="#FDFCFB"/>
      </svg>
    );
  }
  if (v === 1) {
    return (
      <svg viewBox="0 0 300 130" xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="xMidYMid slice"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <rect fill="#EAE5DF" width="300" height="130"/>
        <ellipse cx="30"  cy="65"  rx="42" ry="58" fill="#B5D8E0"/>
        <ellipse cx="12"  cy="80"  rx="22" ry="38" fill="#C2DFE7"/>
        <ellipse cx="48"  cy="18"  rx="14" ry="10" fill="#BEDDE5" opacity="0.80"/>
        <path d="M60,8 Q76,65 60,122" stroke="#F3C3B2" strokeWidth="8" fill="none"/>
        <line x1="60" y1="48" x2="300" y2="44" stroke="#F3C3B2" strokeWidth="7"/>
        <line x1="60" y1="90" x2="300" y2="92" stroke="#F3C3B2" strokeWidth="9"/>
        <rect x="78"  y="4"  width="80"  height="36" rx="3" fill="#CFD6C4" opacity="0.84"/>
        <rect x="168" y="4"  width="128" height="36" rx="3" fill="#C8D1BB" opacity="0.80"/>
        <rect x="78"  y="52" width="78"  height="30" rx="3" fill="#CFD6C4" opacity="0.78"/>
        <rect x="168" y="52" width="128" height="30" rx="3" fill="#C8D1BB" opacity="0.82"/>
        <rect x="78"  y="98" width="218" height="26" rx="3" fill="#CFD6C4" opacity="0.74"/>
        <circle cx="68" cy="30"  r="9"  fill="#C8DDD5" opacity="0.76"/>
        <circle cx="65" cy="108" r="8"  fill="#C4D9D1" opacity="0.70"/>
        <rect x="86"  y="10" width="30" height="20" rx="2" fill="#F0EDE8" opacity="0.92"/>
        <rect x="176" y="9"  width="36" height="22" rx="2" fill="#EDE9E4" opacity="0.88"/>
        <circle cx="188" cy="70" r="10" fill="rgba(153,205,216,0.14)"/>
        <circle cx="188" cy="70" r="5"  fill="#99CDD8"/>
        <circle cx="188" cy="70" r="2"  fill="#FDFCFB"/>
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 300 130" xmlns="http://www.w3.org/2000/svg"
      preserveAspectRatio="xMidYMid slice"
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
      <rect fill="#EAE5DF" width="300" height="130"/>
      <ellipse cx="244" cy="100" rx="32" ry="22" fill="#B5D8E0"/>
      <ellipse cx="260" cy="108" rx="16" ry="12" fill="#C2DFE7"/>
      <circle cx="38"  cy="38"  r="24" fill="#C8DDD5" opacity="0.70"/>
      <circle cx="58"  cy="96"  r="17" fill="#C4D9D1" opacity="0.66"/>
      <circle cx="262" cy="26"  r="19" fill="#C8DDD5" opacity="0.64"/>
      <line x1="0"   y1="76"  x2="300" y2="58"  stroke="#F3C3B2" strokeWidth="9"/>
      <line x1="138" y1="0"   x2="132" y2="130" stroke="#F3C3B2" strokeWidth="7"/>
      <line x1="0"   y1="110" x2="300" y2="106" stroke="#E8CEBE" strokeWidth="3"/>
      <rect x="72"  y="6"   width="58" height="44" rx="3" fill="#CFD6C4" opacity="0.82"/>
      <rect x="148" y="10"  width="64" height="36" rx="3" fill="#C8D1BB" opacity="0.78"/>
      <rect x="6"   y="88"  width="74" height="36" rx="3" fill="#CFD6C4" opacity="0.76"/>
      <rect x="148" y="88"  width="64" height="36" rx="3" fill="#C8D1BB" opacity="0.74"/>
      <rect x="80"  y="14"  width="28" height="20" rx="2" fill="#F0EDE8" opacity="0.92"/>
      <rect x="156" y="16"  width="32" height="18" rx="2" fill="#EDE9E4" opacity="0.88"/>
      <circle cx="150" cy="62" r="10" fill="rgba(153,205,216,0.14)"/>
      <circle cx="150" cy="62" r="5"  fill="#99CDD8"/>
      <circle cx="150" cy="62" r="2"  fill="#FDFCFB"/>
    </svg>
  );
}

// ─── Project card ─────────────────────────────────────────────────────────────

function ProjectCard({ project, onClick, mapVariant }: {
  project: Project;
  onClick: () => void;
  mapVariant: number;
}) {
  const isComplete = project.status === "complete";
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      style={{
        background: "#FDFCFB",
        border: "1px solid #CFD6C4",
        borderRadius: 16,
        overflow: "hidden",
        cursor: "pointer",
        transition: "transform 0.18s ease, box-shadow 0.18s ease",
        boxShadow: "0 1px 4px rgba(48,98,35,0.07), 0 4px 16px rgba(48,98,35,0.05)",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.transform = "translateY(-3px)";
        (e.currentTarget as HTMLDivElement).style.boxShadow = "0 8px 32px rgba(48,98,35,0.16), 0 2px 8px rgba(48,98,35,0.08)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.transform = "translateY(0)";
        (e.currentTarget as HTMLDivElement).style.boxShadow = "0 1px 4px rgba(48,98,35,0.07), 0 4px 16px rgba(48,98,35,0.05)";
      }}
    >
      {/* Map thumbnail */}
      <div style={{ position: "relative", height: 130, overflow: "hidden" }}>
        <CardMapSVG variant={mapVariant} />
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0, height: 48,
          background: "linear-gradient(to bottom, transparent, rgba(253,252,251,0.92))",
          pointerEvents: "none",
        }}/>
        <span style={{
          position: "absolute", top: 10, right: 10,
          padding: "3px 10px", borderRadius: 9999,
          fontSize: 10, fontWeight: 600, letterSpacing: "0.2px",
          backdropFilter: "blur(6px)",
          background: isComplete ? "rgba(228,240,232,0.88)" : "rgba(248,237,224,0.88)",
          color: isComplete ? "#5A8F6A" : "#C4865A",
          border: `1px solid ${isComplete ? "rgba(90,143,106,0.28)" : "rgba(196,134,90,0.28)"}`,
        }}>
          {isComplete ? "Complete" : "Needs review"}
        </span>
      </div>

      {/* Card body */}
      <div style={{ padding: "12px 16px 14px" }}>
        <div style={{
          fontSize: 14, fontWeight: 700, color: "#3A3F3B",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          letterSpacing: "-0.2px",
        }}>{project.name}</div>

        {project.location && (
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 3 }}>
            <MapPin size={10} color="#B8C4BB" style={{ flexShrink: 0 }} />
            <span style={{ fontSize: 11, color: "#7B8F83", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {project.location}
            </span>
          </div>
        )}

        <div style={{ height: 1, background: "#F0EDE9", margin: "10px 0" }}/>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 3,
            padding: "2px 7px", borderRadius: 9999,
            fontSize: 9, fontWeight: 700, letterSpacing: "0.3px",
            background: "rgba(48,98,35,0.08)", color: "#306223",
            border: "1px solid rgba(48,98,35,0.20)",
          }}>CADASTRAL</span>
          <span style={{ fontSize: 10, color: "#B8C4BB" }}>{formatDate(project.created_at)}</span>
        </div>
      </div>
    </div>
  );
}

// ─── New project card ─────────────────────────────────────────────────────────

function NewProjectCard({ onSubmit }: { onSubmit: (name: string, location: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [loc, setLoc]   = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    await onSubmit(name.trim(), loc.trim());
    setBusy(false);
    setName(""); setLoc("");
  }

  return (
    <div style={{
      position: "relative", overflow: "hidden",
      borderRadius: 16, border: "1.5px dashed #AECBD2",
      background: "linear-gradient(145deg, #F8FAFB 0%, #F2EFF9 100%)",
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 14,
      padding: "28px 20px",
    }}>
      {/* Decorative rings */}
      <svg style={{ position: "absolute", bottom: -20, right: -20, opacity: 0.10, pointerEvents: "none" }}
        width="110" height="110" viewBox="0 0 110 110" fill="none">
        <circle cx="55" cy="55" r="50" stroke="#99CDD8" strokeWidth="1"/>
        <circle cx="55" cy="55" r="35" stroke="#99CDD8" strokeWidth="1"/>
        <circle cx="55" cy="55" r="20" stroke="#99CDD8" strokeWidth="1"/>
      </svg>

      <div style={{
        width: 44, height: 44, borderRadius: "50%",
        border: "1.5px solid #99CDD8",
        background: "rgba(153,205,216,0.10)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Plus size={20} color="#99CDD8" strokeWidth={1.5} />
      </div>

      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#3A3F3B", letterSpacing: "-0.1px" }}>New project</div>
        <div style={{ fontSize: 11, color: "#7B8F83", marginTop: 3 }}>Add a site to explore</div>
      </div>

      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%" }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Project name"
          required
          style={{
            width: "100%", padding: "7px 10px", border: "1px solid #CFD6C4",
            borderRadius: 8, fontSize: 12, background: "#FDFCFB", color: "#3A3F3B",
            boxSizing: "border-box", outline: "none",
          }}
        />
        <input
          value={loc}
          onChange={(e) => setLoc(e.target.value)}
          placeholder="Location (optional)"
          style={{
            width: "100%", padding: "7px 10px", border: "1px solid #CFD6C4",
            borderRadius: 8, fontSize: 12, background: "#FDFCFB", color: "#3A3F3B",
            boxSizing: "border-box", outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={busy || !name.trim()}
          style={{
            padding: "8px 0", background: busy || !name.trim() ? "#7B8F83" : "#306223",
            color: "#FDFCFB", border: "none", borderRadius: 9999,
            fontSize: 11, fontWeight: 600, cursor: busy ? "wait" : "pointer",
            letterSpacing: "0.2px", transition: "background 0.15s",
          }}
        >
          {busy ? "Creating…" : "Create project →"}
        </button>
      </form>
    </div>
  );
}

// ─── Ghost card ───────────────────────────────────────────────────────────────

function GhostCard({ variant }: { variant: number }) {
  return (
    <div style={{
      background: "#FDFCFB", border: "1px solid #E4DDD6",
      borderRadius: 16, overflow: "hidden", opacity: 0.28, pointerEvents: "none",
    }}>
      <div style={{ height: 130, position: "relative", overflow: "hidden" }}>
        <CardMapSVG variant={variant} />
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0, height: 48,
          background: "linear-gradient(to bottom, transparent, rgba(253,252,251,0.92))",
        }}/>
      </div>
      <div style={{ padding: "12px 16px 14px" }}>
        <div style={{ height: 11, background: "#E4DDD6", borderRadius: 6, marginBottom: 7, width: "70%" }}/>
        <div style={{ height: 9,  background: "#E4DDD6", borderRadius: 6, marginBottom: 12, width: "45%" }}/>
        <div style={{ height: 1,  background: "#F0EDE9", marginBottom: 10 }}/>
        <div style={{ height: 16, background: "#E4DDD6", borderRadius: 9999, width: 70 }}/>
      </div>
    </div>
  );
}

// ─── Stat card ────────────────────────────────────────────────────────────────

const STAT_META = [
  { accent: "#99CDD8", tint: "rgba(153,205,216,0.08)", icon: Layers,       iconColor: "#99CDD8", label: "Total projects"  },
  { accent: "#5A8F6A", tint: "rgba(90,143,106,0.07)",  icon: BadgeCheck,   iconColor: "#5A8F6A", label: "Fully analysed"  },
  { accent: "#C4865A", tint: "rgba(196,134,90,0.07)",  icon: Clock,        iconColor: "#C4865A", label: "Needs review"    },
  { accent: "#306223", tint: "rgba(48,98,35,0.07)",    icon: CalendarDays, iconColor: "#306223", label: "This month"      },
] as const;

function StatCard({ num, meta, dim }: { num: number; meta: typeof STAT_META[number]; dim: boolean }) {
  const Icon = meta.icon;
  return (
    <div style={{
      position: "relative", overflow: "hidden",
      background: dim ? "#FDFCFB" : meta.tint,
      border: "1px solid #CFD6C4",
      borderTop: `2.5px solid ${dim ? "#CFD6C4" : meta.accent}`,
      borderRadius: 14, padding: "20px 22px 18px",
    }}>
      <svg style={{ position: "absolute", top: 0, right: 0, opacity: dim ? 0.04 : 0.10, pointerEvents: "none" }}
        width="80" height="80" viewBox="0 0 80 80" fill="none">
        {[0,16,32,48,64].flatMap(x => [0,16,32,48,64].map(y =>
          <circle key={`${x}-${y}`} cx={x} cy={y} r="1.5" fill={meta.accent}/>
        ))}
      </svg>
      <div style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 32, height: 32, borderRadius: 8, marginBottom: 14,
        background: dim ? "rgba(48,98,35,0.06)" : `${meta.accent}18`,
        border: `1px solid ${dim ? "#CFD6C4" : `${meta.accent}30`}`,
      }}>
        <Icon size={15} color={dim ? "#B8C4BB" : meta.iconColor} />
      </div>
      <div style={{
        fontSize: 36, fontWeight: 700, lineHeight: 1, letterSpacing: "-1px",
        color: dim ? "#B8C4BB" : "#3A3F3B",
      }}>{num}</div>
      <div style={{
        fontSize: 11, fontWeight: 500, marginTop: 6, letterSpacing: "0.2px",
        color: dim ? "#B8C4BB" : "#7B8F83", textTransform: "uppercase",
      }}>{meta.label}</div>
      {!dim && (
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0, height: 2,
          background: `linear-gradient(to right, ${meta.accent}40, transparent)`,
        }}/>
      )}
    </div>
  );
}

// ─── Welcome hero (empty state) ───────────────────────────────────────────────

function WelcomeHero() {
  return (
    <div style={{
      display: "flex", borderRadius: 16, overflow: "hidden",
      border: "1px solid #CFD6C4", marginBottom: 28,
      boxShadow: "0 2px 12px rgba(48,98,35,0.07)",
    }}>
      <div style={{
        flex: "0 0 58%", padding: "32px 36px",
        background: "linear-gradient(135deg, #F5F0EB 0%, #EEE9E3 100%)",
      }}>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "4px 10px", borderRadius: 9999, marginBottom: 16,
          background: "rgba(153,205,216,0.14)", border: "1px solid rgba(153,205,216,0.30)",
          fontSize: 10, fontWeight: 600, color: "#3A6A99", letterSpacing: "0.4px",
        }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#99CDD8", display: "inline-block" }}/>
          KARNATAKA CADASTRAL
        </div>
        <div style={{
          fontSize: 22, fontWeight: 700, color: "#3A3F3B",
          lineHeight: 1.25, letterSpacing: "-0.4px", marginBottom: 10,
        }}>
          Explore land records,<br/>survey by survey.
        </div>
        <div style={{ fontSize: 13, color: "#7B8F83", lineHeight: 1.65, marginBottom: 18, maxWidth: 340 }}>
          Navigate Karnataka&apos;s e-Chawadi data — district to village to parcel — with survey number search and boundary overlays.
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
          {[
            { label: "District → Village", color: "#5B93C9" },
            { label: "Survey search",      color: "#306223"  },
            { label: "Parcel boundaries",  color: "#8B6FCB"  },
          ].map(({ label, color }) => (
            <span key={label} style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "4px 10px", borderRadius: 9999,
              fontSize: 11, fontWeight: 500,
              background: `${color}14`, color,
              border: `1px solid ${color}28`,
            }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: color }}/>
              {label}
            </span>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, position: "relative", overflow: "hidden", background: "#EAE5DF" }}>
        <CardMapSVG variant={0} />
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const { projects, stats, setProjects } = useProjectStore();
  const [loading, setLoading] = useState(true);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (!isAuthenticated) { router.replace("/"); return; }
    if (hasFetched.current) return;
    hasFetched.current = true;
    getProjects()
      .then(({ projects: p, stats: s }) => { setProjects(p, s); setLoading(false); })
      .catch(() => setLoading(false));
  }, [isAuthenticated, router, setProjects]);

  async function handleCreate(name: string, location: string) {
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name, location: location || null }),
    });
    if (res.ok) {
      const proj = await res.json();
      router.push(`/project/${proj.id}`);
    }
  }

  const initials = user ? getInitials({ email: user.email, name: user.name }) : "U";
  const isEmpty  = !loading && projects.length === 0;
  const statNums = [
    stats?.total          ?? 0,
    stats?.fully_analysed ?? 0,
    stats?.needs_review   ?? 0,
    stats?.this_month     ?? 0,
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#F2EDE8", fontFamily: "var(--font-inter)" }}>

      {/* Top nav */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 32px", height: 56,
        background: "rgba(253,252,251,0.90)",
        backdropFilter: "blur(14px)",
        borderBottom: "1px solid #CFD6C4",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <span style={{ fontWeight: 800, fontSize: 17, color: "#306223", letterSpacing: "-0.3px" }}>
          Qnit Builders
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 32, height: 32, borderRadius: "50%",
            background: "#306223", color: "#FDFCFB",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 12, fontWeight: 700,
          }}>{initials}</div>
          {user?.name && (
            <span style={{ fontSize: 12, color: "#7B8F83" }}>{user.name}</span>
          )}
          <button
            onClick={() => signOut({ callbackUrl: "/" })}
            style={{
              background: "none", border: "1px solid #CFD6C4", borderRadius: 6,
              padding: "4px 12px", fontSize: 12, color: "#7B8F83", cursor: "pointer",
            }}
          >Sign out</button>
        </div>
      </div>

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "36px 24px 60px" }}>

        {/* Stat cards */}
        {stats && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 36 }}>
            {STAT_META.map((meta, i) => (
              <StatCard key={meta.label} num={statNums[i]} meta={meta} dim={statNums[i] === 0} />
            ))}
          </div>
        )}

        {/* Welcome hero — only on first visit (empty state) */}
        {isEmpty && <WelcomeHero />}

        {/* Section header */}
        <div style={{
          display: "flex", alignItems: "flex-end", justifyContent: "space-between",
          marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid #CFD6C4",
        }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
              <svg width="18" height="14" viewBox="0 0 18 14" fill="none" aria-hidden>
                <rect x="0"  y="0"  width="7"  height="7"  rx="1.5" fill="#CFD6C4"/>
                <rect x="9"  y="0"  width="9"  height="3"  rx="1.5" fill="#CFD6C4"/>
                <rect x="9"  y="5"  width="6"  height="3"  rx="1.5" fill="#CFD6C4" opacity="0.6"/>
                <rect x="0"  y="9"  width="18" height="2"  rx="1"   fill="#CFD6C4" opacity="0.5"/>
                <rect x="0"  y="13" width="12" height="1"  rx="0.5" fill="#CFD6C4" opacity="0.35"/>
              </svg>
              <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.8px", color: "#B8C4BB", textTransform: "uppercase" }}>
                Qnit Builders
              </span>
            </div>
            <h1 style={{ fontSize: 28, fontWeight: 800, color: "#3A3F3B", letterSpacing: "-0.6px", lineHeight: 1, margin: 0 }}>
              Projects
            </h1>
            {!isEmpty && stats && (
              <div style={{ marginTop: 8 }}>
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  padding: "3px 9px", borderRadius: 9999, fontSize: 11, fontWeight: 500,
                  background: "rgba(90,143,106,0.09)", color: "#5A8F6A",
                  border: "1px solid rgba(90,143,106,0.20)",
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#5A8F6A" }}/>
                  {stats.total} project{stats.total !== 1 ? "s" : ""}
                </span>
              </div>
            )}
          </div>
          <svg width="80" height="50" viewBox="0 0 80 50" fill="none" aria-hidden style={{ opacity: 0.50, flexShrink: 0 }}>
            <ellipse cx="40" cy="25" rx="39" ry="24" stroke="#CFD6C4" strokeWidth="0.8"/>
            <ellipse cx="40" cy="25" rx="28" ry="17" stroke="#CFD6C4" strokeWidth="0.8"/>
            <ellipse cx="40" cy="25" rx="17" ry="10" stroke="#CFD6C4" strokeWidth="0.8"/>
            <circle  cx="40" cy="25" r="4"            fill="#99CDD8"  opacity="0.7"/>
            <circle  cx="40" cy="25" r="1.5"          fill="#FDFCFB"/>
          </svg>
        </div>

        {/* Project grid */}
        {loading ? (
          <div style={{ color: "#7B8F83", fontSize: 14 }}>Loading projects…</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))", gap: 16 }}>
            {projects.map((p: Project, i: number) => (
              <ProjectCard
                key={p.id}
                project={p}
                mapVariant={i}
                onClick={() => router.push(`/project/${p.id}`)}
              />
            ))}
            <NewProjectCard onSubmit={handleCreate} />
            {isEmpty && <GhostCard variant={1} />}
            {isEmpty && <GhostCard variant={2} />}
          </div>
        )}
      </div>
    </div>
  );
}
