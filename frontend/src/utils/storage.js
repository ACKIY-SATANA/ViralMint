// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Browser-storage keys: one namespace, one convention.
 *
 * `vm.<scope>.<name>` — scope is the surface, name is the setting. Keeping
 * every key under one prefix is what makes "clear ViralMint's local state" a
 * thing a user (or a support answer) can actually do; an unprefixed key is
 * invisible to any such sweep and gets left behind.
 *
 * Existing unprefixed keys are deliberately NOT renamed — a rename silently
 * discards whatever the user had saved. This is what a NEW key looks like.
 */
export function storageKey(scope, name) {
  if (!scope || !name) throw new Error("storageKey(scope, name): both required")
  return `vm.${scope}.${name}`
}
