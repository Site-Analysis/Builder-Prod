// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Sign out of the app and of Keycloak: a top-level POST to /api/auth/logout (POST, so a
// link or image on another site cannot log the user out), which answers with a 303 to
// Keycloak's end-session endpoint.

"use client";

export function signOutEverywhere(): void {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = "/api/auth/logout";
  document.body.appendChild(form);
  form.submit();
}
