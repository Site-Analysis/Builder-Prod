// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

function BuildingSVG() {
  return (
    <svg viewBox="0 0 420 300" xmlns="http://www.w3.org/2000/svg"
      preserveAspectRatio="xMidYMid slice"
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#E8E3DC"/>
          <stop offset="100%" stopColor="#D6D0C8"/>
        </linearGradient>
      </defs>
      <rect width="420" height="300" fill="url(#sky)"/>

      {[40,80,120,160,200,240,280,320,360,400].flatMap(x =>
        [30,70,110,150,190,230,270].map(y =>
          <circle key={`${x}-${y}`} cx={x} cy={y} r="1" fill="#C8C3BB" opacity="0.4"/>
        )
      )}

      <rect x="0" y="260" width="420" height="40" fill="#C8C3BB" opacity="0.35"/>
      <line x1="0" y1="260" x2="420" y2="260" stroke="#A8A39C" strokeWidth="0.8"/>
      <ellipse cx="210" cy="262" rx="150" ry="6" fill="rgba(58,63,59,0.07)"/>

      {/* Left wing */}
      <rect x="20" y="170" width="80" height="90" fill="#EDE8E2" stroke="#9CAC9C" strokeWidth="1"/>
      <line x1="20" y1="215" x2="100" y2="215" stroke="#CFD6C4" strokeWidth="0.6"/>
      {[32,58].flatMap(wx => [178,222].map(wy =>
        <rect key={`lw-${wx}-${wy}`} x={wx} y={wy} width="22" height="16"
          fill="rgba(153,205,216,0.28)" stroke="#99CDD8" strokeWidth="0.7" rx="1"/>
      ))}

      {/* Right wing */}
      <rect x="320" y="170" width="80" height="90" fill="#EDE8E2" stroke="#9CAC9C" strokeWidth="1"/>
      <line x1="320" y1="215" x2="400" y2="215" stroke="#CFD6C4" strokeWidth="0.6"/>
      {[330,356].flatMap(wx => [178,222].map(wy =>
        <rect key={`rw-${wx}-${wy}`} x={wx} y={wy} width="22" height="16"
          fill="rgba(153,205,216,0.28)" stroke="#99CDD8" strokeWidth="0.7" rx="1"/>
      ))}

      {/* Main building */}
      <rect x="98" y="50" width="224" height="210" fill="#F2EEE9" stroke="#9CAC9C" strokeWidth="1.2"/>
      <rect x="94" y="44" width="232" height="11" rx="1" fill="#CFD6C4" stroke="#9CAC9C" strokeWidth="0.8"/>
      <line x1="98" y1="100" x2="322" y2="100" stroke="#CFD6C4" strokeWidth="0.7"/>
      <line x1="98" y1="150" x2="322" y2="150" stroke="#CFD6C4" strokeWidth="0.7"/>
      <line x1="98" y1="200" x2="322" y2="200" stroke="#CFD6C4" strokeWidth="0.7"/>
      {[160,210,260].map(px =>
        <line key={px} x1={px} y1="50" x2={px} y2="260" stroke="#CFD6C4" strokeWidth="0.5" strokeDasharray="2,4"/>
      )}

      {/* Windows 5×3 */}
      {[112,148,184,220,256].flatMap((wx, ci) =>
        [58,108,158].map((wy, ri) => {
          if (ci === 2 && ri === 2) return null;
          const lit = (ci + ri) % 3 !== 0;
          return (
            <g key={`w-${wx}-${wy}`}>
              <rect x={wx} y={wy} width="26" height="20"
                fill={lit ? "rgba(153,205,216,0.32)" : "rgba(197,214,204,0.25)"}
                stroke="#99CDD8" strokeWidth="0.8" rx="1"/>
              <line x1={wx+13} y1={wy} x2={wx+13} y2={wy+20} stroke="#99CDD8" strokeWidth="0.4" opacity="0.5"/>
            </g>
          );
        })
      )}

      {/* Entrance */}
      <rect x="188" y="196" width="44" height="6" rx="1" fill="#306223" opacity="0.55"/>
      <rect x="198" y="206" width="24" height="54" fill="rgba(48,98,35,0.12)" stroke="#9CAC9C" strokeWidth="0.8" rx="1"/>
      <line x1="210" y1="206" x2="210" y2="260" stroke="#9CAC9C" strokeWidth="0.5"/>
      <rect x="200" y="208" width="8" height="12" rx="0.5" fill="rgba(153,205,216,0.3)" stroke="#99CDD8" strokeWidth="0.4"/>
      <rect x="212" y="208" width="8" height="12" rx="0.5" fill="rgba(153,205,216,0.3)" stroke="#99CDD8" strokeWidth="0.4"/>
      <rect x="192" y="258" width="36" height="3" fill="#C8C3BB"/>

      {/* Left trees */}
      <line x1="14" y1="215" x2="14" y2="260" stroke="#7B9A7E" strokeWidth="1.5"/>
      <ellipse cx="14" cy="196" rx="15" ry="22" fill="#9CB89E" opacity="0.55" stroke="#7B9A7E" strokeWidth="0.8"/>
      <ellipse cx="14" cy="190" rx="10" ry="15" fill="#8AAF8C" opacity="0.45"/>
      <line x1="55" y1="225" x2="55" y2="260" stroke="#7B9A7E" strokeWidth="1.2"/>
      <ellipse cx="55" cy="210" rx="12" ry="18" fill="#9CB89E" opacity="0.50" stroke="#7B9A7E" strokeWidth="0.7"/>

      {/* Right trees */}
      <line x1="406" y1="215" x2="406" y2="260" stroke="#7B9A7E" strokeWidth="1.5"/>
      <ellipse cx="406" cy="196" rx="15" ry="22" fill="#9CB89E" opacity="0.55" stroke="#7B9A7E" strokeWidth="0.8"/>
      <ellipse cx="406" cy="190" rx="10" ry="15" fill="#8AAF8C" opacity="0.45"/>
      <line x1="365" y1="225" x2="365" y2="260" stroke="#7B9A7E" strokeWidth="1.2"/>
      <ellipse cx="365" cy="210" rx="12" ry="18" fill="#9CB89E" opacity="0.50" stroke="#7B9A7E" strokeWidth="0.7"/>
    </svg>
  );
}

export default function Home() {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    if (isAuthenticated) router.replace("/dashboard");
  }, [isAuthenticated, router]);

  if (isAuthenticated) return null;

  return (
    <div style={{ display: "flex", height: "100vh", fontFamily: "var(--font-inter)" }}>

      {/* Left — sign-in panel */}
      <div style={{
        flex: "0 0 420px", display: "flex", flexDirection: "column",
        justifyContent: "center", padding: "56px 52px",
        background: "#FDFCFB",
        borderRight: "1px solid #CFD6C4",
      }}>

        {/* Logo */}
        <div style={{ display: "inline-flex", alignItems: "center", gap: 7, marginBottom: 40 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 9, background: "#306223",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <rect x="3" y="7" width="12" height="9" fill="rgba(255,255,255,0.9)" rx="1"/>
              <rect x="5" y="2" width="8" height="7" fill="rgba(255,255,255,0.7)" rx="1"/>
              <rect x="7" y="11" width="4" height="5" fill="rgba(48,98,35,0.5)"/>
              <rect x="5" y="9" width="3" height="2" fill="rgba(153,205,216,0.8)"/>
              <rect x="10" y="9" width="3" height="2" fill="rgba(153,205,216,0.8)"/>
            </svg>
          </div>
          <span style={{
            fontFamily: "var(--font-space-grotesk, inherit)",
            fontWeight: 800, fontSize: 20, color: "#306223", letterSpacing: "-0.3px",
          }}>
            Qnit Builders
          </span>
        </div>

        {/* Heading */}
        <div style={{
          fontFamily: "var(--font-space-grotesk, inherit)",
          fontSize: 28, fontWeight: 800, color: "#3A3F3B",
          lineHeight: 1.18, letterSpacing: "-0.6px", marginBottom: 12,
        }}>
          Karnataka&apos;s land<br/>records, mapped.
        </div>
        <div style={{ fontSize: 14, color: "#7B8F83", lineHeight: 1.65, marginBottom: 32, maxWidth: 300 }}>
          Navigate cadastral records district to village, search survey numbers, and overlay parcel boundaries on live maps.
        </div>

        {/* Feature pills */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 40 }}>
          {[
            { label: "District → Village", color: "#5B93C9" },
            { label: "Survey search",      color: "#306223"  },
            { label: "Parcel boundaries",  color: "#8B6FCB"  },
          ].map(({ label, color }) => (
            <span key={label} style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "5px 11px", borderRadius: 9999,
              fontSize: 11, fontWeight: 500,
              background: `${color}12`, color,
              border: `1px solid ${color}26`,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }}/>
              {label}
            </span>
          ))}
        </div>

        {/* Sign-in button */}
        <button
          onClick={() => signIn("keycloak")}
          style={{
            background: "#306223", color: "#FDFCFB",
            border: "none", borderRadius: 10,
            padding: "14px 0", fontWeight: 700,
            fontSize: 15, cursor: "pointer",
            letterSpacing: "0.02em", width: "100%",
            boxShadow: "0 4px 16px rgba(48,98,35,0.25)",
            transition: "opacity 0.15s",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.88"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
        >
          Sign in with SSO →
        </button>

        <div style={{ marginTop: 20, fontSize: 11, color: "#B8C4BB", textAlign: "center" }}>
          Secured by Keycloak · Karnataka Cadastral Explorer
        </div>
      </div>

      {/* Right — building illustration */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <BuildingSVG />
        <div style={{
          position: "absolute", inset: 0, pointerEvents: "none",
          background: "linear-gradient(135deg, rgba(240,237,232,0.12) 0%, rgba(200,195,187,0.08) 100%)",
        }}/>
        <div style={{
          position: "absolute", bottom: 28, right: 32,
          fontSize: 10, color: "#9CAC9C", letterSpacing: "0.4px", fontWeight: 500,
        }}>
          Karnataka Cadastral Explorer
        </div>
      </div>
    </div>
  );
}
