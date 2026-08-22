// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Shared Clip Studio formatting helpers.
 *
 * Extracted from ClipStudio.jsx so the cutting-bench components don't each
 * carry a copy — and so the page and the bench agree on what a timecode looks
 * like, which matters when the same number appears on a range block, in the
 * ranges rail and in the clip inspector.
 */
import { formatClock } from "../../utils/format"

/**
 * Clip Studio timecode — "1:30:00" / "2:51", or "--:--" when unknown.
 *
 * A thin alias over the app-wide `formatClock`: the "--:--" placeholder is
 * specific to this surface, and four call sites read better as
 * `formatTime(clip.clip_start_seconds)`. It is NOT a second implementation —
 * it used to be one, and that copy stopped at minutes, so a 90-minute source
 * video rendered as "90:00".
 */
export function formatTime(seconds) {
  return formatClock(seconds, { fallback: "--:--" })
}

export function viralityColor(score) {
  if (score >= 8) return "success"
  if (score >= 6) return "warning"
  return "default"
}

export function viralityLabel(score) {
  if (score >= 9) return "Viral"
  if (score >= 8) return "Strong"
  if (score >= 6) return "Good"
  if (score >= 4) return "Average"
  return "Low"
}

// Map AI-returned hook_type values to short user-facing labels. The closed set
// lives server-side in clip_extractor; "general" is the catch-all and renders
// as null (no chip) so we don't crowd the UI with an uninformative label.
const HOOK_TYPE_LABEL = {
  curiosity_gap:   "Curiosity gap",
  contrarian:      "Contrarian",
  emotional_peak:  "Emotional peak",
  question:        "Question hook",
  number_promise:  "Number promise",
  story_loop:      "Story loop",
  actionable_tip:  "Actionable tip",
  shocking_claim:  "Shocking claim",
}

export function hookTypeLabel(t) {
  if (!t || t === "general") return null
  return HOOK_TYPE_LABEL[t] || null
}
