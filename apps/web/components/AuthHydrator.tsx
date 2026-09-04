// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { useEffect } from "react";
import { useSession } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

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
  return <>{children}</>;
}
