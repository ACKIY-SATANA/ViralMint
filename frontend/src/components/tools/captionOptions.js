// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Shared caption + emoji enums for the tool pages and Clip Studio.
 *
 * The VALUES are the contract: they must match the keys of
 * backend/services/caption_service.py CAPTION_STYLES, minus `brainrot` (a look
 * applied by its own pipeline, not a general preset).
 * tests/test_caption_styles_parity.py fails if the two drift.
 *
 * They drifted once already: this list and the /api/tools/captions Literal
 * were both pinned to the original three, so the Captions tool offered 3 of
 * 10 styles — neon, karaoke, glow, urban, warm and mono were unreachable from
 * the tool built to apply them.
 *
 * Descriptions summarize the real render params (highlight colour, font,
 * words_per_group) rather than being decorative.
 */
export const CAPTION_STYLES = [
  { value: "viral", label: "Viral", description: "Yellow highlight, ~6-word phrases" },
  { value: "classic", label: "Classic", description: "Full sentence, no highlight" },
  { value: "bold", label: "Bold", description: "Green highlight, ~4-word phrases" },
  { value: "neon", label: "Neon", description: "Pink text, yellow highlight, ~6 words" },
  { value: "minimal", label: "Minimal", description: "Plain Arial, long lines, no highlight" },
  { value: "karaoke", label: "Karaoke", description: "Grey text lights up word by word" },
  { value: "glow", label: "Glow", description: "Soft orange glow, ~6-word phrases" },
  { value: "urban", label: "Bold Urban", description: "Arial Black, punchy ~3-word bursts" },
  { value: "warm", label: "Warm Glow", description: "Georgia serif, warm cream tone" },
  { value: "mono", label: "Monochrome", description: "Verdana, grey highlight, understated" },
]

export const EMOJI_LEVELS = [
  { value: "none", label: "None" },
  { value: "minimal", label: "Minimal" },
  { value: "moderate", label: "Moderate" },
  { value: "heavy", label: "Heavy" },
]
