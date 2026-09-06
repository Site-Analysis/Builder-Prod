// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import { auth, signOut } from "@/auth";
import { redirect } from "next/navigation";

export async function GET() {
  const session = await auth();
  const idToken = session?.idToken;

  await signOut({ redirect: false });

  const kcUrl = process.env.KEYCLOAK_URL!;
  const kcRealm = process.env.KEYCLOAK_REALM!;
  const postLogoutUri = process.env.NEXTAUTH_URL ?? "http://localhost:3000";

  const logoutUrl = new URL(
    `${kcUrl}/realms/${kcRealm}/protocol/openid-connect/logout`
  );
  logoutUrl.searchParams.set("post_logout_redirect_uri", postLogoutUri);
  logoutUrl.searchParams.set("client_id", process.env.KEYCLOAK_CLIENT_ID!);
  if (idToken) logoutUrl.searchParams.set("id_token_hint", idToken);

  redirect(logoutUrl.toString());
}
