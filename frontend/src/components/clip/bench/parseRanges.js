// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/* ── Free-text timestamps → ranges ─────────────────────────────
   The one thing the old Extract dialog's typed rows did better than a
   timeline: pasting times you already have — from a podcast's show notes,
   a chapter list, or a colleague's message. Dragging cannot reproduce
   "0:42-1:05, 2:10-2:38" quickly, so the capability moved here instead of
   being dropped with the dialog's manual tab.

   `parseTimestamp` mirrors clip_extractor._parse_timestamp so what this
   accepts is what the backend accepts. The backend re-validates and stays
   the authority — this exists to reject a typo before it becomes a 400.
*/

export function parseTimestamp(text) {
  if (typeof text === "number") return Number.isFinite(text) && text >= 0 ? text : null
  const s = String(text ?? "").trim()
  if (!s) return null
  const parts = s.split(":")
  if (parts.length > 3) return null
  const nums = parts.map((p) => parseFloat(p))
  if (nums.some((n) => !Number.isFinite(n) || n < 0)) return null
  // Seconds must be < 60 in multi-part forms; minutes too in HH:MM:SS.
  // A bare "70" is fine — that's 70 seconds.
  if (nums.length >= 2 && nums[nums.length - 1] >= 60) return null
  if (nums.length === 3 && nums[1] >= 60) return null
  if (nums.length === 1) return nums[0]
  if (nums.length === 2) return nums[0] * 60 + nums[1]
  return nums[0] * 3600 + nums[1] * 60 + nums[2]
}

// One range per line, or several per line separated by commas/semicolons.
// Accepts -, –, —, → and "to" between the two times, because that is what
// people actually paste.
const SEP = /\s*(?:-{1,2}|–|—|→|>|\bto\b)\s*/

/**
 * Parse pasted text into ranges.
 *
 * Returns `{ranges, errors}` rather than throwing: a paste of twelve lines
 * with one typo should add the eleven good ones and say which line failed,
 * not refuse the lot.
 *
 * @param {string} text
 * @param {number} duration  source length; ranges beyond it are rejected
 *   here rather than at submit, where the 400 names a row number the user
 *   can no longer see.
 * @param {number} minLen    shortest acceptable clip, in seconds
 */
export function parseRangeText(text, duration = 0, minLen = 1) {
  const ranges = []
  const errors = []
  const chunks = String(text || "")
    .split(/[\n;,]+/)
    .map((c) => c.trim())
    .filter(Boolean)

  for (const chunk of chunks) {
    const parts = chunk.split(SEP).filter(Boolean)
    if (parts.length !== 2) {
      errors.push(`${chunk} — need a start and an end`)
      continue
    }
    const start = parseTimestamp(parts[0])
    const end = parseTimestamp(parts[1])
    if (start == null || end == null) {
      errors.push(`${chunk} — not a time`)
      continue
    }
    if (end <= start) {
      errors.push(`${chunk} — ends before it starts`)
      continue
    }
    if (end - start < minLen) {
      errors.push(`${chunk} — shorter than ${minLen}s`)
      continue
    }
    if (duration && start >= duration) {
      errors.push(`${chunk} — starts past the end of the video`)
      continue
    }
    ranges.push({ start, end: duration ? Math.min(end, duration) : end })
  }
  ranges.sort((a, b) => a.start - b.start)
  return { ranges, errors }
}
