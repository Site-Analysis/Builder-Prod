// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

import type { Project, ProjectStats } from "../stores/project";

interface ProjectRow {
  id: string;
  user_id: string;
  name: string;
  location: string | null;
  status: string;
  boundary: GeoJSON.Geometry | null;
  created_at: string;
}

function rowToProject(row: ProjectRow): Project {
  return {
    id: row.id,
    name: row.name,
    location: row.location ?? "",
    status: (row.status as Project["status"]) ?? "needs-review",
    boundary: row.boundary ?? undefined,
    created_at: row.created_at,
  };
}

async function apiCall<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    credentials: "include",
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function getProjects(): Promise<{ projects: Project[]; stats: ProjectStats }> {
  const { projects: rows, stats } = await apiCall<{ projects: ProjectRow[]; stats: ProjectStats }>("/api/projects");
  return { projects: rows.map(rowToProject), stats };
}

export async function getProject(id: string): Promise<Project> {
  const row = await apiCall<ProjectRow>(`/api/projects/${id}`);
  return rowToProject(row);
}
