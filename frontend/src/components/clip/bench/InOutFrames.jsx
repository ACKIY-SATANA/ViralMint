// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material"
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft"
import ChevronRightIcon from "@mui/icons-material/ChevronRight"
import MyLocationIcon from "@mui/icons-material/MyLocation"

/* ── IN / OUT frame previews ───────────────────────────────────
   The answer to "what am I actually cutting?" — the first frame the clip
   will open on and the last one it will end on.

   ⚠️ These were <video> elements seeked to the in/out points, and that is
   the single worst thing this feature has done. A media element that is
   only ever seeked still behaves like a player: it holds a range response
   open unconsumed, and on the real page its seek request came back
   ERR_ABORTED and the element sat in `seeking` forever. Measured over a
   700ms edge drag, the OUT preview updated TWICE and then froze — the
   "takes seconds to visualize" the bench was reported for. Two rewrites of
   the seek pump (rAF polling, then event-driven) both measured the same,
   because the pump was never the problem.

   Two layers instead, neither of which is a media element:

     proxy — the filmstrip sprite already in the browser's cache, offset to
       whichever cell covers `time`. Costs nothing, updates on the same
       frame as the drag, and is coarse (one cell ≈ duration/cells).
     exact — GET /downloaded/{id}/frame?t=, debounced past the end of the
       gesture. ~200ms cold, ~15ms cached, and it paints over the proxy.

   So the drag feels live and the settled frame is exact. Nothing here can
   stall: an <img> that fails to load leaves the proxy showing.
*/

const FRAME_STEP = 1 / 30
const EXACT_DEBOUNCE_MS = 130

function stampOf(time) {
  if (time == null || !Number.isFinite(time)) return "--:--.--"
  const m = Math.floor(time / 60)
  const s = time - m * 60
  return `${m}:${s.toFixed(2).padStart(5, "0")}`
}

function FramePane({
  sourceId, time, duration, aspect, strip,
  label, accent, disabled, onNudge, onSetToPlayhead,
}) {
  // The exact frame lags the handle on purpose — one request per settle,
  // not one per pointermove.
  const [exactT, setExactT] = useState(null)
  const [exactReady, setExactReady] = useState(false)
  const timer = useRef(0)

  useEffect(() => {
    if (time == null || !Number.isFinite(time)) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setExactT(Math.round(time * 10) / 10), EXACT_DEBOUNCE_MS)
    return () => clearTimeout(timer.current)
  }, [time])

  useEffect(() => { setExactReady(false) }, [exactT, sourceId])

  const cells = strip?.cells || 0
  const cellIdx = (cells && duration)
    ? Math.max(0, Math.min(cells - 1, Math.floor((time / duration) * cells)))
    : null

  const exactUrl = (sourceId && exactT != null)
    ? `/api/downloaded/${sourceId}/frame?t=${exactT}&w=288`
    : null

  return (
    <Box sx={{ width: 132, flexShrink: 0, pt: 2.2 }}>
      <Typography sx={{
        fontSize: "0.6rem", fontWeight: 800, letterSpacing: 0.8,
        color: disabled ? "text.disabled" : accent, mb: 0.4,
      }}>
        {label}
      </Typography>
      <Box sx={(t) => ({
        position: "relative",
        width: "100%", aspectRatio: String(aspect || 16 / 9),
        borderRadius: 1.5, overflow: "hidden",
        bgcolor: "#000",
        border: "2px solid",
        borderColor: disabled ? "divider" : accent,
        opacity: disabled ? 0.45 : 1,
        boxShadow: disabled ? "none"
          : t.palette.mode === "dark"
            ? "0 4px 16px rgba(0,0,0,0.5)"
            : "0 4px 16px rgba(60,70,110,0.2)",
      })}>
        {/* Layer 1 — instant, coarse. Plain inline style: this repaints on
            every pointermove and an `sx` object would recompile emotion
            styles at the same rate. */}
        {!disabled && strip?.url && cellIdx != null && (
          <div
            aria-hidden
            style={{
              position: "absolute", inset: 0,
              backgroundImage: `url("${strip.url}")`,
              backgroundSize: `${cells * 100}% 100%`,
              backgroundPosition: cells > 1 ? `${(cellIdx / (cells - 1)) * 100}% 0` : "0 0",
              backgroundRepeat: "no-repeat",
              filter: exactReady ? "none" : "blur(1px)",
            }}
          />
        )}
        {/* Layer 2 — exact, debounced. */}
        {!disabled && exactUrl && (
          <Box
            component="img"
            src={exactUrl}
            alt=""
            draggable={false}
            onLoad={() => setExactReady(true)}
            onError={() => setExactReady(false)}
            sx={{
              position: "absolute", inset: 0,
              width: "100%", height: "100%", objectFit: "cover",
              opacity: exactReady ? 1 : 0,
              transition: "opacity .12s ease",
            }}
          />
        )}
        {disabled && (
          <Typography sx={{
            position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center",
            fontSize: "0.65rem", color: "text.disabled", textAlign: "center", px: 1,
          }}>
            No range selected
          </Typography>
        )}
      </Box>

      <Stack direction="row" alignItems="center" spacing={0.25} sx={{ mt: 0.4 }}>
        <Typography sx={{
          flex: 1, fontFamily: "ui-monospace, monospace",
          fontSize: "0.68rem", fontWeight: 700,
          color: disabled ? "text.disabled" : "text.primary",
        }}>
          {stampOf(time)}
        </Typography>
        <Tooltip title="Back one frame">
          <span>
            <IconButton size="small" disabled={disabled} aria-label={`${label} back one frame`}
              onClick={() => onNudge(-FRAME_STEP)} sx={{ p: 0.15 }}>
              <ChevronLeftIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title={`Snap ${label} to the playhead`}>
          <span>
            <IconButton size="small" disabled={disabled} aria-label={`Snap ${label} to the playhead`}
              onClick={onSetToPlayhead} sx={{ p: 0.15 }}>
              <MyLocationIcon sx={{ fontSize: 13 }} />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Forward one frame">
          <span>
            <IconButton size="small" disabled={disabled} aria-label={`${label} forward one frame`}
              onClick={() => onNudge(FRAME_STEP)} sx={{ p: 0.15 }}>
              <ChevronRightIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
    </Box>
  )
}

const MemoPane = memo(FramePane)

export default function InOutFrames({
  sourceId, range, playhead, duration, aspect, strip, onChange, children,
}) {
  const has = !!range
  // `common` and the four callbacks were rebuilt every render, so MemoPane
  // never once skipped a render — memo() on a component whose props are all
  // fresh object identities is decoration. This is the surface that repaints
  // on every pointermove of a drag, which is the one place it matters.
  const common = useMemo(
    () => ({ sourceId, duration, aspect, strip, disabled: !has }),
    [sourceId, duration, aspect, strip, has],
  )
  // The handlers read `range` and `playhead`, which change constantly during
  // a drag — but only the pane whose bound moved should re-render, so they
  // go through a ref instead of into the dependency list.
  const live = useRef(null)
  live.current = { range, playhead, onChange }
  const inNudge = useCallback((d) => {
    const { range: r, onChange: f } = live.current
    if (r) f({ start: r.start + d })
  }, [])
  const outNudge = useCallback((d) => {
    const { range: r, onChange: f } = live.current
    if (r) f({ end: r.end + d })
  }, [])
  const inToPlayhead = useCallback(() => {
    const { playhead: p, onChange: f } = live.current
    f({ start: p })
  }, [])
  const outToPlayhead = useCallback(() => {
    const { playhead: p, onChange: f } = live.current
    f({ end: p })
  }, [])
  return (
    // Centred with the panes shrink-to-fit, NOT a flex:1 middle. A greedy
    // centre column parks IN and OUT against the far edges of a wide
    // window, which is the opposite of the point: the three frames only
    // read as one shot when they sit beside each other.
    <Stack
      direction="row" spacing={1.5}
      alignItems="flex-start" justifyContent="center"
      sx={{ width: "100%", height: "100%", minHeight: 0 }}
    >
      <MemoPane
        {...common} label="IN" accent="primary.main"
        time={has ? range.start : null}
        onNudge={inNudge}
        onSetToPlayhead={inToPlayhead}
      />
      {/* The player lives between the two frames, so the eye reads
          first-frame → what you're scrubbing → last-frame in one pass. */}
      <Box sx={{
        // alignSelf:stretch, not height:100% — the row is alignItems:
        // flex-start so the IN/OUT panes stay top-aligned, and a percentage
        // height against an auto-height flex line resolves to nothing.
        flex: "0 1 auto", minWidth: 0, alignSelf: "stretch", minHeight: 0,
        display: "flex", alignItems: "flex-start", justifyContent: "center",
      }}>
        {children}
      </Box>
      <MemoPane
        {...common} label="OUT" accent="secondary.main"
        time={has ? range.end : null}
        onNudge={outNudge}
        onSetToPlayhead={outToPlayhead}
      />
    </Stack>
  )
}
