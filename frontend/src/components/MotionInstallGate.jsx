// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useEffect, useState } from "react"
import { Box, Button, Stack, Typography } from "@mui/material"
import { useNavigate } from "react-router-dom"
import MovieFilterIcon from "@mui/icons-material/MovieFilterOutlined"
import UpgradeIcon from "@mui/icons-material/UpgradeOutlined"
import { GlassCard } from "../utils/glassFx"
import http from "../api/http"

/**
 * "Motion Graphics needs setting up" — one consistent card for every surface
 * that needs the engine, rendered in place of the page content.
 *
 * It fetches the plugin status itself, because the parent only knows
 * `installed === false` and that collapses two quite different situations:
 *   - nothing installed  → a one-time download
 *   - a stale engine     → a quick in-place update that reuses what's on disk
 * Telling someone to re-download hundreds of megabytes for what is really a
 * package bump is the kind of thing that makes people not bother.
 *
 * Pass `onBack` on sub-pages that have somewhere to return to; omit it on
 * top-level sidebar pages, where there is nothing to go back to.
 */
export default function MotionInstallGate({ onBack }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState(null)   // null while checking

  useEffect(() => {
    let cancelled = false
    http.get("/api/settings/motion-graphics/status")
      .then((r) => { if (!cancelled) setStatus(r.data || {}) })
      .catch(() => { if (!cancelled) setStatus({}) })
    return () => { cancelled = true }
  }, [])

  const updating = !!status?.update_available
  const from = status?.installed_version
  const to = status?.hyperframes_version
  const mb = status?.approx_download_mb || 350

  return (
    <Box sx={{ height: "100%", display: "grid", placeItems: "center", p: 3 }}>
      <GlassCard sx={{ p: 4, textAlign: "center", maxWidth: 540 }}>
        {updating
          ? <UpgradeIcon sx={{ fontSize: 44, color: "primary.main", mb: 1 }} />
          : <MovieFilterIcon sx={{ fontSize: 44, color: "primary.main", mb: 1 }} />}
        {/* An h5 element: when this gate renders it REPLACES the page, so this
            line is the page title and should sit at the level every other page
            title does — not tie with the app shell's own brand heading. */}
        <Typography variant="h6" component="h5" sx={{ fontWeight: 700, mb: 1 }}>
          {updating ? "Motion Graphics needs an update" : "Motion Graphics isn’t installed"}
        </Typography>
        <Typography variant="body2" sx={{ color: "text.secondary", mb: 3 }}>
          {updating ? (
            <>
              A newer engine ships with this version
              {from && to ? ` (${from} → ${to})` : ""}. The update is quick — it reuses
              the Node runtime already on disk, so it is not a full re-download.
            </>
          ) : (
            <>
              Render designed motion video — kinetic type, stat cards, lower thirds —
              entirely on this machine. It needs a one-time ~{mb}&nbsp;MB setup, which
              installs into your data folder and can be removed again at any time.
            </>
          )}
        </Typography>
        <Stack direction="row" spacing={1.5} justifyContent="center">
          {onBack && <Button variant="outlined" onClick={onBack}>Back</Button>}
          <Button variant="contained" onClick={() => navigate("/settings#motion-graphics")}>
            {updating ? "Update from Settings" : "Install from Settings"}
          </Button>
        </Stack>
      </GlassCard>
    </Box>
  )
}
