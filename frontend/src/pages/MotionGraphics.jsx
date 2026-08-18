// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useEffect, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import {
  Box, Stack, Typography, Button, CircularProgress, useTheme,
} from "@mui/material"
import MovieFilterIcon from "@mui/icons-material/MovieFilterOutlined"
import VideoLibraryIcon from "@mui/icons-material/VideoLibraryOutlined"
import PageHero from "../components/PageHero"
import MotionInstallGate from "../components/MotionInstallGate"
import http from "../api/http"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"

/**
 * Motion Graphics — the embedded HyperFrames studio.
 *
 * The studio itself is the engine's: timeline, preview, asset import, Export.
 * This page is the surround. It starts the preview server, embeds it, keeps it
 * themed to match the app, and quietly pulls the studio's MP4 exports into the
 * Library so a motion piece ends up wherever everything else the app makes
 * ends up.
 *
 * The iframe is same-host on purpose. The studio SPA keeps state in browser
 * storage, and serving it from a different origin would hand it a fresh empty
 * store on every visit.
 */
export default function MotionGraphics() {
  useDocumentTitle("Motion Graphics")
  const theme = useTheme()
  const navigate = useNavigate()
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const setMotionInstalled = useAppStore((s) => s.setMotionInstalled)
  const mode = theme.palette.mode   // the studio is re-skinned to follow the app

  const [installed, setInstalled] = useState(null)   // null = still checking
  const [studioUrl, setStudioUrl] = useState(null)
  const [starting, setStarting] = useState(true)
  const [startError, setStartError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setStarting(true)
    setStartError(null)
    http.post(`/api/generate/motion/studio/start?mode=${mode}`).then((r) => {
      if (cancelled) return
      if (r.data?.error_code === "hyperframes_not_installed") {
        setInstalled(false)
        setMotionInstalled(false)
        setStarting(false)
        return
      }
      setInstalled(true)
      setMotionInstalled(true)
      // Follow the app's own hostname rather than hardcoding localhost, so the
      // studio stays reachable when the app is opened over the network.
      setStudioUrl(`${window.location.protocol}//${window.location.hostname}:${r.data.port}/`)
      setStarting(false)
      setReloadKey((k) => k + 1)
    }).catch((e) => {
      if (!cancelled) { setStartError(e.message); setStarting(false) }
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Pull the studio's own Exports into the Library while the page is open.
  useEffect(() => {
    if (!studioUrl) return undefined
    let syncing = false   // a first sync of many files can outlast one tick
    const iv = setInterval(async () => {
      if (syncing || document.hidden) return   // never poll a backgrounded tab
      syncing = true
      try {
        const { data } = await http.post("/api/generate/motion/studio/sync-renders")
        if (data?.imported > 0) {
          showSnackbar(
            `${data.imported} video${data.imported > 1 ? "s" : ""} added to your Library`,
            "success")
        }
      } catch { /* the next tick retries; a toast here would be noise */ }
      finally { syncing = false }
    }, 6000)
    return () => clearInterval(iv)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studioUrl])

  const hero = (
    <PageHero
      icon={<MovieFilterIcon sx={{ fontSize: 22 }} />}
      title="Motion Graphics"
      subtitle="Design animated compositions — kinetic type, stat cards, lower thirds — and render them locally"
      dense
      actions={
        installed ? (
          <Button size="small" variant="outlined" startIcon={<VideoLibraryIcon />}
            onClick={() => navigate("/videos")}>
            Library
          </Button>
        ) : null
      }
    />
  )

  // The gate keeps the page header. Returning the bare card would leave the one
  // screen a new user always sees first as the only screen in the app with no
  // title bar.
  const shell = (children) => (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      overflow: "hidden", bgcolor: "background.default",
    }}>
      {hero}
      <Box sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
        {children}
      </Box>
    </Box>
  )

  if (installed === false) return shell(<MotionInstallGate />)

  return shell(
    <Box sx={{ position: "absolute", inset: 0, bgcolor: "background.default" }}>
      {starting && (
        <Stack alignItems="center" justifyContent="center" spacing={1.5}
          sx={{ height: "100%", color: "text.secondary" }}>
          <CircularProgress />
          <Typography variant="body2">Starting the studio…</Typography>
        </Stack>
      )}
      {startError && !starting && (
        <Stack alignItems="center" justifyContent="center" spacing={1}
          sx={{ height: "100%", color: "text.secondary" }}>
          <Typography variant="body2">Couldn’t start the studio: {startError}</Typography>
          <Button size="small" onClick={() => window.location.reload()}>Retry</Button>
        </Stack>
      )}
      {studioUrl && (
        <iframe key={reloadKey} src={studioUrl} title="Motion Studio"
          style={{ width: "100%", height: "100%", border: 0, display: "block" }} />
      )}
    </Box>
  )
}
