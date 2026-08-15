// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useRef, useEffect } from "react"
import {
  Box, Typography, Stack, Button, ToggleButton, ToggleButtonGroup,
} from "@mui/material"
import CropOutlinedIcon from "@mui/icons-material/CropOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import { useToolInput } from "../../components/tools/ToolInputContext"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Aspect presets, as width/height ratios. "free" leaves the box unconstrained.
const PRESETS = [
  { value: "free", label: "Free", ratio: 0 },
  { value: "9:16", label: "9:16", ratio: 9 / 16 },
  { value: "1:1", label: "1:1", ratio: 1 },
  { value: "16:9", label: "16:9", ratio: 16 / 9 },
]

// Matches CROP_MIN_PIXELS in the runner closely enough to stop a useless
// submit; the backend still validates against the real source size.
const MIN_FRACTION = 0.03

/**
 * Constrain a drawn box to an aspect ratio, in NORMALIZED space.
 *
 * The subtlety: normalized coordinates are not square. A 0.5 x 0.5 box on a
 * 1920x1080 source is 960x540 pixels — already 16:9, not 1:1. So the target
 * ratio has to be converted into normalized units by the source's own aspect
 * before it means anything. Getting this wrong yields a "1:1" crop that isn't
 * square, which looks like the preset is broken.
 */
function applyRatio(box, ratio, srcW, srcH) {
  if (!ratio || !srcW || !srcH) return box
  const srcAspect = srcW / srcH
  // Normalized height for a given normalized width at the target ratio.
  const hForW = (bw) => (bw * srcAspect) / ratio
  let { x, y, w, h } = box
  const wantH = hForW(w)
  if (wantH <= 1) {
    h = wantH
  } else {
    // Too tall to fit — drive from height instead.
    h = 1
    w = (h * ratio) / srcAspect
  }
  // Keep the box inside the frame after the ratio pass.
  if (x + w > 1) x = Math.max(0, 1 - w)
  if (y + h > 1) y = Math.max(0, 1 - h)
  return { x, y, w, h }
}

function CropCanvas({ box, setBox, preset }) {
  const { previewUrl, width: srcW, height: srcH } = useToolInput()
  const wrapRef = useRef(null)
  const [drag, setDrag] = useState(null)
  const ratio = PRESETS.find((p) => p.value === preset)?.ratio || 0

  // Re-apply the ratio when the preset changes so the existing box snaps
  // instead of staying stale until the next drag.
  useEffect(() => {
    if (!box || !ratio) return
    setBox(applyRatio(box, ratio, srcW, srcH))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset])

  if (!previewUrl) {
    return (
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        Add a video, then drag a box over the frame to set the crop.
      </Typography>
    )
  }

  const rel = (e) => {
    const r = wrapRef.current.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    }
  }
  const onDown = (e) => {
    const p = rel(e)
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y })
  }
  const onMove = (e) => {
    if (!drag) return
    const p = rel(e)
    setDrag((d) => ({ ...d, x1: p.x, y1: p.y }))
  }
  const onUp = () => {
    if (!drag) return
    const x = Math.min(drag.x0, drag.x1)
    const y = Math.min(drag.y0, drag.y1)
    const w = Math.abs(drag.x1 - drag.x0)
    const h = Math.abs(drag.y1 - drag.y0)
    setDrag(null)
    // A tap (rather than a drag) clears the box back to "whole frame".
    setBox(w > MIN_FRACTION && h > MIN_FRACTION
      ? applyRatio({ x, y, w, h }, ratio, srcW, srcH)
      : null)
  }

  const live = drag
    ? {
        x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1),
        w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0),
      }
    : box

  return (
    <Box>
      <Box
        ref={wrapRef}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
        sx={{
          position: "relative", width: "100%", borderRadius: 1.5,
          overflow: "hidden", cursor: "crosshair", userSelect: "none",
          bgcolor: "black", border: 1, borderColor: "divider",
        }}
      >
        <Box
          component="video"
          src={previewUrl}
          muted
          playsInline
          preload="metadata"
          sx={{ display: "block", width: "100%", maxHeight: 420, objectFit: "contain",
                pointerEvents: "none" }}
        />
        {live && (
          // ONE element does both jobs: the border marks the keep-area, and a
          // huge non-blurred spread shadow paints everything OUTSIDE it dark.
          // (An inset overlay clipped to the box can't work — clipping to the
          // crop region throws away the very pixels the dimming needs.)
          <Box sx={{
            position: "absolute", pointerEvents: "none",
            left: `${live.x * 100}%`, top: `${live.y * 100}%`,
            width: `${live.w * 100}%`, height: `${live.h * 100}%`,
            border: 2, borderColor: "primary.main",
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
          }} />
        )}
      </Box>

      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mt: 1 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", flex: 1 }}>
          {box && srcW > 0
            ? `Crop ${Math.round(srcW * box.w) & ~1} × ${Math.round(srcH * box.h) & ~1} px `
              + `from ${srcW} × ${srcH}`
            : "Drag a box over the frame. Tap once to reset to the whole frame."}
        </Typography>
        {box && (
          <Button size="small" onClick={() => setBox(null)}>Reset</Button>
        )}
      </Stack>
    </Box>
  )
}

export default function ToolCrop() {
  useDocumentTitle("Crop Video")
  const [box, setBox] = useState(null)          // {x,y,w,h} normalized, or null
  const [preset, setPreset] = useState("free")

  return (
    <ToolRunner
      title="Crop Video"
      description="Drag a box over the frame and keep just that part."
      icon={<CropOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/crop"
      processLabel="Crop"
      downloadLabel="Download cropped"
      canSubmit={!!box}
      disabledReason={box ? "" : "Drag a crop box over the frame"}
      costNote={
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Runs locally with FFmpeg — the video never leaves your machine. The
          audio track is copied across untouched, so cropping costs nothing in
          sound quality. Want the framing chosen for you instead? Reframe to
          Vertical tracks faces and picks the crop itself.
        </Typography>
      }
      fieldBuilder={() => ({
        x: box?.x ?? 0, y: box?.y ?? 0, w: box?.w ?? 1, h: box?.h ?? 1,
      })}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.75 }}>
            Shape
          </Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={preset}
            onChange={(_e, v) => v && setPreset(v)}
          >
            {PRESETS.map((p) => (
              <ToggleButton key={p.value} value={p.value} sx={{ px: 1.75 }}>
                {p.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        <CropCanvas box={box} setBox={setBox} preset={preset} />
      </Stack>
    </ToolRunner>
  )
}
