// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

export default function Home() {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, router]);

  if (isAuthenticated) return null;

  return (
    <div className="flex items-center justify-center h-full bg-neutral-bg">
      <div style={{
        background: "rgba(253,252,251,0.85)",
        backdropFilter: "blur(16px)",
        border: "1px solid #CFD6C4",
        borderRadius: 16,
        padding: "48px 56px",
        textAlign: "center",
        boxShadow: "0 8px 32px rgba(58,63,59,0.12)",
      }}>
        <div style={{ fontFamily: "var(--font-space-grotesk)", fontWeight: 800, fontSize: 26, color: "#306223", marginBottom: 8 }}>
          Qnit Builders
        </div>
        <div style={{ color: "#7B8F83", fontSize: 14, marginBottom: 32 }}>
          Karnataka cadastral explorer &amp; site feasibility
        </div>
        <button
          onClick={() => signIn("keycloak")}
          style={{
            background: "#306223", color: "#FDFCFB",
            border: "none", borderRadius: 8,
            padding: "12px 32px", fontWeight: 700,
            fontSize: 14, cursor: "pointer",
            letterSpacing: "0.02em",
          }}
        >
          Sign in
        </button>
      </div>
    </div>
  );
}
