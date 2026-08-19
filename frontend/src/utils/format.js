// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Shared formatters. One implementation each, so the same value never renders
 * two different ways on two screens.
 */

const NAIVE_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

/**
 * Coerce the timestamp shapes this app receives into a Date.
 *
 * A bare number is UNIX SECONDS. Returns null when the input can't be read as
 * a time, so callers render their own blank rather than "Invalid Date".
 *
 * ⚠️ A NAIVE datetime string is read as UTC, not local. Every timestamp the
 * backend stores is `datetime.utcnow()` — naive UTC — and FastAPI serializes it
 * with `.isoformat()`, which appends no offset. ECMAScript says a date-TIME
 * form without a designator is LOCAL time, so `new Date(raw)` shifts every
 * relative age by the viewer's UTC offset: at UTC+2, a job that started five
 * minutes ago renders as "2h ago". Reading it as UTC is what the producer meant.
 *
 * The space-separated SQLite form is normalized to "T" in the same step —
 * engines are not required to parse it, and Safari does not.
 */
export function toDate(input) {
  if (input == null || input === "") return null
  if (typeof input === "number") {
    const n = new Date(input * 1000)
    return Number.isNaN(n.getTime()) ? null : n
  }
  let raw = input
  if (typeof raw === "string" && NAIVE_DATETIME.test(raw.trim())) {
    raw = raw.trim().replace(" ", "T") + "Z"
  }
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * Seconds → a clock badge: "1:05", "1:02:03".
 *
 * Quantised to the requested precision BEFORE splitting into fields, so a value
 * that rounds up across a boundary carries into the minute rather than
 * rendering "0:60". Clamped at zero: a negative duration is a bug upstream and
 * a badge is not the place to surface it.
 */
export function formatClock(seconds, { fallback = "", padMinutes = false, decimals = 0 } = {}) {
  const n = Number(seconds)
  if (seconds == null || !Number.isFinite(n)) return fallback
  const factor = 10 ** decimals
  const total = Math.max(0, Math.round(n * factor) / factor)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total - h * 3600 - m * 60
  const ss = s.toFixed(decimals).padStart(decimals ? decimals + 3 : 2, "0")
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${ss}`
  return `${padMinutes ? String(m).padStart(2, "0") : m}:${ss}`
}

/**
 * Seconds → compact prose: "1h 30m" / "2m 51s" / "45s" / "<1s".
 *
 * Rounds first so float durations never leak floating-point noise into the UI.
 * Seconds are dropped once an hour is involved — "1h 30m 12s" is noise in a
 * meta line. Sub-second clips are real (a trimmed frame), and "0s" would read
 * as an empty or failed asset, so they are named as short instead.
 */
export function formatDuration(seconds) {
  const n = Number(seconds)
  const raw = Number.isFinite(n) ? Math.max(0, n) : 0
  const total = Math.round(raw)
  if (total === 0) return raw > 0 ? "<1s" : "0s"
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`
  if (m > 0) return s > 0 ? `${m}m ${s}s` : `${m}m`
  return `${s}s`
}
