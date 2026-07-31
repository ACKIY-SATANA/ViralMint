import { useState } from "react"
import {
  Box, Typography, Stack, Slider, Alert, IconButton, Tooltip,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import FormatListNumberedIcon from "@mui/icons-material/FormatListNumbered"
import ContentCopyIcon from "@mui/icons-material/ContentCopy"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import useAppStore from "../../store/appStore"
import useJobOutput from "../../hooks/useJobOutput"

// Auto-chapters tool. Whisper-transcribe → AI clusters into chapters →
// emit a `MM:SS Title` text file. Inline preview shows the chapter list
// with a copy-to-clipboard button so users can paste straight into a
// YouTube description.

function formatTimestamp(seconds) {
  const s = Math.max(0, Math.floor(seconds))
  const mm = Math.floor(s / 60)
  const ss = s % 60
  if (mm >= 60) {
    const hh = Math.floor(mm / 60)
    return `${hh}:${String(mm % 60).padStart(2, "0")}:${String(ss).padStart(2, "0")}`
  }
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`
}

function chaptersToText(chapters) {
  return chapters.map((c) => `${formatTimestamp(c.start_seconds)} ${c.title || "Chapter"}`).join("\n")
}

function ResultPreview({ jobId }) {
  // See useJobOutput: the store never carries a structured `output`, so this
  // chapter list could never render before — the fallback was all anyone saw.
  const { output, loading } = useJobOutput(jobId)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const chapters = output?.chapters || []
  const copy = (s) => navigator.clipboard.writeText(s).then(
    () => showSnackbar?.("Copied", "success"),
    () => showSnackbar?.("Could not copy", "warning"),
  )
  if (chapters.length === 0) {
    return (
      <Alert severity="info" sx={{ fontSize: "0.85rem" }}>
        {loading
          ? "Loading result…"
          : <>Result ready — click <strong>Download</strong> below to get the text file.</>}
      </Alert>
    )
  }
  const plainText = chaptersToText(chapters)
  return (
    <GlassPanel sx={{ p: 1.5, borderRadius: 2 }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, fontSize: "0.65rem", flex: 1 }}>
          {chapters.length} chapters — paste into your YouTube description
        </Typography>
        <Tooltip title="Copy all chapters">
          <IconButton size="small" onClick={() => copy(plainText)}>
            <ContentCopyIcon sx={{ fontSize: "1rem" }} />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{
        fontFamily: "monospace", fontSize: "0.82rem", lineHeight: 1.6,
        whiteSpace: "pre-wrap", color: "text.primary",
      }}>
        {plainText}
      </Box>
    </GlassPanel>
  )
}

export default function ToolAutoChapters() {
  useDocumentTitle("Auto Chapters")
  const [targetCount, setTargetCount] = useState(0)

  return (
    <ToolRunner
      title="Auto Chapters"
      description="AI-generated YouTube chapter markers from your long-form video"
      icon={<FormatListNumberedIcon fontSize="large" />}
      endpoint="/api/tools/auto-chapters"
      processLabel="Generate Chapters"
      downloadLabel="Download .txt"
      fieldBuilder={() => ({ target_count: targetCount })}
      resultPreview={(jobId) => <ResultPreview jobId={jobId} />}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Target chapter count — {targetCount === 0 ? "Auto" : targetCount}
          </Typography>
          <Slider
            value={targetCount}
            min={0} max={20} step={1}
            marks={[
              { value: 0, label: "Auto" },
              { value: 5, label: "5" },
              { value: 10, label: "10" },
              { value: 20, label: "20" },
            ]}
            onChange={(_, v) => setTargetCount(v)}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => v === 0 ? "Auto" : `${v}`}
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            <strong>Auto</strong> picks ~1 chapter per 2.5 minutes (typically 5-12 chapters for a 15-30min video).
            Best for long-form podcast episodes, lectures, and tutorials.
          </Typography>
        </Box>

        <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
          The first chapter is forced to <strong>00:00</strong> — required by YouTube to detect the rest as chapters.
          Output is plain text in <code>MM:SS Title</code> format, ready to paste into a video description.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}
