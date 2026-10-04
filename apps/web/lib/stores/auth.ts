// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { create } from "zustand";

interface AuthUser {
  id: string;
  email?: string;
  name?: string;
}

// set when a data call returns 401: "forbidden" = signed in but the backend refused the
// token; "signin-loop" = not signed in and sign-in was already tried moments ago
export type AccessDenied = "forbidden" | "signin-loop" | null;

interface AuthState {
  user: AuthUser | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  accessDenied: AccessDenied;
  setAuth: (user: AuthUser, accessToken: string) => void;
  clearAuth: () => void;
  setAccessDenied: (reason: AccessDenied) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  isAuthenticated: false,
  accessDenied: null,
  setAuth: (user, accessToken) => set({ user, accessToken, isAuthenticated: true }),
  clearAuth: () => set({ user: null, accessToken: null, isAuthenticated: false }),
  setAccessDenied: (accessDenied) => set({ accessDenied }),
}));
