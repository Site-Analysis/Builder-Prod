// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Feature flags read from NEXT_PUBLIC_* env vars. Next.js inlines NEXT_PUBLIC_ values only
// for literal `process.env.NAME` access, so each flag is read here by name once.

const FLAG_VALUES = {
  cadastralExplorer: process.env.NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER,
  planningLayers: process.env.NEXT_PUBLIC_ENABLE_PLANNING_LAYERS,
} as const;

export type Flag = keyof typeof FLAG_VALUES;

export function isEnabled(flag: Flag): boolean {
  const v = FLAG_VALUES[flag];
  return v === "1" || v === "true";
}
