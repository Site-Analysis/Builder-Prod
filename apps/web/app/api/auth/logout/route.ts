// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Federated sign-out: clear the app session, then send the browser to Keycloak's
// end-session endpoint so the Keycloak session ends too (Auth.js signOut() alone only
// clears the app cookie).
//
// POST only: a GET logout can be triggered by any link or <img> on another site. The
// session cookie is SameSite=Lax, so a cross-site POST arrives without it, and a POST
// whose Origin is not this app is refused.
//
// The id_token lives only in the encrypted JWT cookie (never in the session object sent
// to the browser); it is read here on the server and sent as id_token_hint, which is how
// Keycloak (18+) identifies the client, user and session.

import { NextResponse } from "next/server"
import { getToken } from "next-auth/jwt"
import { signOut } from "@/auth"

function appUrl(): string {
  return (process.env.AUTH_URL || process.env.NEXTAUTH_URL || "http://localhost:3000").replace(/\/+$/, "")
}

export async function POST(req: Request) {
  const base = appUrl()
  const origin = req.headers.get("origin")
  if (origin && origin !== new URL(base).origin) {
    return NextResponse.json({ error: "Cross-site logout refused" }, { status: 403 })
  }

  // read the id_token before signOut() deletes the cookie
  const jwt = await getToken({
    req,
    secret: process.env.AUTH_SECRET || process.env.NEXTAUTH_SECRET,
    secureCookie: base.startsWith("https://"),
  })
  const idToken = typeof jwt?.idToken === "string" ? jwt.idToken : undefined

  await signOut({ redirect: false })

  // public Keycloak URL (the browser goes there); it includes any /auth prefix
  const kcUrl = (process.env.KEYCLOAK_URL ?? "").replace(/\/+$/, "")
  const endSession = new URL(
    `${kcUrl}/realms/${encodeURIComponent(process.env.KEYCLOAK_REALM ?? "")}/protocol/openid-connect/logout`
  )
  endSession.searchParams.set("client_id", process.env.KEYCLOAK_CLIENT_ID ?? "")
  // must be registered on the client: Valid post logout redirect URIs
  endSession.searchParams.set("post_logout_redirect_uri", base)
  if (idToken) endSession.searchParams.set("id_token_hint", idToken)

  // 303: the browser follows with a GET (Keycloak's confirm page if there is no hint)
  return NextResponse.redirect(endSession.toString(), 303)
}

export async function GET() {
  return NextResponse.json({ error: "Use POST to sign out" }, { status: 405, headers: { Allow: "POST" } })
}
