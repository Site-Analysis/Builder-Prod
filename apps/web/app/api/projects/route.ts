// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import { auth } from "@/auth"
import { getSupabaseAdmin } from "@/lib/supabase/server"

interface ProjectRow {
  id: string
  user_id: string
  name: string
  location: string | null
  status: string
  boundary: object | null
  coordinates: string | null
  area_sqm: number | null
  created_at: string
}

function computeStats(projects: ProjectRow[]) {
  const monthStart = new Date()
  monthStart.setDate(1)
  monthStart.setHours(0, 0, 0, 0)
  return {
    total: projects.length,
    fully_analysed: projects.filter((p) => p.status === "complete").length,
    needs_review: projects.filter((p) => p.status === "needs-review").length,
    this_month: projects.filter((p) => new Date(p.created_at) >= monthStart).length,
  }
}

const DEV_USER_ID = "dev"

export async function GET() {
  let userId: string
  if (process.env.DEV_BYPASS_AUTH === "1") {
    userId = DEV_USER_ID
  } else {
    const session = await auth()
    if (!session?.user?.id) return Response.json({ error: "Unauthorized" }, { status: 401 })
    userId = session.user.id
  }
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return Response.json({ error: "Supabase not configured — set SUPABASE_SERVICE_ROLE_KEY" }, { status: 503 })
  }
  const { data, error } = await getSupabaseAdmin()
    .from("builder_projects")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
  if (error) return Response.json({ error: error.message }, { status: 500 })
  const projects = (data ?? []) as ProjectRow[]
  return Response.json({ projects, stats: computeStats(projects) })
}

export async function POST(req: Request) {
  let userId: string
  if (process.env.DEV_BYPASS_AUTH === "1") {
    userId = DEV_USER_ID
  } else {
    const session = await auth()
    if (!session?.user?.id) return Response.json({ error: "Unauthorized" }, { status: 401 })
    userId = session.user.id
  }
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return Response.json({ error: "Supabase not configured — set SUPABASE_SERVICE_ROLE_KEY" }, { status: 503 })
  }
  const body = await req.json()
  const { data, error } = await getSupabaseAdmin()
    .from("builder_projects")
    .insert({
      user_id: userId,
      name: body.name,
      location: body.location,
      status: "needs-review",
      boundary: body.boundary ?? null,
    })
    .select()
    .single()
  if (error) return Response.json({ error: error.message }, { status: 500 })
  return Response.json(data)
}
