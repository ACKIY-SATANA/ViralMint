import { useEffect, useState } from "react"
import {
  Box, Typography, ToggleButtonGroup, ToggleButton, Stack, Slider,
} from "@mui/material"
import TransformIcon from "@mui/icons-material/Transform"
import { useSearchParams } from "react-router-dom"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

const OPS = [
  { v: "flip_h", label: "Flip ◀▶" },
  { v: "flip_v", label: "Flip ▲▼" },
  { v: "rotate_cw", label: "Rotate ⟳" },
  { v: "rotate_ccw", label: "Rotate ⟲" },
  { v: "rotate_180", label: "Rotate 180°" },
  { v: "loop", label: "Loop" },
  { v: "volume", label: "Volume" },
  { v: "mute", label: "Remove audio" },
]

// Ops that can be deep-linked with ?op=… — the Tools hub lists "Remove Audio"
// as its own card because that's how people look for it, and it lands here
// with the operation already picked. Whitelisted so a hand-typed param can't
// set an operation the backend would reject.
const DEEP_LINKABLE = new Set(OPS.map((o) => o.v))
// "Mute" used to live here as volume=0, which is a subtly different thing:
// measured on ffmpeg 7.1, it re-encodes the audio to silence and leaves that
// SILENT AAC stream in the file. For "I don't want any sound on this" the
// track should be gone, not quiet — that's the `mute` OPERATION, which drops
// it with -an and skips the audio re-encode entirely.
const VOL_PRESETS = [
  { v: 0.5, label: "−50%" },
  { v: 1.5, label: "+50%" },
  { v: 2, label: "2×" },
]

export default function ToolTransform() {
  useDocumentTitle("Transform")
  const [operation, setOperation] = useState("flip_h")
  const [loopCount, setLoopCount] = useState(2)
  const [volume, setVolume] = useState(2)
  const [params] = useSearchParams()

  // Deep link from the Tools hub's "Remove Audio" card.
  const opParam = params.get("op")
  useEffect(() => {
    if (opParam && DEEP_LINKABLE.has(opParam)) setOperation(opParam)
  }, [opParam])

  const amount = operation === "loop" ? String(loopCount) : operation === "volume" ? String(volume) : ""

  return (
    <ToolRunner
      title="Transform"
      description="Quick edits — flip, rotate, loop, change volume, or strip the audio"
      icon={<TransformIcon fontSize="large" />}
      endpoint="/api/tools/transform"
      fieldBuilder={() => ({ operation, amount })}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>Operation</Typography>
          <ToggleButtonGroup
            exclusive size="small" value={operation}
            onChange={(_e, v) => v && setOperation(v)}
            sx={{ flexWrap: "wrap", gap: 0.5, "& .MuiToggleButtonGroup-grouped": { border: 1, borderColor: "divider", borderRadius: "8px !important", mx: 0 } }}
          >
            {OPS.map((o) => <ToggleButton key={o.v} value={o.v} sx={{ textTransform: "none" }}>{o.label}</ToggleButton>)}
          </ToggleButtonGroup>
        </Box>

        {operation === "loop" && (
          <Box>
            <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
              Repeat the clip {loopCount}× (back-to-back)
            </Typography>
            <Slider value={loopCount} onChange={(_e, v) => setLoopCount(v)} min={2} max={20} step={1} marks valueLabelDisplay="auto" sx={{ maxWidth: 360 }} />
          </Box>
        )}

        {operation === "volume" && (
          <Box>
            <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>Volume</Typography>
            <ToggleButtonGroup exclusive size="small" value={volume} onChange={(_e, v) => v !== null && setVolume(v)}>
              {VOL_PRESETS.map((p) => <ToggleButton key={p.v} value={p.v} sx={{ textTransform: "none" }}>{p.label}</ToggleButton>)}
            </ToggleButtonGroup>
          </Box>
        )}
      </Stack>
    </ToolRunner>
  )
}
