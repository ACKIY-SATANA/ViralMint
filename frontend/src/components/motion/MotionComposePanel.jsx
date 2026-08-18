// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useEffect, useRef } from "react"
import {
  Box, Stack, Typography, TextField, Button, ToggleButton, ToggleButtonGroup,
  LinearProgress, Alert, IconButton, Divider, Chip, Tooltip,
} from "@mui/material"
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesomeOutlined"
import CloseIcon from "@mui/icons-material/CloseOutlined"
import AttachFileIcon from "@mui/icons-material/AttachFileOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const ASPECTS = ["9:16", "16:9", "1:1"]
const DURATIONS = [5, 10, 15, 30]

// Openers for the empty state. Static and local: an endpoint would be a network
// call to answer a question that has the same answer every time.
const STARTERS = [
  "A bold kinetic-typography hook for a video about morning routines",
  "A stat card built around “73% of viewers drop off in 3 seconds”",
  "A launch teaser for an app called Northwind — dark and cinematic",
  "A three-point listicle on why most side projects stall",
]

/**
 * AI Compose — describe a video, the configured model writes the composition
 * into the studio project, and the studio reloads onto it.
 *
 * This deliberately does NOT hold a conversation. Composing rewrites one file,
 * so the useful unit is a brief and a result; the refine box edits what is
 * currently live rather than replaying a thread. Iterating is safe because
 * every composition it replaces is archived rather than overwritten.
 */
export default function MotionComposePanel({ aspect, setAspect, onComposed, onNeedInstall, onClose }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [topic, setTopic] = useState("")
  const [duration, setDuration] = useState(10)
  const [accent, setAccent] = useState("#ffd60a")
  const [job, setJob] = useState(null)        // {status, step, pct, error}
  const [composed, setComposed] = useState(false)
  const [staged, setStaged] = useState([])
  const fileRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  const poll = (id) => {
    const tick = async () => {
      try {
        const { data } = await http.get(`/api/jobs/${id}`)
        setJob({ status: data.status, step: data.current_step,
                 pct: data.progress_pct, error: data.error_message })
        if (["success", "failed", "cancelled"].includes(data.status)) {
          pollRef.current = null
          if (data.status === "success") {
            setComposed(true)
            showSnackbar("Composition ready — the studio is reloading onto it.", "success")
            onComposed?.()
          }
          return
        }
      } catch { /* a blip shouldn't abandon a job that is still running */ }
      pollRef.current = setTimeout(tick, 2000)
    }
    pollRef.current = setTimeout(tick, 1500)
  }

  const compose = async (instruction) => {
    const brief = instruction
      ? { instruction, aspect_ratio: aspect, duration_seconds: duration, accent }
      : { topic: topic.trim(), aspect_ratio: aspect, duration_seconds: duration, accent }
    if (!instruction && !brief.topic) return
    setJob({ status: "pending", step: "Starting…", pct: 0 })
    setComposed(false)
    try {
      const { data } = await http.post("/api/generate/motion/studio/author", brief)
      if (data?.error_code === "hyperframes_not_installed") { onNeedInstall?.(); return }
      poll(data.job_id)
    } catch (e) {
      setJob({ status: "failed", error: e?.response?.data?.detail || e.message })
    }
  }

  const attach = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    const form = new FormData()
    form.append("file", file)
    try {
      const { data } = await http.post("/api/generate/motion/studio/assets", form)
      setStaged((s) => [...s, data])
      showSnackbar(`${file.name} staged — it will be built into the next composition.`, "success")
    } catch (err) {
      showSnackbar(err?.response?.data?.detail || "Could not stage that file", "error")
    }
  }

  const running = !!job && !["success", "failed", "cancelled"].includes(job.status)

  return (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      bgcolor: "background.paper", borderLeft: "1px solid", borderColor: "divider",
    }}>
      <Stack direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <AutoAwesomeIcon sx={{ fontSize: 18, color: "primary.main" }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700, flexGrow: 1 }}>AI Compose</Typography>
        <IconButton size="small" onClick={onClose} aria-label="Close AI Compose">
          <CloseIcon sx={{ fontSize: 18 }} />
        </IconButton>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <Typography variant="caption" sx={{ display: "block", color: "text.secondary", mb: 1.5 }}>
          Describe the video. Your configured AI model writes the composition and the
          studio opens it — then change anything by hand.
        </Typography>

        <TextField
          fullWidth multiline minRows={3} size="small" value={topic}
          onChange={(e) => setTopic(e.target.value)} disabled={running}
          placeholder="e.g. a punchy hook about why most side projects stall"
          sx={{ mb: 1.5 }}
        />

        {!topic && !job && (
          <Stack spacing={0.75} sx={{ mb: 2 }}>
            {STARTERS.map((s) => (
              <Chip key={s} label={s} size="small" variant="outlined"
                onClick={() => setTopic(s)}
                sx={{
                  height: "auto", justifyContent: "flex-start",
                  "& .MuiChip-label": { whiteSpace: "normal", py: 0.6, fontSize: "0.74rem" },
                }} />
            ))}
          </Stack>
        )}

        <Typography variant="caption" sx={{ color: "text.secondary" }}>Format</Typography>
        <ToggleButtonGroup exclusive size="small" value={aspect} disabled={running}
          onChange={(_, v) => v && setAspect(v)} sx={{ display: "flex", mb: 1.5, mt: 0.5 }}>
          {ASPECTS.map((a) => (
            <ToggleButton key={a} value={a} sx={{ flex: 1, textTransform: "none" }}>{a}</ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Typography variant="caption" sx={{ color: "text.secondary" }}>Length</Typography>
        <ToggleButtonGroup exclusive size="small" value={duration} disabled={running}
          onChange={(_, v) => v && setDuration(v)} sx={{ display: "flex", mb: 1.5, mt: 0.5 }}>
          {DURATIONS.map((d) => (
            <ToggleButton key={d} value={d} sx={{ flex: 1, textTransform: "none" }}>{d}s</ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>Accent</Typography>
          <input type="color" value={accent} disabled={running} aria-label="Accent colour"
            onChange={(e) => setAccent(e.target.value)}
            style={{ width: 38, height: 26, border: 0, background: "none", cursor: "pointer" }} />
          <Box sx={{ flexGrow: 1 }} />
          <Tooltip title="Attach an image, video or audio file to build in">
            <span>
              <Button size="small" startIcon={<AttachFileIcon sx={{ fontSize: 16 }} />}
                disabled={running} onClick={() => fileRef.current?.click()}>
                Attach
              </Button>
            </span>
          </Tooltip>
          <input ref={fileRef} type="file" hidden onChange={attach}
            accept="image/*,video/*,audio/*" />
        </Stack>

        {staged.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            {staged.map((a) => (
              <Chip key={a.file} size="small" variant="outlined" label={`${a.file} · ${a.type}`} />
            ))}
          </Stack>
        )}

        <Button fullWidth variant="contained" startIcon={<AutoAwesomeIcon />}
          disabled={running || !topic.trim()} onClick={() => compose(null)}>
          {running ? "Composing…" : "Compose"}
        </Button>

        {running && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {job.step || "Working…"}
            </Typography>
            <LinearProgress variant={job.pct ? "determinate" : "indeterminate"}
              value={job.pct || 0} sx={{ mt: 0.5, height: 6, borderRadius: 3 }} />
          </Box>
        )}

        {job?.status === "failed" && (
          <Alert severity="error" sx={{ mt: 2, fontSize: "0.78rem", whiteSpace: "pre-wrap" }}>
            {job.error || "Compose failed."}
          </Alert>
        )}

        {composed && (
          <>
            <Divider sx={{ my: 2.5 }} />
            <Typography variant="caption" sx={{ display: "block", color: "text.secondary", mb: 1 }}>
              Refine it — this rewrites the composition that is live. The previous one
              is kept under the studio’s Comps.
            </Typography>
            <RefineBox onSubmit={compose} disabled={running} />
          </>
        )}
      </Box>
    </Box>
  )
}

function RefineBox({ onSubmit, disabled }) {
  const [text, setText] = useState("")
  const send = () => {
    if (!text.trim()) return
    onSubmit(text.trim())
    setText("")
  }
  return (
    <Stack spacing={1}>
      <TextField fullWidth multiline minRows={2} size="small" value={text} disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        placeholder="e.g. make the headline bigger and slow the exit" />
      <Button variant="outlined" size="small" disabled={disabled || !text.trim()} onClick={send}>
        Apply change
      </Button>
    </Stack>
  )
}
