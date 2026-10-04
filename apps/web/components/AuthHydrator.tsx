// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect } from "react";
import { useSession } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";
import { signOutEverywhere } from "@/lib/logout";

// shown when a data call returned 401 (lib/auth-recovery): signed in but refused, or
// sign-in was just tried and did not complete. Never signs out on its own.
function AccessDeniedBanner() {
  const reason = useAuthStore((s) => s.accessDenied);
  const setAccessDenied = useAuthStore((s) => s.setAccessDenied);
  if (!reason) return null;
  const msg =
    reason === "forbidden"
      ? "Access denied: you are signed in, but the data service did not accept your session."
      : "Sign-in did not complete. Please try again in a minute.";
  const btn: React.CSSProperties = {
    background: "none", border: "1px solid #F2C9C3", borderRadius: 6,
    padding: "4px 10px", fontSize: 12, color: "#8A2A1F", cursor: "pointer",
  };
  return (
    <div role="alert" style={{
      position: "fixed", top: 12, left: "50%", transform: "translateX(-50%)", zIndex: 1000,
      display: "flex", gap: 10, alignItems: "center", maxWidth: "calc(100vw - 32px)",
      background: "#FDECEA", border: "1px solid #F2C9C3", borderRadius: 8,
      padding: "8px 12px", fontSize: 13, color: "#8A2A1F",
    }}>
      <span>{msg}</span>
      <button style={btn} onClick={() => { setAccessDenied(null); window.location.reload(); }}>Retry</button>
      <button style={btn} onClick={signOutEverywhere}>Sign out</button>
    </div>
  );
}

export function AuthHydrator({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();
  const setAuth   = useAuthStore((s) => s.setAuth);
  const clearAuth = useAuthStore((s) => s.clearAuth);

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1") {
      setAuth({ id: "dev", email: "dev@local", name: "Dev User" }, "dev-token");
      return;
    }
    if (status === "loading") return;
    if (status === "authenticated" && session?.user) {
      setAuth(
        { id: session.user.id ?? session.user.email ?? "", email: session.user.email ?? undefined, name: session.user.name ?? undefined },
        session.accessToken ?? ""
      );
    } else {
      clearAuth();
    }
  }, [session, status, setAuth, clearAuth]);

  if (process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === "1") return <>{children}</>;
  if (status === "loading") return null;
  return <><AccessDeniedBanner />{children}</>;
}
