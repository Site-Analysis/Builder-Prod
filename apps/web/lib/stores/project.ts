// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary

"use client";

import { create } from "zustand";

export interface Project {
  id: string;
  name: string;
  location: string;
  status: "complete" | "needs-review";
  created_at: string;
  boundary?: GeoJSON.Geometry;
}

export interface ProjectStats {
  total: number;
  fully_analysed: number;
  needs_review: number;
  this_month: number;
}

interface ProjectState {
  projects: Project[];
  stats: ProjectStats | null;
  currentProject: Project | null;
  setProjects: (projects: Project[], stats: ProjectStats) => void;
  setCurrentProject: (project: Project) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  projects: [],
  stats: null,
  currentProject: null,
  setProjects: (projects, stats) => set({ projects, stats }),
  setCurrentProject: (project) => set({ currentProject: project }),
}));
