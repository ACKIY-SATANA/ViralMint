// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Box, CircularProgress, Stack, Typography } from "@mui/material"
import RangeBlock from "./RangeBlock"
import { formatTime } from "../clipFormat"

/* ── The cutting bench timeline ────────────────────────────────
   Four stacked lanes over one shared time axis:

     filmstrip  — sampled frames, so scene changes are visible
     ranges     — pending cuts, drawn ON the filmstrip
     speech     — where the talking is (also the snap targets)
     already    — clips previously extracted from this source
     ruler      — labelled ticks

   This component owns ALL px↔seconds conversion; nothing below it knows
   how wide the track is. Gestures use pointer capture rather than window
   listeners, so a drag that leaves the element still ends cleanly.
*/

// The filmstrip lane is a FIXED dark plate (#0b0d12) in BOTH themes, because a
// filmstrip sits on it — so the theme's text colours do not apply to it. In
// light mode `text.disabled` resolves to a dark grey that measures 3.14:1 here,
// i.e. unreadable, which is exactly what the lane's own status messages were
// rendered in. 7.7:1 against the plate, in either theme.
const LANE_TEXT = "#9aa4b5"

const STRIP_H = 64      // filmstrip / range lane height
const SPEECH_H = 12
const GHOST_H = 8
const RULER_H = 16
const SNAP_PX = 7       // grab distance to a speech boundary
const DRAG_THRESHOLD_PX = 4  // below this a press is a seek, not a new range

// Cell count is quantized backend-side too; matching the step here means a
// resize inside one step reuses the cached strip instead of rebuilding it.
const STRIP_STEP = 4

function niceStep(duration, width) {
  // Aim for a label roughly every 90px, snapped to a human interval.
  const target = (duration / Math.max(width, 1)) * 90
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
  return steps.find((s) => s >= target) || 3600
}

export default function Timeline({
  sourceId,
  duration,
  aspect = 16 / 9,        // source frame aspect, for sizing filmstrip cells
  segments = [],          // [{start, end, text}]
  ranges = [],
  activeId,
  ghosts = [],            // [{start, end}] — clips already cut from this source
  playhead = 0,
  snapEnabled = true,
  onSeek,
  onRangeSelect,
  onRangeChange,          // (id, {start?, end?}) => void — live, per pointermove
  onRangeCommit,          // () => void — pointerup
  onRangeAdd,             // (start, end) => void
  onRangeRemove,
  onStrip,                // ({url, cells}) => void — the IN/OUT panes reuse
                          // this exact sprite as their instant proxy, so it
                          // must be the same URL the browser already cached
}) {
  const trackRef = useRef(null)
  const [width, setWidth] = useState(0)
  const [drag, setDrag] = useState(null)   // {kind, id, rect, grabOffset, span}
  const [draft, setDraft] = useState(null) // {start, end} while drag-creating
  const [stripFailed, setStripFailed] = useState(false)
  // A strip can fail for three quite different reasons and the difference is
  // the whole message:
  //   "ok"        ffmpeg hiccupped on an otherwise fine video — cosmetic, and
  //               the bench really does still work.
  //   "truncated" the file decodes its OPENING frames and nothing after: a
  //               download that stopped part-way. Most of the timeline is
  //               unusable even though the first seconds look fine.
  //   "dead"      no readable video at all — a download cancelled mid-write
  //               leaves a valid header, a plausible duration and no data.
  // "the timeline still works" is true only in the first case; in the other
  // two, playback, the IN/OUT frames and the cut itself all fail too. Two
  // probes on the failure path tell them apart — the same two ends the strip
  // builder itself checks — and they are free on the happy path because they
  // never run there.
  const [readable, setReadable] = useState("ok")
  // The sprite's true cell count, measured once it decodes (see below).
  const [stripCells, setStripCells] = useState(null)
  // Building a strip for a long source is a few seconds of ffmpeg the first
  // time. Saying so beats a black bar that looks broken.
  const [stripLoaded, setStripLoaded] = useState(false)

  // ── Measure ────────────────────────────────────────────────
  useEffect(() => {
    const el = trackRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setWidth(Math.round(entry.contentRect.width))
    })
    ro.observe(el)
    setWidth(Math.round(el.getBoundingClientRect().width))
    return () => ro.disconnect()
  }, [])

  useEffect(() => { setStripFailed(false); setStripLoaded(false); setStripCells(null) }, [sourceId])

  const pxPerSec = duration > 0 && width > 0 ? width / duration : 0
  const toX = useCallback((t) => (pxPerSec ? t * pxPerSec : 0), [pxPerSec])
  const toT = useCallback((x) => (pxPerSec ? x / pxPerSec : 0), [pxPerSec])

  // ── Filmstrip URL ──────────────────────────────────────────
  // Cell count follows the measured width so cells land at their natural
  // aspect. A strip built for a different width would either squash the
  // frames or repeat them — either way it stops reading as footage.
  const strip = useMemo(() => {
    if (!sourceId || !width) return null
    const cellW = Math.max(24, STRIP_H * aspect)
    const raw = Math.round(width / cellW)
    const n = Math.max(4, Math.min(96, Math.round(raw / STRIP_STEP) * STRIP_STEP))
    return { url: `/api/downloaded/${sourceId}/filmstrip?n=${n}&h=${STRIP_H}`, cells: n }
  }, [sourceId, width, aspect])

  // Hand the sprite to the IN/OUT panes only once it has actually decoded —
  // offering them a URL that is still loading would show them a blank proxy.
  //
  // `cells` comes from the DECODED image, not from what we asked for. The
  // endpoint caps density by duration (MIN_CELL_SEC), so a short source can
  // return fewer cells than `n` — and every consumer of this sprite offsets
  // into it by cell index, so believing the request would skew the IN/OUT
  // proxy across the whole strip. Measuring the answer cannot drift.
  // Ask for the FIRST and LAST frame when the strip fails. The endpoint decodes
  // on demand and caches per 0.1s bucket, so each is one cheap request, and a
  // truncated source is exactly the case a head-only probe gets wrong: its
  // opening frames decode, so asking only about t=0 would report the file as
  // healthy and print the reassurance over a timeline that mostly is not.
  useEffect(() => {
    if (!stripFailed || !sourceId) { setReadable("ok"); return }
    let alive = true
    const at = (t) => new Promise((resolve) => {
      const img = new Image()
      img.onload = () => resolve(true)
      img.onerror = () => resolve(false)
      img.src = `/api/downloaded/${sourceId}/frame?t=${t}&w=64`
    })
    // A hair inside the end: a container's duration runs to the END of its
    // last frame, so seeking exactly there decodes nothing even on a healthy
    // file — the same tail clamp the strip builder applies.
    const tail = Math.max(0, (duration || 0) - 0.5)
    Promise.all([at(0), at(tail.toFixed(2))]).then(([head, end]) => {
      if (!alive) return
      setReadable(!head ? "dead" : !end ? "truncated" : "ok")
    })
    return () => { alive = false }
  }, [stripFailed, sourceId, duration])

  useEffect(() => {
    onStrip?.(strip && stripLoaded && !stripFailed
      ? { ...strip, cells: stripCells || strip.cells }
      : null)
  }, [strip, stripLoaded, stripFailed, stripCells, onStrip])

  const measureCells = useCallback((img) => {
    const cellW = img.naturalHeight * aspect
    if (!cellW || !img.naturalWidth) return null
    return Math.max(1, Math.round(img.naturalWidth / cellW))
  }, [aspect])

  // ── Snap targets ───────────────────────────────────────────
  // Sentence edges from the transcript. Cutting mid-word is the commonest
  // way a hand-made clip reads as sloppy, and the boundaries are already
  // in the data — they just needed a way out of the DB.
  const snapPoints = useMemo(() => {
    if (!segments.length) return []
    const pts = []
    for (const s of segments) { pts.push(s.start); pts.push(s.end) }
    return [...new Set(pts)].sort((a, b) => a - b)
  }, [segments])

  const snap = useCallback((t, disabled) => {
    if (disabled || !snapEnabled || !snapPoints.length || !pxPerSec) return t
    const tol = SNAP_PX / pxPerSec
    let best = null
    let bestD = Infinity
    // Linear is fine: a long podcast is a few hundred boundaries and this
    // runs once per pointermove, not once per animation frame.
    for (const p of snapPoints) {
      const d = Math.abs(p - t)
      if (d < bestD) { bestD = d; best = p }
      else if (p > t) break
    }
    return bestD <= tol ? best : t
  }, [snapPoints, pxPerSec, snapEnabled])

  // ── Gestures ───────────────────────────────────────────────
  const beginGrab = (kind, id, e) => {
    const rect = trackRef.current?.getBoundingClientRect()
    const r = ranges.find((x) => x.id === id)
    if (!rect || !r) return
    e.currentTarget.setPointerCapture?.(e.pointerId)
    setDrag({
      kind, id, rect,
      // Where inside the block the cursor grabbed, so a moved block
      // doesn't jump its left edge under the pointer.
      grabOffset: toT(e.clientX - rect.left) - r.start,
      span: r.end - r.start,
    })
  }

  const onTrackPointerDown = (e) => {
    if (e.button !== 0 || !pxPerSec) return
    const rect = trackRef.current.getBoundingClientRect()
    const t = Math.max(0, Math.min(duration, toT(e.clientX - rect.left)))
    e.currentTarget.setPointerCapture?.(e.pointerId)
    // Ambiguous until it moves: a press is a seek, a press-and-drag makes
    // a range. Resolved in pointermove against DRAG_THRESHOLD_PX.
    setDrag({ kind: "create", rect, originT: t, originX: e.clientX })
    onSeek?.(t)
  }

  const onPointerMove = (e) => {
    if (!drag || !pxPerSec) return
    const raw = Math.max(0, Math.min(duration, toT(e.clientX - drag.rect.left)))
    const noSnap = e.altKey

    if (drag.kind === "create") {
      if (Math.abs(e.clientX - drag.originX) < DRAG_THRESHOLD_PX) return
      const a = snap(drag.originT, noSnap)
      const b = snap(raw, noSnap)
      let lo = Math.min(a, b)
      let hi = Math.max(a, b)
      // Clamp the draft against existing blocks so the preview shows what
      // add() will actually accept (it refuses overlaps — see the note
      // there). Without this the draft sweeps across a pending block and
      // then visibly shrinks on release, which reads as a glitch.
      for (const r of ranges) {
        if (lo >= r.start && lo < r.end) lo = r.end
        if (hi > r.start && lo <= r.start) hi = r.start
      }
      if (hi <= lo) return   // keep the last valid draft
      setDraft({ start: lo, end: hi })
      return
    }
    if (drag.kind === "move") {
      const snapped = snap(raw - drag.grabOffset, noSnap)
      onRangeChange?.(drag.id, { start: snapped, end: snapped + drag.span })
      return
    }
    onRangeChange?.(drag.id, { [drag.kind]: snap(raw, noSnap) })
  }

  const endDrag = () => {
    if (!drag) return
    if (drag.kind === "create") { if (draft) onRangeAdd?.(draft.start, draft.end) }
    else onRangeCommit?.()
    setDraft(null)
    setDrag(null)
  }

  // ── Ruler ticks ────────────────────────────────────────────
  const ticks = useMemo(() => {
    if (!duration || !width) return []
    const step = niceStep(duration, width)
    const out = []
    for (let t = 0; t <= duration + 0.001; t += step) out.push(t)
    return out
  }, [duration, width])

  const laneSx = { position: "relative", width: "100%", overflow: "hidden" }

  return (
    <Box sx={{ width: "100%", userSelect: "none" }}>
      <Box
        ref={trackRef}
        onPointerDown={onTrackPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        sx={{ position: "relative", width: "100%", touchAction: "none", cursor: "text" }}
      >
        {/* ── Filmstrip + range lane ─────────────────────── */}
        <Box sx={{
          ...laneSx,
          height: STRIP_H,
          borderRadius: 1.5,
          bgcolor: "#0b0d12",
          border: 1, borderColor: "divider",
        }}>
          {strip && !stripFailed && (
            <Box
              component="img"
              key={strip.url}
              src={strip.url}
              alt=""
              draggable={false}
              onLoad={(e) => {
                setStripCells(measureCells(e.currentTarget))
                setStripLoaded(true)
              }}
              onError={() => { setStripFailed(true); setStripLoaded(false) }}
              sx={{
                position: "absolute", inset: 0,
                width: "100%", height: "100%",
                objectFit: "fill",   // the cell count was chosen FOR this width
                opacity: stripLoaded ? 0.85 : 0,
                transition: "opacity .2s ease",
                pointerEvents: "none",
              }}
            />
          )}
          {strip && !stripFailed && !stripLoaded && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{
              position: "absolute", inset: 0, justifyContent: "center",
              pointerEvents: "none",
            }}>
              <CircularProgress size={14} thickness={5} sx={{ color: LANE_TEXT }} />
              <Typography variant="caption" sx={{ color: LANE_TEXT }}>
                Reading frames from this video…
              </Typography>
            </Stack>
          )}
          {stripFailed && (
            <Typography variant="caption" sx={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              textAlign: "center", px: 2,
              color: LANE_TEXT, pointerEvents: "none",
            }}>
              {readable === "dead"
                ? "This video has no readable frames — it may be an incomplete or cancelled download"
                : readable === "truncated"
                  ? "Only the beginning of this video is readable — it looks like an unfinished download, so most of the timeline won't play or cut"
                  : "Preview frames unavailable — the timeline still works"}
            </Typography>
          )}

          {/* Live drag-create preview */}
          {draft && (
            <Box sx={{
              position: "absolute", top: 0, bottom: 0,
              left: toX(draft.start), width: Math.max(2, toX(draft.end - draft.start)),
              bgcolor: "rgba(99,179,237,0.35)",
              border: "2px solid", borderColor: "primary.main",
              borderRadius: 1.5, pointerEvents: "none",
            }} />
          )}

          {ranges.map((r, i) => (
            <RangeBlock
              key={r.id}
              index={i}
              range={r}
              duration={duration}
              left={toX(r.start)}
              width={Math.max(6, toX(r.end - r.start))}
              selected={r.id === activeId}
              onSelect={() => onRangeSelect?.(r.id)}
              onRemove={() => onRangeRemove?.(r.id)}
              onGrab={(kind, e) => beginGrab(kind, r.id, e)}
            />
          ))}

          {/* Playhead — drawn last so it reads above every block. */}
          <Box sx={{
            position: "absolute", top: -2, bottom: -2,
            left: toX(playhead), width: 2, ml: "-1px",
            bgcolor: "#fff",
            boxShadow: "0 0 0 1px rgba(0,0,0,0.7), 0 0 8px rgba(255,255,255,0.5)",
            pointerEvents: "none",
          }} />
        </Box>

        {/* ── Speech + already-cut + ruler ────────────────
            All three are memoized and depend only on (data, pxPerSec) —
            none of them changes while a range is dragged. Before that, a
            pointermove re-rendered 235 MUI Boxes for the speech lane
            alone, each recompiling its `sx` through emotion. */}
        <SpeechLane segments={segments} pxPerSec={pxPerSec} laneSx={laneSx} />
        <GhostLane ghosts={ghosts} pxPerSec={pxPerSec} laneSx={laneSx} />
        <Ruler ticks={ticks} pxPerSec={pxPerSec} laneSx={laneSx} />
      </Box>
    </Box>
  )
}


/* The static lanes. Plain <div style> rather than <Box sx> on purpose:
   these render one node per segment and emotion's per-node style
   compilation is what made a busy transcript expensive. */

const SpeechLane = memo(function SpeechLane({ segments, pxPerSec, laneSx }) {
  return (
    <Box sx={{ ...laneSx, height: SPEECH_H, mt: 0.5 }}>
      {segments.length === 0 ? (
        <Box sx={{ position: "absolute", inset: 0, borderRadius: 1, bgcolor: "action.hover" }} />
      ) : segments.map((s, i) => (
        <div key={i} style={{
          position: "absolute", top: 2, bottom: 2,
          left: s.start * pxPerSec,
          width: Math.max(1, (s.end - s.start) * pxPerSec),
          background: "rgba(76,175,80,0.55)", borderRadius: 2,
          pointerEvents: "none",
        }} />
      ))}
    </Box>
  )
})

const GhostLane = memo(function GhostLane({ ghosts, pxPerSec, laneSx }) {
  return (
    <Box sx={{ ...laneSx, height: GHOST_H, mt: "2px" }}>
      {ghosts.map((g, i) => (
        <div key={i} style={{
          position: "absolute", top: 1, bottom: 1,
          left: g.start * pxPerSec,
          width: Math.max(2, (g.end - g.start) * pxPerSec),
          background: "rgba(140,140,150,0.5)", borderRadius: 2,
          pointerEvents: "none",
        }} />
      ))}
    </Box>
  )
})

const Ruler = memo(function Ruler({ ticks, pxPerSec, laneSx }) {
  return (
    <Box sx={{ ...laneSx, height: RULER_H, mt: "2px" }}>
      {ticks.map((t) => (
        <div key={t} style={{
          position: "absolute", left: t * pxPerSec, top: 0,
          transform: t === 0 ? "none" : "translateX(-50%)",
          display: "flex", flexDirection: "column",
          alignItems: t === 0 ? "flex-start" : "center",
          pointerEvents: "none",
        }}>
          <div style={{ width: 1, height: 4, background: "currentColor", opacity: 0.25 }} />
          <span style={{ fontSize: "0.6rem", opacity: 0.55, lineHeight: 1.1 }}>
            {formatTime(t)}
          </span>
        </div>
      ))}
    </Box>
  )
})
