// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// What a data call does on HTTP 401. Never signs out (a data error must not end the
// session or loop through Keycloak logout):
//   - no Auth.js session  -> start sign-in once (sessionStorage time guard: at most one
//     redirect per SIGNIN_GUARD_MS, so a misconfigured backend can never loop)
//   - session exists      -> the backend refused this user's token: show "access denied"

"use client";

import { getSession, signIn } from "next-auth/react";
import { useAuthStore } from "@/lib/stores/auth";

const GUARD_KEY = "qnit.signin-on-401.at";
const SIGNIN_GUARD_MS = 60_000;

export class UnauthorizedError extends Error {}

function guardAllows(): boolean {
  try {
    const last = Number(sessionStorage.getItem(GUARD_KEY) ?? 0);
    if (Date.now() - last < SIGNIN_GUARD_MS) return false;
    sessionStorage.setItem(GUARD_KEY, String(Date.now()));
    return true;
  } catch {
    return false; // no storage: never auto-redirect (fail safe, no loop)
  }
}

export async function handleUnauthorized(): Promise<never> {
  const session = await getSession();
  if (!session) {
    if (guardAllows()) {
      void signIn("keycloak", { callbackUrl: window.location.href });
      throw new UnauthorizedError("Signing in…");
    }
    useAuthStore.getState().setAccessDenied("signin-loop");
    throw new UnauthorizedError("Sign-in did not complete");
  }
  useAuthStore.getState().setAccessDenied("forbidden");
  throw new UnauthorizedError("Access denied");
}
