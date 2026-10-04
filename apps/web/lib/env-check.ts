// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Server start-up check of the auth configuration (Node.js runtime only, imported from
// instrumentation.ts). Runs when the server starts, never during `next build`, so an image
// can be built without secrets. A missing variable stops the server with one error that
// names every missing variable, instead of failing later (e.g. client_id=undefined).

const REQUIRED = [
  "KEYCLOAK_URL",
  "KEYCLOAK_REALM",
  "KEYCLOAK_CLIENT_ID",
  "KEYCLOAK_CLIENT_SECRET",
  "AUTH_SECRET",
  "AUTH_URL",
] as const

export function checkEnv(): void {
  const missing = REQUIRED.filter((k) => !process.env[k]?.trim())
  if (missing.length) {
    const msg =
      `[config] Missing required environment variable(s): ${missing.join(", ")}. ` +
      "Set them on the container (see .env.builders.example) and restart."
    console.error(msg)
    // fail closed: do not serve with a broken auth configuration
    process.exit(1)
  }
}
