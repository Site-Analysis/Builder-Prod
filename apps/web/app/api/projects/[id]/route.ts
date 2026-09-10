// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import { auth } from "@/auth"
import { getSupabaseAdmin } from "@/lib/supabase/server"

const DEV_USER_ID = "f8b07c58-0a16-4b5f-8c3e-16016c2a16f6"

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  let userId: string
  if (process.env.DEV_BYPASS_AUTH === "1") {
    userId = DEV_USER_ID
  } else {
    const session = await auth()
    if (!session?.user?.id) return Response.json({ error: "Unauthorized" }, { status: 401 })
    userId = session.user.id
  }
  const { id } = await params
  const { data, error } = await getSupabaseAdmin()
    .from("builder_projects")
    .select("*")
    .eq("id", id)
    .eq("user_id", userId)
    .single()
  if (error) return Response.json({ error: error.message }, { status: 404 })
  return Response.json(data)
}
