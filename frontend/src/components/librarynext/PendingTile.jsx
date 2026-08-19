// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * PendingTile — a render in flight, shown in the grid where its output will land.
 *
 * This is the half of "Jobs" that belongs in the Library: you wait where the
 * thing will appear instead of navigating to a log. The other half — history,
 * failures, retries — lives in the app-wide Activity panel.
 *
 * RUNNING only, deliberately. Failures were tiles here at first, and on a real
 * library the result was a wall of red: seventeen "didn't finish" cards ahead of
 * any content, because a failed job is sticky where a running one is transient.
 * A failure is a batch problem ("3 renders didn't finish"), so the page shows it
 * once as a strip above the grid and keeps the detail in Activity.
 *
 * The footprint and footer grammar match AssetTile exactly, so a render in
 * flight reads as a tile still developing rather than a different object.
 */
import { Box, Typography, Stack, LinearProgress, Button, alpha, useTheme } from "@mui/material"
import { ORIGINS, originColor } from "./assetModel"

export default function PendingTile({ job, dense = false, onCancel }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const oc = originColor(job.origin, isDark)
  const frameH = dense ? 112 : 148

  return (
    <Box sx={{
      position: "relative", borderRadius: "14px", overflow: "hidden",
      bgcolor: "background.paper", border: 1, borderColor: alpha(oc, 0.35),
      boxShadow: theme.customShadows?.sm,
    }}>
      <Box sx={{
        position: "absolute", left: 0, top: 0, bottom: 0, width: 3,
        bgcolor: oc, opacity: 0.9, zIndex: 2,
      }} />

      <Box sx={{
        height: frameH, position: "relative", display: "flex",
        alignItems: "center", justifyContent: "center",
        background: `linear-gradient(135deg, ${alpha(oc, isDark ? 0.2 : 0.12)}, ${alpha(oc, 0.03)})`,
      }}>
        <Stack alignItems="center" spacing={0.75} sx={{ width: "72%" }}>
          <Typography sx={{
            fontSize: "0.72rem", fontWeight: 700, color: oc,
            fontVariantNumeric: "tabular-nums",
          }}>
            {job.percent ? `${job.percent}%` : "Starting…"}
          </Typography>
          <LinearProgress variant="determinate" value={job.percent || 0}
            sx={{
              width: "100%", height: 4, borderRadius: 2,
              bgcolor: alpha(oc, 0.18),
              "& .MuiLinearProgress-bar": { bgcolor: oc },
            }} />
          <Typography noWrap sx={{ fontSize: "0.66rem", color: "text.secondary", maxWidth: "100%" }}>
            {job.step}
          </Typography>
        </Stack>

        <Box sx={{
          position: "absolute", left: 0, right: 0, bottom: 0, px: 0.75, py: 0.5,
          display: "flex", justifyContent: "flex-end",
        }}>
          <Button size="small" onClick={onCancel}
            sx={{
              minWidth: 0, px: 0.9, py: 0.1, fontSize: "0.66rem", color: "#fff",
              bgcolor: "rgba(0,0,0,0.58)",
              "&:hover": { bgcolor: "rgba(0,0,0,0.72)" },
            }}>
            Cancel
          </Button>
        </Box>
      </Box>

      <Box sx={{ pl: 1.5, pr: 1.25, pt: 0.9, pb: 1 }}>
        <Typography sx={{
          fontSize: "0.8rem", fontWeight: 600, lineHeight: 1.35, letterSpacing: "-0.01em",
          display: "-webkit-box", WebkitLineClamp: dense ? 1 : 2, WebkitBoxOrient: "vertical",
          overflow: "hidden", minHeight: dense ? undefined : 34,
        }}>
          {job.label}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.4 }}>
          <Box sx={{
            width: 6, height: 6, borderRadius: "50%", bgcolor: oc, flexShrink: 0,
            animation: "vm-pending-pulse 1.6s ease-in-out infinite",
            "@keyframes vm-pending-pulse": { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.3 } },
            "@media (prefers-reduced-motion: reduce)": { animation: "none" },
          }} />
          <Typography sx={{
            fontSize: "0.62rem", fontWeight: 700, letterSpacing: "0.07em",
            textTransform: "uppercase", color: oc,
          }}>
            {ORIGINS[job.origin]?.label}
          </Typography>
          <Typography sx={{ fontSize: "0.68rem", color: "text.secondary" }}>·</Typography>
          <Typography noWrap sx={{ fontSize: "0.68rem", color: "text.secondary" }}>
            {job.producer}
          </Typography>
        </Stack>
      </Box>
    </Box>
  )
}
