// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import { auth } from "@/auth"
import { getSupabaseAdmin } from "@/lib/supabase/server"

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await auth()
  if (!session?.user?.id) {
    return Response.json({ error: "Unauthorized" }, { status: 401 })
  }
  const { id } = await params
  const { data, error } = await getSupabaseAdmin()
    .from("builder_projects")
    .select("*")
    .eq("id", id)
    .eq("user_id", session.user.id)
    .single()
  if (error) return Response.json({ error: error.message }, { status: 404 })
  return Response.json(data)
}
