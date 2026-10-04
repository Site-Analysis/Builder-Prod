// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Server start-up check of the auth configuration (Node.js runtime only, imported from
// instrumentation.ts). Runs when the server starts, never during `next build`, so an image
// can be built without secrets. A bad configuration stops the server with one error that
// names every problem, instead of failing later (e.g. client_id=undefined).

const REQUIRED = [
  "KEYCLOAK_URL",
  "KEYCLOAK_REALM",
  "KEYCLOAK_CLIENT_ID",
  "KEYCLOAK_CLIENT_SECRET",
  "AUTH_SECRET",
  "AUTH_URL",
] as const

const truthy = (v: string | undefined) => ["1", "true", "yes"].includes((v ?? "").trim().toLowerCase())

export function checkEnv(): void {
  const problems: string[] = []
  const missing = REQUIRED.filter((k) => !process.env[k]?.trim())
  if (missing.length) {
    problems.push(`Missing required environment variable(s): ${missing.join(", ")}`)
  }
  // fail closed: the auth bypass is allowed only on a developer machine (APP_ENV=local).
  // NEXT_PUBLIC_DEV_BYPASS_AUTH is inlined at build time, so this also catches an image
  // that was built with the browser-side bypass on.
  const bypass = truthy(process.env.DEV_BYPASS_AUTH) || truthy(process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH)
  const appEnv = (process.env.APP_ENV ?? "").trim().toLowerCase()
  if (bypass && appEnv !== "local") {
    problems.push(
      `DEV_BYPASS_AUTH / NEXT_PUBLIC_DEV_BYPASS_AUTH is set but APP_ENV is ${appEnv ? `"${appEnv}"` : "unset"}; ` +
        "the bypass is allowed only with APP_ENV=local"
    )
  }
  if (problems.length) {
    console.error(
      `[config] Refusing to start. ${problems.join(". ")}. ` +
        "Fix the container environment (see .env.builders.example) and restart."
    )
    process.exit(1)
  }
}
