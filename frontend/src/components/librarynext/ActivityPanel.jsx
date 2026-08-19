/**
 * ActivityPanel — the app-wide home for jobs.
 *
 * Jobs are not library contents: a render is an EVENT, not a file, and it can
 * be started from Smart Video, a tool page, the Motion studio or a chat turn.
 * Filing that log under Library only made it reachable from one page, and only
 * after the fact.
 *
 * So it becomes an app-wide panel, opened from the sidebar's running-jobs pill
 * (which already exists) from any route, with three sections in priority order:
 *
 *   Needs attention — failed, with the error and a Retry
 *   Running         — live progress, cancellable
 *   Recent          — finished, with a link to what it produced
 *
 * Library keeps only the half a creator actually wants there: a running job
 * shows up as a placeholder TILE in the grid, which becomes the real tile when
 * it lands. You wait where the thing will appear, not in a log.
 */
import { useState } from "react"
import { Box, Typography, Stack, Drawer, IconButton, Button, LinearProgress, Tooltip, alpha, useTheme } from "@mui/material"
import CloseRoundedIcon from "@mui/icons-material/CloseRounded"
import ReplayRoundedIcon from "@mui/icons-material/ReplayRounded"
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded"
import CheckCircleOutlineRoundedIcon from "@mui/icons-material/CheckCircleOutlineRounded"
import RemoveCircleOutlineRoundedIcon from "@mui/icons-material/RemoveCircleOutlineRounded"
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded"
import NorthEastRoundedIcon from "@mui/icons-material/NorthEastRounded"
import { fmtDay } from "./assetModel"
import { formatDuration } from "../../utils/format"

// Elapsed wall-clock between a job's start and end — "2m 14s" / "45s". Both
// timestamps come from the same source, so a naive-UTC pair still subtracts
// correctly. Null (not "0s") when either end is missing — the line drops the
// badge rather than showing a zero.
function elapsed(job) {
  if (!job.started || !job.completed) return null
  const ms = new Date(job.completed) - new Date(job.started)
  if (ms < 0 || !Number.isFinite(ms)) return null
  return formatDuration(ms / 1000)
}

// Per-section render caps. The panel reads the whole jobs table, and a busy
// install had 2,260 rows; rendering all of them would make opening the panel the
// slowest thing in the app. The DB side is bounded separately by
// services/job_retention.py — this is just what one glance needs.
const MAX_PER_SECTION = { failed: 20, running: 20, done: 15 }

export default function ActivityPanel({
  open, onClose, jobs, total = 0, onOpenResult, onCancel, onDelete, onClearSection, onToast,
}) {
  const theme = useTheme()
  // "Show all" per section — the caps keep the first paint cheap, but the old
  // Jobs tab could page through everything, so a cap must be expandable, not
  // a wall.
  const [showAll, setShowAll] = useState({})
  const allFailed = jobs.filter((j) => j.state === "failed")
  const allRunning = jobs.filter((j) => j.state === "running")
  // Cancelled rides with the finished work: a deliberate cancel is terminal,
  // not something that "needs attention".
  const allDone = jobs.filter((j) => j.state === "done" || j.state === "cancelled")
  const failed = showAll.failed ? allFailed : allFailed.slice(0, MAX_PER_SECTION.failed)
  const running = showAll.running ? allRunning : allRunning.slice(0, MAX_PER_SECTION.running)
  const done = showAll.done ? allDone : allDone.slice(0, MAX_PER_SECTION.done)

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 420 }, borderLeft: 1, borderColor: "divider" } } }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{
        px: 2, py: 1.75, borderBottom: 1, borderColor: "divider", flexShrink: 0,
      }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ fontSize: "1rem", fontWeight: 700, letterSpacing: "-0.02em" }}>Activity</Typography>
          <Typography sx={{ fontSize: "0.72rem", color: "text.secondary" }}>
            {allRunning.length} running · {allFailed.length} failed · {allDone.length} finished
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <CloseRoundedIcon fontSize="small" />
        </IconButton>
      </Stack>

      <Box sx={{ flex: 1, overflow: "auto", px: 2, py: 2 }}>
        {failed.length > 0 && (
          <Section label="Needs attention" tone={theme.palette.error.main}
            more={allFailed.length - failed.length}
            onShowAll={() => setShowAll((s) => ({ ...s, failed: true }))}
            onClear={() => onClearSection?.(allFailed.map((j) => j.id), "failed")}
            clearLabel={`Clear ${allFailed.length}`}>
            {failed.map((j) => (
              <JobRow key={j.id} job={j} onToast={onToast} onCancel={onCancel} onDelete={onDelete} />
            ))}
          </Section>
        )}
        {/* No clear-all for running work: cancelling a batch of renders in one
            click is not something to make easy. */}
        {running.length > 0 && (
          <Section label="Running" more={allRunning.length - running.length}
            onShowAll={() => setShowAll((s) => ({ ...s, running: true }))}>
            {running.map((j) => (
              <JobRow key={j.id} job={j} onToast={onToast} onCancel={onCancel} onDelete={onDelete} />
            ))}
          </Section>
        )}
        {done.length > 0 && (
          <Section label="Recent" more={allDone.length - done.length}
            onShowAll={() => setShowAll((s) => ({ ...s, done: true }))}
            onClear={() => onClearSection?.(allDone.map((j) => j.id), "finished")}
            clearLabel={`Clear ${allDone.length}`}>
            {done.map((j) => (
              <JobRow key={j.id} job={j} onToast={onToast} onOpenResult={onOpenResult}
                onDelete={onDelete} />
            ))}
          </Section>
        )}
        {jobs.length === 0 && (
          <Typography sx={{ fontSize: "0.84rem", color: "text.secondary" }}>
            Nothing has run yet. Renders you start anywhere in the app show up here.
          </Typography>
        )}
        {/* The store holds one fetch window, not the whole table — say so, or
            the header counts above read as totals they are not. */}
        {total > jobs.length && (
          <Typography sx={{ fontSize: "0.72rem", color: "text.disabled" }}>
            Showing the latest {jobs.length} of {total} runs.
          </Typography>
        )}
      </Box>
    </Drawer>
  )
}

function Section({ label, tone, more = 0, onShowAll, onClear, clearLabel, children }) {
  return (
    <Box sx={{ mb: 2.5 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.9 }}>
        <Typography sx={{
          fontSize: "0.62rem", fontWeight: 700, letterSpacing: "0.09em",
          textTransform: "uppercase", color: tone || "text.disabled", flex: 1,
        }}>
          {label}
        </Typography>
        {onClear && (
          <Typography component="span" onClick={onClear}
            sx={{
              fontSize: "0.68rem", fontWeight: 600, cursor: "pointer",
              color: "text.disabled", "&:hover": { color: tone || "text.primary" },
            }}>
            {clearLabel}
          </Typography>
        )}
      </Stack>
      <Stack spacing={0.9}>{children}</Stack>
      {more > 0 && (
        // Never silently truncate — and never dead-end either: the rest is one
        // click away, like the old Jobs tab's pagination.
        <Typography onClick={onShowAll} sx={{
          mt: 0.9, fontSize: "0.72rem", fontWeight: 600,
          color: "text.disabled", cursor: onShowAll ? "pointer" : "default",
          "&:hover": onShowAll ? { color: "text.primary" } : undefined,
        }}>
          Show {more} more
        </Typography>
      )}
    </Box>
  )
}

function JobRow({ job, onToast, onOpenResult, onCancel, onDelete }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const tone = job.state === "failed" ? theme.palette.error.main
    : job.state === "running" ? theme.palette.primary.main
    : job.state === "cancelled" ? theme.palette.text.disabled
    : theme.palette.success.main
  const Icon = job.state === "failed" ? ErrorOutlineRoundedIcon
    : job.state === "cancelled" ? RemoveCircleOutlineRoundedIcon
    : CheckCircleOutlineRoundedIcon

  return (
    <Box data-testid={`job-row-${job.id}`} sx={{
      p: 1.25, borderRadius: "12px", border: 1,
      borderColor: job.state === "failed" ? alpha(tone, 0.4) : "divider",
      bgcolor: job.state === "failed" ? alpha(tone, isDark ? 0.1 : 0.05) : "background.paper",
    }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        {job.state !== "running" && <Icon sx={{ fontSize: 16, color: tone, flexShrink: 0 }} />}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography noWrap sx={{ fontSize: "0.8rem", fontWeight: 600 }}>{job.label}</Typography>
          <Typography sx={{ fontSize: "0.68rem", color: "text.secondary" }}>
            {job.where} · {fmtDay(job.at)}
            {job.state === "running" && job.percent != null && ` · ${job.percent}%`}
            {job.state === "done" && elapsed(job) && ` · done in ${elapsed(job)}`}
            {job.state === "cancelled" && " · Cancelled"}
          </Typography>
        </Box>
        {job.state === "running" && (
          <Button size="small" sx={{ minWidth: 0, fontSize: "0.7rem" }}
            onClick={() => onCancel?.(job.id)}>
            Cancel
          </Button>
        )}
        {job.state === "failed" && (
          <Button size="small" startIcon={<ReplayRoundedIcon sx={{ fontSize: 15 }} />}
            sx={{ fontSize: "0.7rem" }}
            onClick={() => onToast?.("Open the tool that started it to run it again")}>
            Retry
          </Button>
        )}
        {job.state === "done" && job.result_key && (
          <Tooltip title="Open what it made">
            <IconButton size="small" aria-label="Open result"
              onClick={() => onOpenResult?.(job.result_key)}>
              <NorthEastRoundedIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </Tooltip>
        )}
        {/* Every state can be removed from the log — the old Jobs tab allowed it
            and losing that was a regression. For a RUNNING job this cancels first
            and then removes the row (the endpoint cancels a live job rather than
            deleting it, by design). */}
        <Tooltip title={job.state === "running" ? "Cancel and remove" : "Remove from the log"}>
          <IconButton size="small" aria-label="Remove job"
            onClick={() => onDelete?.(job)}>
            <DeleteOutlineRoundedIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Stack>
      {job.state === "running" && (
        <>
          <LinearProgress variant="determinate" value={job.percent || 0}
            sx={{ mt: 1, height: 4, borderRadius: 2 }} />
          {/* What it's DOING right now — the old Jobs tab showed current_step
              and losing it turned a 10-minute render into a mute bar. */}
          {job.step && (
            <Typography noWrap sx={{ mt: 0.5, fontSize: "0.68rem", color: "text.secondary" }}>
              {job.step}
            </Typography>
          )}
        </>
      )}
      {job.state === "failed" && job.error && (
        <ExpandableError message={job.error} />
      )}
    </Box>
  )
}

// Failed jobs used to truncate the error with no way to read the rest (and an
// unclamped one lets a traceback swallow the panel). Click to expand.
function ExpandableError({ message }) {
  const [open, setOpen] = useState(false)
  return (
    <Typography
      onClick={(e) => { e.stopPropagation(); setOpen((v) => !v) }}
      title={open ? "" : "Click to expand"}
      sx={{
        mt: 0.75, fontSize: "0.72rem", color: "error.main", cursor: "pointer",
        ...(open
          ? { whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace" }
          : {
              display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }),
      }}>
      {message}
    </Typography>
  )
}
