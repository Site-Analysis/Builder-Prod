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
import { useIsMobile } from "@/lib/useIsMobile";

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

// ─── Building hero ────────────────────────────────────────────────────────────

function BuildingHero({ isMobile }: { isMobile: boolean }) {
  return (
    <div style={{
      position: "relative", overflow: "hidden",
      borderRadius: 16, border: "1px solid #CFD6C4",
      display: "flex", flexDirection: isMobile ? "column" : "row", marginBottom: isMobile ? 20 : 36,
      boxShadow: "0 2px 12px rgba(48,98,35,0.07)",
    }}>
      {/* Left: text */}
      <div style={{
        flex: isMobile ? "none" : "0 0 52%", padding: isMobile ? "20px 18px" : "32px 36px",
        background: "linear-gradient(135deg, #F5F0EB 0%, #EEE9E3 100%)",
        display: "flex", flexDirection: "column", justifyContent: "center", gap: 0,
      }}>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 14,
          padding: "4px 10px", borderRadius: 9999, width: "fit-content",
          background: "rgba(48,98,35,0.08)", border: "1px solid rgba(48,98,35,0.18)",
          fontSize: 10, fontWeight: 700, letterSpacing: "0.7px", color: "#306223",
          textTransform: "uppercase",
        }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#306223" }}/>
          Qnit Builders
        </div>
        <div style={{
          fontSize: isMobile ? 18 : 22, fontWeight: 800, color: "#3A3F3B",
          lineHeight: 1.2, letterSpacing: "-0.5px", marginBottom: 10,
          fontFamily: "var(--font-space-grotesk, inherit)",
        }}>
          Karnataka&apos;s land records,<br />down to every parcel.
        </div>
        <div style={{ fontSize: 12, color: "#7B8F83", lineHeight: 1.6, marginBottom: 18, maxWidth: 320 }}>
          Navigate Karnataka land records — district to village — with survey search and parcel boundary overlays.
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {[
            { label: "District → Village", color: "#5B93C9" },
            { label: "Survey search",      color: "#306223" },
            { label: "Parcel boundaries",  color: "#8B6FCB" },
          ].map(({ label, color }) => (
            <span key={label} style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "4px 10px", borderRadius: 9999,
              fontSize: 11, fontWeight: 500,
              background: `${color}14`, color,
              border: `1px solid ${color}28`,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }}/>
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Right: architectural building SVG (hidden on mobile) */}
      {!isMobile && <div style={{ flex: 1, position: "relative", background: "#E8E3DC", overflow: "hidden" }}>
        <svg viewBox="0 0 420 180" xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="xMidYMid slice"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>

          {/* Sky */}
          <rect width="420" height="180" fill="#EAE5DF"/>

          {/* Subtle dot grid texture */}
          {[40,80,120,160,200,240,280,320,360,400].flatMap(x =>
            [20,50,80,110,140].map(y =>
              <circle key={`${x}-${y}`} cx={x} cy={y} r="1" fill="#C8C3BB" opacity="0.5"/>
            )
          )}

          {/* Ground plane */}
          <rect x="0" y="155" width="420" height="25" fill="#C8C3BB" opacity="0.4"/>
          <line x1="0" y1="155" x2="420" y2="155" stroke="#A8A39C" strokeWidth="0.8"/>

          {/* Ground shadow under building */}
          <ellipse cx="210" cy="156" rx="130" ry="5" fill="rgba(58,63,59,0.07)"/>

          {/* ── Left wing (2-storey) ── */}
          <rect x="28" y="97" width="74" height="58" fill="#EDE8E2" stroke="#9CAC9C" strokeWidth="1"/>
          {/* Left wing floor line */}
          <line x1="28" y1="126" x2="102" y2="126" stroke="#CFD6C4" strokeWidth="0.6"/>
          {/* Left wing windows - 2 cols × 2 rows */}
          {[38,62].flatMap(wx => [104,132].map(wy =>
            <rect key={`lw-${wx}-${wy}`} x={wx} y={wy} width="20" height="14"
              fill="rgba(153,205,216,0.28)" stroke="#99CDD8" strokeWidth="0.7" rx="1"/>
          ))}

          {/* ── Right wing (2-storey) ── */}
          <rect x="318" y="97" width="74" height="58" fill="#EDE8E2" stroke="#9CAC9C" strokeWidth="1"/>
          <line x1="318" y1="126" x2="392" y2="126" stroke="#CFD6C4" strokeWidth="0.6"/>
          {[328,352].flatMap(wx => [104,132].map(wy =>
            <rect key={`rw-${wx}-${wy}`} x={wx} y={wy} width="20" height="14"
              fill="rgba(153,205,216,0.28)" stroke="#99CDD8" strokeWidth="0.7" rx="1"/>
          ))}

          {/* ── Main building body ── */}
          <rect x="100" y="22" width="220" height="133" fill="#F2EEE9" stroke="#9CAC9C" strokeWidth="1.2"/>

          {/* Parapet / roofline cap */}
          <rect x="96" y="18" width="228" height="9" rx="1" fill="#CFD6C4" stroke="#9CAC9C" strokeWidth="0.8"/>

          {/* Floor division lines */}
          <line x1="100" y1="55"  x2="320" y2="55"  stroke="#CFD6C4" strokeWidth="0.7"/>
          <line x1="100" y1="88"  x2="320" y2="88"  stroke="#CFD6C4" strokeWidth="0.7"/>
          <line x1="100" y1="121" x2="320" y2="121" stroke="#CFD6C4" strokeWidth="0.7"/>

          {/* Subtle structural pilasters */}
          {[155, 210, 265].map(px =>
            <line key={px} x1={px} y1="22" x2={px} y2="155" stroke="#CFD6C4" strokeWidth="0.5" strokeDasharray="2,4"/>
          )}

          {/* ── Windows: 4 rows × 4 cols (skip centre-bottom for entrance) ── */}
          {/* Cols at x: 113, 148, 183, 218, 253, 288 — 4 usable: 113,163,247,297 → let's do 5 evenly */}
          {/* 5 cols: starting at 112, step 36 → 112,148,184,220,256,292 — that's 6, trim to 5: 113,149,185,221,257 */}
          {[113,149,185,221,257].flatMap((wx, ci) =>
            [28,61,94,127].map((wy, ri) => {
              // Skip centre col (ci===2) on bottom row (ri===3) — that's the entrance
              if (ci === 2 && ri === 3) return null;
              const lit = (ci + ri) % 3 !== 0;
              return (
                <g key={`w-${wx}-${wy}`}>
                  <rect x={wx} y={wy} width="26" height="19"
                    fill={lit ? "rgba(153,205,216,0.32)" : "rgba(197,214,204,0.25)"}
                    stroke="#99CDD8" strokeWidth="0.8" rx="1"/>
                  {/* Mullion */}
                  <line x1={wx+13} y1={wy} x2={wx+13} y2={wy+19} stroke="#99CDD8" strokeWidth="0.4" opacity="0.5"/>
                  {/* Sill */}
                  <line x1={wx} y1={wy+16} x2={wx+26} y2={wy+16} stroke="#99CDD8" strokeWidth="0.5" opacity="0.4"/>
                </g>
              );
            })
          )}

          {/* ── Entrance ── */}
          {/* Canopy */}
          <rect x="188" y="118" width="44" height="5" rx="1" fill="#306223" opacity="0.55"/>
          <line x1="185" y1="123" x2="235" y2="123" stroke="#306223" strokeWidth="0.6" opacity="0.4"/>
          {/* Door opening */}
          <rect x="198" y="127" width="24" height="28" fill="rgba(48,98,35,0.12)" stroke="#9CAC9C" strokeWidth="0.8" rx="1"/>
          {/* Door panels */}
          <line x1="210" y1="127" x2="210" y2="155" stroke="#9CAC9C" strokeWidth="0.5"/>
          <rect x="200" y="129" width="8" height="10" rx="0.5" fill="rgba(153,205,216,0.3)" stroke="#99CDD8" strokeWidth="0.4"/>
          <rect x="212" y="129" width="8" height="10" rx="0.5" fill="rgba(153,205,216,0.3)" stroke="#99CDD8" strokeWidth="0.4"/>
          {/* Steps */}
          <rect x="194" y="153" width="32" height="2"   fill="#C8C3BB"/>
          <rect x="190" y="153.5" width="40" height="1.5" fill="#B8B3AB" opacity="0.5"/>

          {/* ── Left trees (architectural elevation style) ── */}
          {/* Tree 1 */}
          <line x1="20" y1="120" x2="20" y2="155" stroke="#7B9A7E" strokeWidth="1.5"/>
          <ellipse cx="20" cy="105" rx="14" ry="20" fill="#9CB89E" opacity="0.55" stroke="#7B9A7E" strokeWidth="0.8"/>
          <ellipse cx="20" cy="100" rx="10" ry="14" fill="#8AAF8C" opacity="0.45"/>
          {/* Tree 2 */}
          <line x1="58" y1="128" x2="58" y2="155" stroke="#7B9A7E" strokeWidth="1.2"/>
          <ellipse cx="58" cy="115" rx="11" ry="16" fill="#9CB89E" opacity="0.50" stroke="#7B9A7E" strokeWidth="0.7"/>

          {/* ── Right trees ── */}
          <line x1="400" y1="120" x2="400" y2="155" stroke="#7B9A7E" strokeWidth="1.5"/>
          <ellipse cx="400" cy="105" rx="14" ry="20" fill="#9CB89E" opacity="0.55" stroke="#7B9A7E" strokeWidth="0.8"/>
          <ellipse cx="400" cy="100" rx="10" ry="14" fill="#8AAF8C" opacity="0.45"/>
          <line x1="362" y1="128" x2="362" y2="155" stroke="#7B9A7E" strokeWidth="1.2"/>
          <ellipse cx="362" cy="115" rx="11" ry="16" fill="#9CB89E" opacity="0.50" stroke="#7B9A7E" strokeWidth="0.7"/>

          {/* Foreground ground line accent */}
          <line x1="0" y1="158" x2="420" y2="158" stroke="#B8B3AB" strokeWidth="0.4" opacity="0.5"/>
        </svg>
      </div>}
    </div>
  );
}

// ─── Welcome hero (empty state) ───────────────────────────────────────────────

function WelcomeHero({ isMobile }: { isMobile: boolean }) {
  return (
    <div style={{
      display: "flex", flexDirection: isMobile ? "column" : "row", borderRadius: 16, overflow: "hidden",
      border: "1px solid #CFD6C4", marginBottom: 28,
      boxShadow: "0 2px 12px rgba(48,98,35,0.07)",
    }}>
      <div style={{
        flex: isMobile ? "none" : "0 0 58%", padding: isMobile ? "20px 18px" : "32px 36px",
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
          Navigate Karnataka land records — district to village to parcel — with survey number search and boundary overlays.
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
      {!isMobile && <div style={{ flex: 1, position: "relative", overflow: "hidden", background: "#EAE5DF" }}>
        <CardMapSVG variant={0} />
      </div>}
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

  const { isMobile } = useIsMobile();
  const initials = user ? getInitials({ email: user.email, name: user.name }) : "U";
  const isEmpty  = !loading && projects.length === 0;

  return (
    <div style={{ minHeight: "100vh", background: "#F2EDE8", fontFamily: "var(--font-inter)" }}>

      {/* Top nav */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: isMobile ? "0 16px" : "0 32px", height: 56,
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
          {user?.name && !isMobile && (
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

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: isMobile ? "20px 14px 40px" : "36px 24px 60px" }}>

        {/* Building hero */}
        <BuildingHero isMobile={isMobile} />

        {/* Welcome hero — only on first visit (empty state) */}
        {isEmpty && <WelcomeHero isMobile={isMobile} />}

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
            <h1 style={{ fontSize: isMobile ? 22 : 28, fontWeight: 800, color: "#3A3F3B", letterSpacing: "-0.6px", lineHeight: 1, margin: 0 }}>
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
          {!isMobile && <svg width="80" height="50" viewBox="0 0 80 50" fill="none" aria-hidden style={{ opacity: 0.50, flexShrink: 0 }}>
            <ellipse cx="40" cy="25" rx="39" ry="24" stroke="#CFD6C4" strokeWidth="0.8"/>
            <ellipse cx="40" cy="25" rx="28" ry="17" stroke="#CFD6C4" strokeWidth="0.8"/>
            <ellipse cx="40" cy="25" rx="17" ry="10" stroke="#CFD6C4" strokeWidth="0.8"/>
            <circle  cx="40" cy="25" r="4"            fill="#99CDD8"  opacity="0.7"/>
            <circle  cx="40" cy="25" r="1.5"          fill="#FDFCFB"/>
          </svg>}
        </div>

        {/* Project grid */}
        {loading ? (
          <div style={{ color: "#7B8F83", fontSize: 14 }}>Loading projects…</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: `repeat(auto-fill, minmax(${isMobile ? 260 : 290}px, 1fr))`, gap: 16 }}>
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
