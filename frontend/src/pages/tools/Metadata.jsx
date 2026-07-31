import { useState } from "react"
import {
  Box, Typography, Stack, TextField, Alert, ToggleButton, ToggleButtonGroup,
  IconButton, Tooltip, Chip,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import LabelOutlinedIcon from "@mui/icons-material/LabelOutlined"
import ContentCopyIcon from "@mui/icons-material/ContentCopy"
import ToolRunner from "../../components/tools/ToolRunner"
import PromptField from "../../components/tools/PromptField"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import useAppStore from "../../store/appStore"
import useJobOutput from "../../hooks/useJobOutput"

// Metadata generator — three input modes (paste text / pick a topic /
// upload a video). The result is parsed JSON returned in the job's
// extra_output, so we can render it inline without a separate fetch.

// Blank-page topic starters — click to fill, then generate (or "✨ Enhance"
// the script field first when in paste mode).
const TOPIC_STARTERS = [
  "5 beginner mistakes in [your niche]",
  "How I grew my channel to 10k in 90 days",
  "The truth about [trending topic] nobody tells you",
  "3 tools that 10x'd my workflow",
]

const MODES = [
  { value: "text", label: "Paste script", description: "Fastest — no transcription" },
  { value: "topic", label: "From topic", description: "Generate from a bare topic string" },
  { value: "file", label: "Upload video", description: "Whisper transcribes first" },
]

function ResultPreview({ jobId }) {
  // The job's own output_json, via the shared hook: the store never carries a
  // structured `output`, so reading it here meant this preview could never
  // render — the "click Download instead" fallback was all anyone ever saw.
  // Going through the hook also makes it survive a reload.
  const { output, loading } = useJobOutput(jobId)
  const metadata = output?.metadata
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const copy = (s) => {
    if (!s) return
    navigator.clipboard.writeText(s).then(
      () => showSnackbar?.("Copied", "success"),
      () => showSnackbar?.("Could not copy", "warning"),
    )
  }
  if (!metadata) {
    return (
      <Alert severity="info" sx={{ fontSize: "0.85rem" }}>
        {loading
          ? "Loading result…"
          : <>Result ready — click <strong>Download</strong> below to get the JSON file.</>}
      </Alert>
    )
  }
  const tags = metadata.youtube_tags || []
  return (
    <Stack spacing={1.5}>
      <Field label="YouTube title" value={metadata.youtube_title} onCopy={copy} />
      <Field label="YouTube description" value={metadata.youtube_description} onCopy={copy} multiline />
      {tags.length > 0 && (
        <Field
          label="YouTube tags"
          value={tags.join(", ")}
          onCopy={copy}
        />
      )}
      <Field label="TikTok caption" value={metadata.tiktok_title} onCopy={copy} multiline />
    </Stack>
  )
}

function Field({ label, value, onCopy, multiline }) {
  if (!value) return null
  return (
    <GlassPanel sx={{ p: 1.25, borderRadius: 1.5, display: "flex", alignItems: "flex-start", gap: 1 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, fontSize: "0.65rem" }}>
          {label}
        </Typography>
        <Typography variant="body2" sx={{
          mt: 0.25, fontSize: "0.88rem", lineHeight: 1.45, wordBreak: "break-word",
          whiteSpace: multiline ? "pre-wrap" : "normal",
        }}>
          {value}
        </Typography>
      </Box>
      <Tooltip title="Copy">
        <IconButton size="small" onClick={() => onCopy(value)} sx={{ flexShrink: 0 }}>
          <ContentCopyIcon sx={{ fontSize: "0.95rem" }} />
        </IconButton>
      </Tooltip>
    </GlassPanel>
  )
}

export default function ToolMetadata() {
  useDocumentTitle("Metadata Generator")
  const [mode, setMode] = useState("text")
  const [text, setText] = useState("")
  const [topic, setTopic] = useState("")

  const canSubmit = mode === "text" ? !!text.trim()
    : mode === "topic" ? !!topic.trim()
    : true  // file mode — handled by ToolRunner's file presence check

  return (
    <ToolRunner
      title="Title / Description / Tags"
      description="Generate SEO metadata for a video, transcript, or bare topic"
      icon={<LabelOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/metadata"
      processLabel="Generate"
      downloadLabel="Download JSON"
      hideFileInput={mode !== "file"}
      canSubmit={canSubmit}
      disabledReason={mode === "topic" ? "Enter a topic" : "Paste your script or transcript"}
      fieldBuilder={() => ({
        text: mode === "text" ? text : "",
        topic: mode === "topic" ? topic : "",
      })}
      resultPreview={(jobId) => <ResultPreview jobId={jobId} />}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Input mode
          </Typography>
          <ToggleButtonGroup
            value={mode}
            exclusive
            onChange={(_, v) => v && setMode(v)}
            size="small"
            sx={{ flexWrap: "wrap" }}
          >
            {MODES.map((m) => (
              <ToggleButton key={m.value} value={m.value} sx={{ textTransform: "none", flexDirection: "column", py: 1, px: 2 }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{m.label}</Typography>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>{m.description}</Typography>
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {mode === "text" && (
          <PromptField
            minRows={6} maxRows={18}
            autoFocus
            enhanceKind="metadata"
            label="Script / transcript"
            placeholder="Paste a script, transcript, or rough outline. The AI will turn it into platform metadata."
            value={text}
            onChange={(e) => setText(e.target.value)}
            slotProps={{ htmlInput: { maxLength: 20000 } }}
            helperText={`${text.length} / 20,000 chars`}
          />
        )}

        {mode === "topic" && (
          <Stack spacing={1}>
            {!topic.trim() && (
              <Stack direction="row" useFlexGap flexWrap="wrap" sx={{ gap: 0.75 }}>
                {TOPIC_STARTERS.map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    size="small"
                    onClick={() => setTopic(t)}
                    sx={{ fontSize: "0.74rem", height: 26 }}
                  />
                ))}
              </Stack>
            )}
            <TextField
              fullWidth
              autoFocus
              label="Topic"
              placeholder='e.g. "AI startup ideas for solo founders in 2026"'
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              slotProps={{ htmlInput: { maxLength: 500 } }}
            />
          </Stack>
        )}

        {mode === "file" && (
          <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
            Upload a video below. We'll transcribe it with Whisper, then generate metadata from the transcript.
          </Alert>
        )}
      </Stack>
    </ToolRunner>
  )
}
