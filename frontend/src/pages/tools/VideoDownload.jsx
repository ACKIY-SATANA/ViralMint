// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useMemo, useCallback, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import {
  Box, Typography, Stack, Button, TextField, ToggleButton, ToggleButtonGroup,
  FormControlLabel, Checkbox, Select, MenuItem, LinearProgress, Alert,
  Chip, Divider, Tooltip, InputLabel, FormControl,
} from "@mui/material"
import DownloadForOfflineOutlinedIcon from "@mui/icons-material/DownloadForOfflineOutlined"
import VideoLibraryOutlinedIcon from "@mui/icons-material/VideoLibraryOutlined"
import CheckCircleOutlinedIcon from "@mui/icons-material/CheckCircleOutlined"
import WarningAmberIcon from "@mui/icons-material/WarningAmber"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import PageHero from "../../components/PageHero"
import { GlassPanel } from "../../utils/glassFx"

// Why this page is hand-rolled instead of using ToolRunner:
// ToolRunner is built around POST /api/tools/<slug> with an uploaded file and
// a single artifact at /api/tools/download/<job_id>. A download has neither —
// its input is a list of URLs, it posts to /api/downloaded/batch-download,
// runs as job type "download" (not "tool:*"), and its output is N rows in the
// Downloaded library rather than one file. Bending ToolRunner around that
// would have meant special-casing its core contract for one tool.

const MAX_URLS = 20

const QUALITIES = [
  { id: "best", label: "Best" },
  { id: "2160p", label: "4K" },
  { id: "1440p", label: "1440p" },
  { id: "1080p", label: "1080p" },
  { id: "720p", label: "720p" },
  { id: "480p", label: "480p" },
]

const SUB_LANGS = [
  ["en", "English"], ["es", "Spanish"], ["zh", "Chinese"], ["ja", "Japanese"],
  ["ko", "Korean"], ["fr", "French"], ["de", "German"], ["pt", "Portuguese"],
  ["ru", "Russian"], ["ar", "Arabic"], ["hi", "Hindi"],
]

/** Split the textarea into URLs. Forgiving about commas, spaces and blank
 *  lines because people paste from all three. */
function parseUrls(raw) {
  return (raw || "")
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

const isHttpUrl = (u) => /^https?:\/\/\S+$/i.test(u)

export default function VideoDownload() {
  useDocumentTitle("Video Download")
  const navigate = useNavigate()
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const startJob = useAppStore((s) => s.startJob)
  const activeJobs = useAppStore((s) => s.activeJobs)

  const [raw, setRaw] = useState("")
  const [quality, setQuality] = useState("best")
  const [container, setContainer] = useState("mp4")
  const [subtitles, setSubtitles] = useState("off")
  const [subLang, setSubLang] = useState("en")
  const [thumbnail, setThumbnail] = useState(false)
  const [metadata, setMetadata] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  const urls = useMemo(() => parseUrls(raw), [raw])
  const valid = useMemo(() => urls.filter(isHttpUrl), [urls])
  const invalid = useMemo(() => urls.filter((u) => !isHttpUrl(u)), [urls])
  const overCap = valid.length > MAX_URLS
  const submitCount = Math.min(valid.length, MAX_URLS)

  // Job lifecycle. The global store evicts a finished job ~10s after it
  // completes, which would blank the result — so the terminal state is
  // snapshotted here the first time it appears.
  const [jobId, setJobId] = useState(null)
  const [terminal, setTerminal] = useState(null)
  const job = jobId ? activeJobs[jobId] : null
  useEffect(() => {
    if (job && (job.status === "success" || job.status === "failed") && !terminal) {
      setTerminal({ status: job.status, step: job.step })
    }
  }, [job, terminal])

  // Re-attach to a download still running when the user navigates back.
  useEffect(() => {
    if (jobId || terminal) return
    const match = Object.values(activeJobs).find(
      (j) => j.jobType === "download" && j.status === "running",
    )
    if (match) setJobId(match.jobId)
  }, [activeJobs, jobId, terminal])

  const [receipts, setReceipts] = useState(null)
  const [finalStep, setFinalStep] = useState(null)

  const submit = useCallback(async () => {
    if (!submitCount || submitting) return
    setSubmitting(true)
    setTerminal(null)
    setReceipts(null)
    setFinalStep(null)
    try {
      // Quality AND container are ALWAYS sent, including their defaults. The
      // service treats a missing quality as "use the legacy 720p-capped
      // chain" and a missing container as "whatever yt-dlp's merge produces"
      // (often .webm/.mkv) — neither is what the picker SHOWS, so being
      // explicit is what makes both pickers honest rather than no-ops at
      // their default setting. Server-side, a container is a preference:
      // codecs that can't be stream-copied into mp4 come out as mkv, and the
      // receipt below reports the delivered extension.
      const options = { quality, container }
      if (subtitles !== "off") {
        options.subtitles = subtitles
        options.sub_langs = [subLang]
      }
      if (thumbnail) options.thumbnail = true
      if (metadata) options.metadata = true

      const { data } = await http.post("/api/downloaded/batch-download", {
        urls: valid.slice(0, MAX_URLS).map((url) => ({ url })),
        options,
        // The tool was asked for files, not transcripts. Library analyzes on
        // demand; Whisper over every download would be minutes nobody asked
        // for.
        analyze: false,
      })
      setJobId(data.job_id)
      // The batch endpoint doesn't emit job_started, so seed the store here —
      // that's what feeds the progress bar and the global active-jobs banner.
      startJob(data.job_id, "download", "Starting download…")
      // The server echoes the options it ACTUALLY applied after validation.
      // If it dropped everything we asked for, say so rather than letting the
      // user assume the toggles did something.
      if (Object.keys(options).length && !Object.keys(data.options || {}).length) {
        showSnackbar("Downloading with default settings — the options weren't accepted.", "warning")
      }
    } catch (e) {
      showSnackbar(e?.response?.data?.detail || "Couldn't start the download", "error")
      setSubmitting(false)
      return
    }
    setSubmitting(false)
  }, [submitCount, submitting, quality, container, subtitles, subLang, thumbnail,
      metadata, valid, startJob, showSnackbar])

  const jobRunning = job && (job.status === "running" || job.status === "pending") && !terminal
  const busy = submitting || jobRunning
  const done = terminal?.status === "success"
  const failed = terminal?.status === "failed"

  // Per-video delivery receipts. The WS job_complete payload doesn't carry the
  // job's output, so fetch the job row once on success. The runner's
  // output_data.videos says what each download DELIVERED: height, container
  // ext, kept subtitle sidecars, dropped embed extras. That's the honest half
  // of the options story — a quality cap degrades instead of failing, so what
  // you asked for and what you got can legitimately differ, and this is where
  // the difference is shown instead of implied away.
  useEffect(() => {
    if (!done || !jobId) return undefined
    let cancelled = false
    http.get(`/api/jobs/${jobId}`)
      .then(({ data }) => {
        if (cancelled) return
        if (data?.current_step) setFinalStep(data.current_step)
        try {
          const oj = data?.output_json ?? data?.output
          const parsed = typeof oj === "string" ? JSON.parse(oj) : oj
          if (Array.isArray(parsed?.videos) && parsed.videos.length) {
            setReceipts(parsed.videos)
          }
        } catch { /* receipts are a bonus — the success alert stands alone */ }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [done, jobId])

  // What a receipt row says about the quality request. `requested_quality` is
  // echoed by the downloader itself, so its absence means the download never
  // went through the option-aware path.
  const qualityNote = (v) => {
    if (!v.requested_quality) return null
    // Source didn't report a height (direct files, some extractors) — nothing
    // honest to compare the cap against, so say nothing.
    if (v.height == null) return null
    const cap = v.requested_quality !== "best" ? parseInt(v.requested_quality, 10) : null
    if (cap && v.height > cap) {
      return `nothing at ≤${cap}p — downloaded the closest available`
    }
    return null
  }

  return (
    <Box>
      <PageHero
        icon={<DownloadForOfflineOutlinedIcon />}
        title="Video Download"
        subtitle="Paste a link — download from YouTube, TikTok, Bilibili, X and 1,800+ more sites. Runs locally, no watermark."
        backTo="/tools"
      />

      <Box sx={{ maxWidth: 900, mx: "auto", px: { xs: 2, md: 3 }, py: 3 }}>
        <GlassPanel sx={{ p: { xs: 2, md: 3 } }}>
          <Stack spacing={2.5}>
            {/* ── Input ─────────────────────────────────────────────── */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Video links
              </Typography>
              <TextField
                fullWidth
                multiline
                minRows={3}
                maxRows={10}
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
                placeholder={"https://www.youtube.com/watch?v=...\nhttps://www.tiktok.com/@user/video/...\n\nOne per line — up to 20 at once."}
                disabled={busy}
              />
              <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: "wrap", rowGap: 1 }}>
                {valid.length > 0 && (
                  <Chip size="small" color="primary" variant="outlined"
                        label={`${valid.length} link${valid.length > 1 ? "s" : ""}`} />
                )}
                {invalid.length > 0 && (
                  <Chip size="small" color="warning" variant="outlined"
                        icon={<WarningAmberIcon />}
                        label={`${invalid.length} not a valid http(s) link — will be skipped`} />
                )}
                {overCap && (
                  <Chip size="small" color="warning" variant="outlined"
                        label={`Only the first ${MAX_URLS} will download`} />
                )}
              </Stack>
            </Box>

            <Divider />

            {/* ── Quality ───────────────────────────────────────────── */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Quality</Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={quality}
                onChange={(_, v) => v && setQuality(v)}
                disabled={busy}
                sx={{
                  flexWrap: "wrap",
                  gap: 0.5,
                  "& .MuiToggleButton-root": {
                    borderRadius: "8px !important",
                    border: "1px solid",
                    borderColor: "divider",
                    px: 1.5,
                    // MUI uppercases by default, which renders "1440P" / "720P".
                    // Resolution labels are conventionally lowercase-p.
                    textTransform: "none",
                  },
                }}
              >
                {QUALITIES.map((q) => (
                  <ToggleButton key={q.id} value={q.id}>{q.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                If the source has nothing at that size you'll get the closest available — the
                download won't fail. The result shows what actually came down.
              </Typography>
            </Box>

            {/* ── Extras ────────────────────────────────────────────── */}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              {/* Each Select carries an explicit labelId. Without it MUI
                  renders a floating label that is only visually associated,
                  so a screen reader (and anything driving the page by
                  accessible name) sees three unlabelled comboboxes. */}
              <FormControl size="small" sx={{ minWidth: 150 }} disabled={busy}>
                <InputLabel id="vd-subs-label">Subtitles</InputLabel>
                <Select labelId="vd-subs-label" label="Subtitles" value={subtitles}
                        onChange={(e) => setSubtitles(e.target.value)}>
                  <MenuItem value="off">None</MenuItem>
                  <MenuItem value="embed">Embed in video</MenuItem>
                  <MenuItem value="file">Separate .srt file</MenuItem>
                </Select>
              </FormControl>

              <FormControl size="small" sx={{ minWidth: 140 }}
                           disabled={busy || subtitles === "off"}>
                <InputLabel id="vd-lang-label">Language</InputLabel>
                <Select labelId="vd-lang-label" label="Language" value={subLang}
                        onChange={(e) => setSubLang(e.target.value)}>
                  {SUB_LANGS.map(([code, name]) => (
                    <MenuItem key={code} value={code}>{name}</MenuItem>
                  ))}
                </Select>
              </FormControl>

              <FormControl size="small" sx={{ minWidth: 120 }} disabled={busy}>
                <InputLabel id="vd-format-label">Format</InputLabel>
                <Select labelId="vd-format-label" label="Format" value={container}
                        onChange={(e) => setContainer(e.target.value)}>
                  <MenuItem value="mp4">MP4</MenuItem>
                  <MenuItem value="mkv">MKV</MenuItem>
                </Select>
              </FormControl>
            </Stack>

            {subtitles === "embed" && container === "mp4" && (
              <Alert severity="info" sx={{ py: 0.5 }}>
                MP4 only reliably carries one subtitle track. Switch to MKV if you want several.
              </Alert>
            )}

            <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap" }}>
              <FormControlLabel
                control={<Checkbox size="small" checked={thumbnail} disabled={busy}
                                   onChange={(e) => setThumbnail(e.target.checked)} />}
                label={<Typography variant="body2">Embed cover art</Typography>}
              />
              <Tooltip title="Adds title/artist tags and chapter markers to the file">
                <FormControlLabel
                  control={<Checkbox size="small" checked={metadata} disabled={busy}
                                     onChange={(e) => setMetadata(e.target.checked)} />}
                  label={<Typography variant="body2">Metadata + chapters</Typography>}
                />
              </Tooltip>
            </Stack>

            <Divider />

            {/* ── Submit ────────────────────────────────────────────── */}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
              <Button
                variant="contained"
                size="large"
                disabled={!submitCount || busy}
                onClick={submit}
                startIcon={<DownloadForOfflineOutlinedIcon />}
              >
                {busy
                  ? "Downloading…"
                  : submitCount > 1
                    ? `Download ${submitCount} videos`
                    : "Download"}
              </Button>
            </Stack>

            {busy && (
              <Box>
                <LinearProgress
                  variant={job?.percent ? "determinate" : "indeterminate"}
                  value={job?.percent || 0}
                />
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
                  {job?.step || "Starting…"}
                </Typography>
              </Box>
            )}

            {done && (
              <Alert
                severity="success"
                icon={<CheckCircleOutlinedIcon />}
                action={
                  <Button size="small" onClick={() => navigate("/videos")}>
                    Open Library
                  </Button>
                }
              >
                {finalStep || terminal?.step || "Download complete."}
              </Alert>
            )}

            {/* Delivery receipts — what each download actually produced.
                A quality cap degrades instead of failing and a container is
                a preference, so request and delivery can differ; showing the
                difference here is what keeps the pickers honest. */}
            {done && receipts && (
              <Stack spacing={1}>
                {receipts.map((v) => (
                  <Stack key={v.id} direction="row" spacing={1} alignItems="center"
                         sx={{ flexWrap: "wrap", rowGap: 0.5 }}>
                    <Typography variant="body2" noWrap sx={{ maxWidth: 340 }}>
                      {v.title || v.id}
                    </Typography>
                    {v.height != null && (
                      <Chip size="small" variant="outlined" label={`${v.height}p`} />
                    )}
                    {v.ext && (
                      <Chip size="small" variant="outlined" label={v.ext.toUpperCase()} />
                    )}
                    {qualityNote(v) && (
                      <Typography variant="caption" color="text.secondary">
                        {qualityNote(v)}
                      </Typography>
                    )}
                    {v.extras_dropped && (
                      <Typography variant="caption" color="warning.main">
                        embed extras (cover art / metadata) couldn't be added to this file
                      </Typography>
                    )}
                    {(v.subtitle_files || []).length > 0 && (
                      <Typography variant="caption" color="text.secondary">
                        subtitles saved next to the video: {v.subtitle_files.join(", ")}
                      </Typography>
                    )}
                  </Stack>
                ))}
              </Stack>
            )}
            {failed && (
              <Alert severity="error">
                {terminal?.step || "The download failed."}
              </Alert>
            )}
          </Stack>
        </GlassPanel>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 2 }}>
          <Button size="small" variant="outlined" startIcon={<VideoLibraryOutlinedIcon />}
                  onClick={() => navigate("/videos")}>
            Library
          </Button>
        </Stack>
      </Box>
    </Box>
  )
}
