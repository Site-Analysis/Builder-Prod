// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import NextAuth from "next-auth"
import Keycloak from "next-auth/providers/keycloak"

async function refreshKeycloakToken(refreshToken: string): Promise<{ accessToken: string; refreshToken: string; expiresAt: number } | null> {
  const url = `${process.env.KEYCLOAK_URL}/realms/${process.env.KEYCLOAK_REALM}/protocol/openid-connect/token`
  const params = new URLSearchParams({
    grant_type: "refresh_token",
    client_id: process.env.KEYCLOAK_CLIENT_ID!,
    refresh_token: refreshToken,
  })
  if (process.env.KEYCLOAK_CLIENT_SECRET) params.set("client_secret", process.env.KEYCLOAK_CLIENT_SECRET)
  try {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: params })
    if (!res.ok) return null
    const data = await res.json()
    return {
      accessToken: data.access_token,
      refreshToken: data.refresh_token ?? refreshToken,
      expiresAt: Math.floor(Date.now() / 1000) + (data.expires_in ?? 3600),
    }
  } catch {
    return null
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  logger: {
    error: (code, ...message) => { console.error("[auth][error]", code, ...message) },
    warn:  (code)             => { console.warn("[auth][warn]",  code) },
    debug: (code, ...message) => { console.log("[auth][debug]",  code, ...message) },
  },
  providers: [
    Keycloak({
      clientId: process.env.KEYCLOAK_CLIENT_ID!,
      issuer: `${process.env.KEYCLOAK_URL}/realms/${process.env.KEYCLOAK_REALM}`,
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        token.accessToken = account.access_token
        token.refreshToken = account.refresh_token
        token.expiresAt = account.expires_at ?? Math.floor(Date.now() / 1000) + 3600
      }
      if (typeof token.expiresAt === "number" && Date.now() / 1000 > token.expiresAt - 60) {
        if (token.refreshToken) {
          const refreshed = await refreshKeycloakToken(token.refreshToken as string)
          if (refreshed) {
            token.accessToken = refreshed.accessToken
            token.refreshToken = refreshed.refreshToken
            token.expiresAt = refreshed.expiresAt
          }
        }
      }
      return token
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined
      session.user.id = token.sub ?? ""
      return session
    },
  },
})

declare module "next-auth" {
  interface Session {
    accessToken?: string
    user: {
      id: string
      name?: string | null
      email?: string | null
      image?: string | null
    }
  }
}
