// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useEffect, useRef } from "react"
import {
  Box, Stack, Typography, Button, Chip, Alert,
  CircularProgress, LinearProgress, alpha, useTheme,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from "@mui/material"
import MovieFilterIcon from "@mui/icons-material/MovieFilterOutlined"
import PlayArrowIcon from "@mui/icons-material/PlayArrowOutlined"
import CheckCircleIcon from "@mui/icons-material/CheckCircle"
import DownloadIcon from "@mui/icons-material/DownloadOutlined"
import DeleteIcon from "@mui/icons-material/DeleteOutlineOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import useHashHighlight from "../../hooks/useHashHighlight"

/**
 * Motion Graphics (HyperFrames) — the opt-in, on-demand Node plugin that
 * renders code-driven motion graphics locally (HTML → MP4 via headless Chrome
 * + FFmpeg).
 *
 * Nothing ships in the base install; the portable Node runtime and the npm
 * package are downloaded here on first opt-in and removed in full on
 * uninstall. Rendering never leaves the machine and costs nothing.
 */
export default function MotionGraphicsSection() {
  const theme = useTheme()
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  // Keep the app-wide opt-in badge (sidebar + Tools card) in sync as the user
  // installs / uninstalls here.
  const setMotionInstalled = useAppStore((s) => s.setMotionInstalled)

  const [status, setStatus] = useState(null)   // null while loading
  const [busy, setBusy] = useState(false)
  const [uninstallConfirm, setUninstallConfirm] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const pollRef = useRef(null)
  const anchorRef = useRef(null)
  const highlight = useHashHighlight("motion-graphics", anchorRef)

  const fetchStatus = async () => {
    try {
      const res = await http.get("/api/settings/motion-graphics/status")
      setStatus(res.data)
      setMotionInstalled(!!res.data?.installed)
      return res.data
    } catch (e) {
      console.error("Failed to fetch motion-graphics status:", e)
      return null
    }
  }

  const startPoll = () => {
    if (pollRef.current) return
    let nullStreak = 0
    const tick = async () => {
      const data = await fetchStatus()
      if (data == null) {
        // A transient fetch failure (network blip, or a dev reload bouncing the
        // server mid-install). Keep polling rather than silently freezing the
        // progress bar; give up only after several consecutive failures so we
        // don't spin forever against a server that is genuinely gone.
        nullStreak += 1
        if (nullStreak <= 5) {
          pollRef.current = setTimeout(tick, 2000)
        } else {
          pollRef.current = null
          showSnackbar("Lost contact with the server while installing — reload to check status.", "warning")
        }
        return
      }
      nullStreak = 0
      if (data.installing) {
        pollRef.current = setTimeout(tick, 2000)
      } else {
        pollRef.current = null
        if (data.installed && !data.error) {
          showSnackbar("Motion Graphics ready — you can now render motion video locally.", "success")
        } else if (data.error) {
          showSnackbar(`Motion Graphics install failed: ${data.error}`, "error")
        }
      }
    }
    pollRef.current = setTimeout(tick, 1500)
  }

  useEffect(() => {
    fetchStatus().then((data) => {
      if (data?.installing) startPoll()
    })
    return () => {
      if (pollRef.current) {
        clearTimeout(pollRef.current)
        pollRef.current = null
      }
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const install = async () => {
    const updating = status?.update_available
    setBusy(true)
    setTestResult(null)
    try {
      const res = await http.post("/api/settings/motion-graphics/install")
      if (res.data?.ok) {
        showSnackbar(
          updating
            ? "Updating Motion Graphics… this reuses the installed Node runtime."
            : `Installing Motion Graphics… a one-time ~${status?.approx_download_mb ?? 350} MB download.`,
          "info")
        await fetchStatus()
        startPoll()
      } else {
        showSnackbar(res.data?.error || "Could not start install", "error")
      }
    } catch (e) {
      showSnackbar(`Install failed: ${e.message}`, "error")
    } finally {
      setBusy(false)
    }
  }

  const uninstall = async () => {
    setUninstallConfirm(false)
    setBusy(true)
    try {
      await http.post("/api/settings/motion-graphics/uninstall")
      setTestResult(null)
      await fetchStatus()
      showSnackbar("Motion Graphics removed.", "success")
    } catch (e) {
      showSnackbar(`Uninstall failed: ${e.message}`, "error")
    } finally {
      setBusy(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await http.post("/api/settings/motion-graphics/test")
      setTestResult(res.data)
    } catch (e) {
      setTestResult({ ok: false, detail: e.message })
    } finally {
      setTesting(false)
    }
  }

  if (!status) {
    return (
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 1 }}>
        <CircularProgress size={16} />
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Loading Motion Graphics status…
        </Typography>
      </Stack>
    )
  }

  const { installed, installing, error, platform_supported, update_available } = status
  // A prior install of a different pinned version → offer a quick in-place
  // "Update" rather than the first-time "Install ~350 MB download".
  const showUpdate = !installed && update_available && !installing && platform_supported
  const showInstall = !installed && !update_available && !installing && platform_supported

  return (
    <Box
      ref={anchorRef}
      data-hash-target="motion-graphics"
      sx={{
        p: 1.5, borderRadius: 2,
        border: "1px solid",
        borderColor: highlight ? "primary.main" : "divider",
        boxShadow: highlight ? `0 0 0 4px ${alpha(theme.palette.primary.main, 0.18)}` : "none",
        transition: "box-shadow .4s, border-color .4s",
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
        <MovieFilterIcon sx={{ fontSize: 18, color: "primary.main" }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Motion Graphics</Typography>

        {installed && (
          <>
            <Chip size="small" color="success" variant="outlined" icon={<CheckCircleIcon sx={{ fontSize: 14 }} />} label="Installed" />
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {status.disk_size_mb || status.approx_download_mb} MB on disk
              {status.engine_cache_mb ? ` · +${status.engine_cache_mb} MB engine cache` : ""}
            </Typography>
          </>
        )}
        {showInstall && <Chip size="small" variant="outlined" label="Optional" />}
        {showUpdate && <Chip size="small" color="warning" variant="outlined" label="Update available" />}
        {installing && <Chip size="small" color="info" variant="outlined" label="Installing…" />}
        {!platform_supported && <Chip size="small" color="default" variant="outlined" label="Unsupported platform" />}

        <Box sx={{ flexGrow: 1 }} />

        {showInstall && (
          <Button size="small" variant="contained" startIcon={<DownloadIcon sx={{ fontSize: 16 }} />}
            disabled={busy} onClick={install}>
            {busy ? "Starting…" : "Install"}
          </Button>
        )}
        {showUpdate && (
          <Button size="small" variant="contained" color="warning" startIcon={<DownloadIcon sx={{ fontSize: 16 }} />}
            disabled={busy} onClick={install}>
            {busy ? "Updating…" : "Update"}
          </Button>
        )}
        {installed && (
          <>
            <Button size="small" variant="outlined" startIcon={<PlayArrowIcon sx={{ fontSize: 16 }} />}
              disabled={testing || busy} onClick={runTest}>
              {testing ? "Rendering…" : "Test render"}
            </Button>
            <Button size="small" variant="outlined" color="error" startIcon={<DeleteIcon sx={{ fontSize: 16 }} />}
              disabled={busy} onClick={() => setUninstallConfirm(true)}>
              Remove
            </Button>
          </>
        )}
      </Stack>

      {showInstall && (
        <Typography variant="caption" sx={{ display: "block", color: "text.secondary", fontSize: "0.76rem", lineHeight: 1.5, mb: 1 }}>
          Render motion-graphics video — kinetic typography, stat cards, lower-thirds — entirely
          on your machine. Deterministic, repeatable, and free to render as many times as you
          like, because no AI model is involved. One-time ~{status.approx_download_mb} MB
          download (a portable Node runtime plus the HyperFrames engine), all of it inside your
          data folder and all of it removed again if you uninstall.
        </Typography>
      )}

      {showUpdate && (
        <Typography variant="caption" sx={{ display: "block", color: "text.secondary", fontSize: "0.76rem", lineHeight: 1.5, mb: 1 }}>
          A newer Motion Graphics engine ships with this version
          {status.installed_version ? ` (${status.installed_version} → ${status.hyperframes_version})` : ""}.
          Updating is quick — it reuses the installed Node runtime and the cached packages, so
          it is not a full re-download.
        </Typography>
      )}

      {!platform_supported && (
        <Alert severity="info" sx={{ fontSize: "0.78rem", py: 0.25 }}>
          Motion Graphics needs a portable Node build for your platform, and there isn't one
          published for this CPU architecture.
        </Alert>
      )}

      {installing && (
        <Box sx={{ mt: 0.5 }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {status.current_step || "Installing…"}
          </Typography>
          <LinearProgress
            variant={status.progress_pct ? "determinate" : "indeterminate"}
            value={status.progress_pct || 0}
            sx={{ mt: 0.5, height: 6, borderRadius: 3 }}
          />
        </Box>
      )}

      {error && !installing && (
        <Alert severity="error" sx={{ mt: 1, fontSize: "0.78rem", py: 0.25 }}>{error}</Alert>
      )}

      {testResult && (
        <Alert severity={testResult.ok ? "success" : "error"} sx={{ mt: 1, fontSize: "0.78rem", py: 0.25 }}>
          {testResult.ok
            ? `Render OK — ${testResult.output_size_kb} KB in ${testResult.latency_ms} ms.`
            : `Render failed: ${testResult.detail || testResult.error}`}
        </Alert>
      )}

      <Dialog open={uninstallConfirm} onClose={() => setUninstallConfirm(false)}>
        <DialogTitle>Remove Motion Graphics?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ fontSize: "0.88rem" }}>
            This deletes the portable Node runtime and the HyperFrames engine
            (~{status.disk_size_mb || status.approx_download_mb} MB). Motion videos you have
            already rendered stay in your library, and you can reinstall at any time.
            {status.engine_cache_mb ? (
              <>
                {" "}The engine also keeps a headless Chrome
                (~{status.engine_cache_mb} MB) in <code>{status.engine_cache_path}</code>.
                That folder is shared with any other HyperFrames install on this machine, so
                it is left alone — delete it yourself if you want the space back.
              </>
            ) : null}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUninstallConfirm(false)} disabled={busy}>Cancel</Button>
          <Button onClick={uninstall} color="error" variant="contained" disabled={busy}>Remove</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
