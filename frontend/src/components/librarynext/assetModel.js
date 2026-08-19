// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * assetModel — the Library taxonomy, as data. The frontend TWIN of
 * backend/services/library_taxonomy.py: the Library page, the pending tiles,
 * the Activity panel and the Library pickers all classify through this file,
 * and `payloadKind` / `toLibraryPick` are the wire contract the backend
 * resolvers read.
 *
 * The model is two ORTHOGONAL axes plus lineage:
 *
 *   media  = video | image | audio | doc      — what the file IS
 *   origin = created | imported | edited      — where it CAME FROM
 *   parent = the asset this one was made from — lineage
 *
 * Every item has exactly ONE answer on each axis:
 *
 *   downloaded mp3        → audio  · imported
 *   AI voiceover          → audio  · created
 *   audio pulled from a
 *   video you own         → audio  · edited   (parent = that video)
 *
 * PRODUCERS mirrors the backend's producer map: job_type / source_type →
 * { label, media, origin } (rule #32 — the backend map is the source of
 * truth; keep this one in step, with the drift pinned by assetModel.test.js).
 * Anything that writes a durable file and is missing here is invisible in the
 * Library — the exact bug the rewrite exists to fix.
 */

import { toDate } from "../../utils/format"

// ── The origin axis ─────────────────────────────────────────────────────────
// Colour is reserved for provenance ONLY — media type is carried by glyph and
// tile shape. One colour system, one meaning, so a 60-tile grid is readable
// without reading a single word.
export const ORIGINS = {
  created: {
    label: "Created",
    hint: "Made in ViralMint from a prompt, script or brief",
    light: "#c96442",   // brand terracotta — the app's own act of making
    dark: "#e88a5a",
  },
  imported: {
    // Label, not key: `imported` stays on the wire (URLs, API, picker payloads).
    // The word was wrong — the bucket is overwhelmingly videos downloaded from
    // links, so "Imported" read as a claim about the handful of disk imports.
    // "Sources" covers all three and matches the "By source" grouping.
    label: "Sources",
    hint: "Raw material that came from outside — downloaded from a link, imported from your disk, or uploaded",
    light: "#0e7490",
    dark: "#22b8cf",
  },
  edited: {
    label: "Edited",
    hint: "Made from something already in your library — a clip, a reframe, burned captions, extracted audio",
    light: "#6d5bd0",
    dark: "#a78bfa",
  },
}

export const ORIGIN_KEYS = ["created", "edited", "imported"]

export function originColor(origin, isDark) {
  const o = ORIGINS[origin] || ORIGINS.created
  return isDark ? o.dark : o.light
}

// ── The media axis ──────────────────────────────────────────────────────────
export const MEDIA = {
  video: { label: "Video", plural: "videos" },
  image: { label: "Image", plural: "images" },
  audio: { label: "Audio", plural: "audio files" },
  doc: { label: "Files", plural: "files" },
}

export const MEDIA_KEYS = ["video", "image", "audio", "doc"]

// ── The producer map (the spec) ─────────────────────────────────────────────
// Keys are the values the backend already stores: generated_videos.source_type
// and jobs.job_type. This mirrors backend/services/library_taxonomy.PRODUCERS
// entry for entry, and a test compares the two literally — a producer that
// exists on one side only is a row the Library files one way and labels
// another.
export const PRODUCERS = {
  // generated_videos.source_type — the studios.
  smart_video: { label: "Stock Video", media: "video", origin: "created" },
  motion_graphics: { label: "Motion Graphics", media: "video", origin: "created" },
  // A clip is cut FROM a source you own — derived, not created. Filing it under
  // "Generated" is why a clip and the video it came from could never be seen
  // together.
  clip_extraction: { label: "Clipper", media: "video", origin: "edited" },

  // Made here from nothing you already owned.
  "tool:voiceover": { label: "Voice-over", media: "audio", origin: "created" },

  // Editors — every one of these is EDITED from something you already own, and
  // not one of them appeared anywhere before the Library index existed.
  "tool:captions": { label: "Captions", media: "video", origin: "edited" },
  "tool:reframe": { label: "Reframe", media: "video", origin: "edited" },
  "tool:trim": { label: "Trim", media: "video", origin: "edited" },
  "tool:speed": { label: "Speed", media: "video", origin: "edited" },
  "tool:transform": { label: "Transform", media: "video", origin: "edited" },
  "tool:merge_clips": { label: "Merge", media: "video", origin: "edited" },
  "tool:watermark": { label: "Watermark", media: "video", origin: "edited" },
  "tool:auto_zoom": { label: "Auto zoom", media: "video", origin: "edited" },
  "tool:remove_silence": { label: "Silence cut", media: "video", origin: "edited" },
  "tool:compress": { label: "Compress", media: "video", origin: "edited" },
  "tool:crop": { label: "Crop", media: "video", origin: "edited" },
  "tool:translate": { label: "Translate", media: "video", origin: "edited" },
  "tool:music_visualizer": { label: "Visualizer", media: "video", origin: "edited" },
  "tool:audio_enhance": { label: "Enhanced", media: "video", origin: "edited" },
  "tool:gif": { label: "GIF", media: "image", origin: "edited" },
  "tool:subtitle_export": { label: "Subtitles", media: "doc", origin: "edited" },
  "tool:auto_chapters": { label: "Chapters", media: "doc", origin: "edited" },
  "tool:metadata": { label: "Metadata", media: "doc", origin: "edited" },

  // Things that came from outside.
  download: { label: "Download", media: "video", origin: "imported" },
  import: { label: "Import", media: "video", origin: "imported" },
  music_upload: { label: "Upload", media: "audio", origin: "imported" },
}

/** Classify a stored row. `mediaOverride` covers the one case the key can't
 *  answer: a download whose file is audio-only. */
export function classify(producerKey, mediaOverride) {
  const key = normalizeProducerKey(producerKey)
  const p = PRODUCERS[key]
  if (!p) return { label: key || "Unknown", media: mediaOverride || "video", origin: "created", unmapped: true }
  return { ...p, media: mediaOverride || p.media }
}

// ── Formatting helpers ──────────────────────────────────────────────────────
// Durations are NOT formatted here: `formatClock` in utils/format.js is the
// app's one clock formatter, and even a wrapper around it drifts (a second copy
// of /60 arithmetic is what tests/test_no_local_formatters.py exists to stop).
// Call sites import it directly; it returns "" for null, which every consumer
// already treats as "no duration".

/** Compact age for tiles — "3h" / "2d" / "Jul 24". Tiles are ~200px wide and
 *  the tool label is the more useful half of the meta line, so the date gets
 *  the short form; the drawer uses fmtDayLong. */
export function fmtDay(iso) {
  if (!iso) return ""
  const d = toDate(iso)
  if (!d) return ""
  const mins = Math.floor((Date.now() - d) / 60000)
  if (mins < 60) return "now"
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

export function fmtDayLong(iso) {
  if (!iso) return ""
  const d = toDate(iso)
  if (!d) return ""
  const days = Math.floor((Date.now() - d) / 86400000)
  if (days <= 0) return "Today"
  if (days === 1) return "Yesterday"
  if (days < 7) return `${days} days ago`
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

export function fmtSize(mb) {
  if (mb == null) return null
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`
}

/** Deterministic pseudo-random 0..1 from a string — drives the audio waveform
 *  bars so a track always draws the same shape (no Math.random flicker). */
export function hashUnit(str, i) {
  let h = 2166136261
  const s = `${str}:${i}`
  for (let k = 0; k < s.length; k++) {
    h ^= s.charCodeAt(k)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 1000) / 1000
}

export function waveformBars(id, count = 30) {
  return Array.from({ length: count }, (_, i) => 0.22 + hashUnit(id, i) * 0.78)
}

/** Fold a stored key onto its canonical form — the frontend half of
 *  `library_taxonomy.normalize_producer_key`. Endpoints are hyphenated and job
 *  types are not, and both spellings have reached this map. */
export function normalizeProducerKey(raw) {
  const key = String(raw || "").trim()
  const i = key.indexOf(":")
  return i === -1 ? key.replace(/-/g, "_") : `${key.slice(0, i)}:${key.slice(i + 1).replace(/-/g, "_")}`
}

// ── Jobs ────────────────────────────────────────────────────────────────────
// Job types that never produce a library item but DO belong in Activity: a
// failed download or a stuck scout is exactly what that panel exists to show.
// Kept out of PRODUCERS on purpose — that map answers "where does this file sit
// in the library", and these have no file.
export const JOB_ONLY_LABELS = {
  scout: "Scout",
  news_scout: "News scout",
  news_save: "Saved article",
  analyze: "Analysis",
  channel_analysis: "Channel analysis",
  generate: "Stock Video",
  extract_clips: "Clipper",
  motion_render: "Motion render",
  motion_compose: "AI Compose",
  upload: "Upload",
  "tool:hook_analysis": "Hook analysis",
  // NOT here: tool:metadata (.json) and tool:podcast_series (.zip). Both write
  // a durable file the user can download, both are producers in the map above,
  // and listing them here told Activity the opposite — so neither earned a
  // pending tile while it ran, and a finished row claimed to produce no library
  // item while still linking to one. A key may be in PRODUCERS or here, never
  // both; the taxonomy drift test asserts exactly that.
}

// Job types that don't classify (their PRODUCT registers its own row — a
// generated_videos entry — so the job type has no producer of its own) but
// whose output DOES land in the Library: a running one earns a pending tile
// in the grid where the result will appear. Everything else in
// JOB_ONLY_LABELS stays Activity-only.
const RENDER_JOB_TYPES = {
  generate: { media: "video", origin: "created" },
  motion_render: { media: "video", origin: "created" },
  // Clip extraction registers one generated_videos row per clip, so a running
  // one is work that WILL land in the grid.
  extract_clips: { media: "video", origin: "edited" },
}

const JOB_STATE = {
  running: "running",
  pending: "running",
  queued: "running",
  success: "done",
  failed: "failed",
  // Its own state, NOT "failed": a deliberate cancel in the red failure strip
  // and "Needs attention" reads as the app breaking on the user's own choice.
  cancelled: "cancelled",
}

const _trunc = (s, n = 80) => {
  s = String(s ?? "").trim()
  return s.length > n ? s.slice(0, n - 1) + "…" : s
}

/** A concise, human detail from a job's input_json — WHAT it is working on
 *  (the niche when scouting, the video when downloading, the tool params),
 *  not just the type label. Ported from the old Jobs tab's jobDetail: without
 *  it every Activity row read as its bare type ("Smart Video", "Download")
 *  and the user couldn't find their own job in the list. Best-effort — ""
 *  falls back to the type label. */
export function jobDetail(job, input) {
  const p = input || {}
  const t = normalizeProducerKey(job.job_type)

  if (t === "scout") {
    const plats = Array.isArray(p.platforms) ? p.platforms.join(", ") : ""
    return [p.niche, plats].filter(Boolean).join(" · ")
  }
  if (t === "download") {
    if (p.title) return _trunc(p.title)
    if (p.url) return _trunc(p.url)
    if (Array.isArray(p.batch_urls)) {
      const n = p.count || p.batch_urls.length
      return `${n} video${n === 1 ? "" : "s"}`
    }
    if (Array.isArray(p.scout_result_ids)) {
      const n = p.scout_result_ids.length
      return `${n} scouted video${n === 1 ? "" : "s"}`
    }
    if (p.channel_download) return `Channel${p.max_videos ? ` · top ${p.max_videos}` : ""}`
    return ""
  }
  if (t === "analyze") {
    if (p.type === "channel_analysis" && p.url) return `Channel · ${_trunc(p.url, 50)}`
    if (Array.isArray(p.video_ids)) {
      const n = p.video_ids.length
      return `${n} video${n === 1 ? "" : "s"}`
    }
    if (p.url) return _trunc(p.url)
    return ""
  }
  if (t === "generate") {
    if (p.type === "clip_extraction") {
      const bits = []
      if (p.max_clips) bits.push(`up to ${p.max_clips} clips`)
      if (p.mode && p.mode !== "auto") bits.push(String(p.mode))
      return bits.join(" · ")
    }
    if (p.topic) return _trunc(p.topic)
    if (p.custom_script) return _trunc(p.custom_script, 60)
    if (p.downloaded_video_id) return "From a downloaded video"
    return ""
  }
  if (t === "news_scout") {
    return _trunc(p.query || p.direct_url || (Array.isArray(p.sources) ? p.sources.join(", ") : ""))
  }
  if (t.startsWith("tool:")) {
    // Surface the most salient param(s) the tool was run with.
    const bits = []
    if (p.format) bits.push(String(p.format).toUpperCase())
    if (p.density) bits.push(`${p.density} density`)
    if (p.speed) bits.push(`${p.speed}×`)
    if (p.style) bits.push(String(p.style))
    if (p.transition === "crossfade") bits.push("crossfade")
    if (p.target_aspect && p.target_aspect !== "auto") bits.push(p.target_aspect)
    if (p.clip_count) bits.push(`${p.clip_count} clips`)
    if (p.target_count) bits.push(`${p.target_count} chapters`)
    if (p.zoom_factor) bits.push(`${Math.round((p.zoom_factor - 1) * 100)}% zoom`)
    if (p.start_seconds != null && p.end_seconds != null) bits.push(`${p.start_seconds}s–${p.end_seconds}s`)
    if (p.mode) bits.push(String(p.mode))
    if (p.title) bits.push(`"${_trunc(p.title, 40)}"`)
    if (p.url) bits.push(_trunc(p.url, 50))
    return bits.join(" · ")
  }
  return ""
}

/** Project an /api/jobs row onto what Activity and the pending tiles render.
 *
 *  `result_key` is how "open what it made" resolves: a clip job registers a
 *  generated_videos row (library key `gv:<id>`), while every other tool output
 *  is addressed by its own job id. Only mapped producers get a `job:` key — an
 *  unmapped job's file (e.g. a podcast-series ZIP) is not in the index, so the
 *  link would open onto a 404.
 *
 *  `libraryBound` says whether the output will land in the Library at all —
 *  the grid's pending tiles filter on it, because a running scout or hook
 *  analysis never becomes a tile no matter how long you wait.
 */
export function activityFromJob(job) {
  const norm = normalizeProducerKey(job.job_type)
  let p = classify(job.job_type)
  let resultKey = null
  let input = null
  try { input = job.input_json ? JSON.parse(job.input_json) : null } catch { /* label falls back */ }
  try {
    const out = job.output_json ? JSON.parse(job.output_json) : null
    if (out?.generated_video_id) resultKey = `gv:${out.generated_video_id}`
    else if (out?.file && !p.unmapped) resultKey = `job:${job.id}`
  } catch { /* malformed output_json — no result link, nothing else breaks */ }
  let label = JOB_ONLY_LABELS[norm] || p?.label || job.job_type
  // Render jobs land in the Library even though the JOB type doesn't classify
  // (the generated_videos row each one registers is the library item).
  let bound = norm in RENDER_JOB_TYPES || (!p.unmapped && !(norm in JOB_ONLY_LABELS))
  if (norm in RENDER_JOB_TYPES) p = { ...p, ...RENDER_JOB_TYPES[norm] }
  // Clip extraction rides job_type "generate" with the real producer in its
  // input — without this, a running Clipper run showed as "Smart Video" and
  // the user couldn't find their own job in Activity.
  if (norm === "generate" && input?.type === "clip_extraction") {
    p = classify("clip_extraction") || p
    label = p?.label || "Clipper"
    bound = true
  }
  // Headline precedence matches the old Jobs tab: the stored title, else what
  // the input says it's working on, else the type label — `where` keeps the
  // type explicit even when the headline is a video name.
  const detail = jobDetail(job, input)
  return {
    id: job.id,
    state: JOB_STATE[job.status] || "done",
    label: job.title || detail || label,
    where: label,
    percent: job.progress_pct != null ? Math.round(job.progress_pct) : null,
    step: job.current_step || "Working\u2026",
    error: job.error_message || null,
    at: job.completed_at || job.started_at || job.created_at,
    // Raw pair for "done in 2m 14s" — the panel formats it with the shared
    // formatDuration (this file stays import-free as the taxonomy mirror).
    started: job.started_at || null,
    completed: job.completed_at || null,
    media: p?.media || "video",
    origin: p?.origin || "created",
    producer: label,
    result_key: resultKey,
    libraryBound: bound,
  }
}

/** Payload `kind` for one index item — the vocabulary every consumer speaks.
 *
 *  It becomes `library_ref {kind, id}` (tool pages) or an entry in a by-id list
 *  (merge), and the backend resolvers read exactly these strings, so this map is
 *  a CONTRACT: `library` means "a tool output", handled by
 *  library_index.resolve_job_video. Shared by the picker and by ToolRunner's
 *  `?pick=` hand-off so the two can't drift.
 */
export function payloadKind(item) {
  if (item.source === "generated") return "generated"
  if (item.source === "downloaded") return "downloaded"
  if (item.source === "music") return "track"
  return item.media === "image" ? "image" : item.media === "audio" ? "audio" : "library"
}

/** An index item as the shape tool pages hold a Library pick in. */
export function toLibraryPick(item) {
  return {
    kind: payloadKind(item),
    id: item.id,
    title: item.title || "",
    media: item.media,
    streamUrl: item.stream_url,
    thumbUrl: item.thumb_url || null,
    createdAt: item.created_at || null,
  }
}
