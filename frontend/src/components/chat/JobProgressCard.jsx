import { Box, Typography, LinearProgress, Stack } from "@mui/material"
import useAppStore from "../../store/appStore"

const JOB_COLORS = {
  scout: "primary", download: "info", generate: "warning", upload: "success",
}

const JOB_LABELS = {
  scout: "Scouting", download: "Downloading", generate: "Generating", upload: "Uploading",
}

// "tool:merge_clips" → "Merge clips". The raw job type read as a debug string
// on the progress card ("tool:gif complete"); named types keep their
// hand-tuned label above.
function humanizeJobType(jobType) {
  const t = String(jobType || "")
  if (JOB_LABELS[t]) return JOB_LABELS[t]
  const bare = t.replace(/^tool:/, "").replace(/[-_]+/g, " ").trim()
  if (!bare) return "Job"
  return bare.charAt(0).toUpperCase() + bare.slice(1)
}

export default function JobProgressCard({ jobId, jobType, message }) {
  const job = useAppStore((s) => s.activeJobs[jobId])

  // Job removed from store (auto-cleaned after completion) — hide the card
  if (!job) return null

  const status = job.status || "running"
  const percent = job.percent || 0
  const step = job.step || message || ""

  const color = JOB_COLORS[jobType] || "primary"
  const label = humanizeJobType(jobType)
  const isRunning = status === "running"
  const isSuccess = status === "success"
  const isFailed = status === "failed"

  return (
    <Box sx={{ mb: 0.5 }}>
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 0.25 }}>
        <Typography variant="caption" sx={{
          fontWeight: 600, fontSize: "0.75rem",
          color: isSuccess ? "success.main" : isFailed ? "error.main" : "text.secondary",
        }}>
          {isSuccess ? `${label} complete` : isFailed ? `${label} failed` : `${label}...`}
        </Typography>
        {isRunning && percent > 0 && (
          <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.disabled" }}>
            {Math.round(percent)}%
          </Typography>
        )}
      </Stack>

      <LinearProgress
        variant={isRunning ? (percent > 0 ? "determinate" : "indeterminate") : "determinate"}
        value={isRunning ? percent : 100}
        color={isSuccess ? "success" : isFailed ? "error" : color}
        sx={{ borderRadius: 1, height: 3 }}
      />

      {step && !isSuccess && (
        <Typography variant="caption" sx={{ color: "text.disabled", fontSize: "0.65rem", mt: 0.25, display: "block" }}>
          {step}
        </Typography>
      )}
    </Box>
  )
}
