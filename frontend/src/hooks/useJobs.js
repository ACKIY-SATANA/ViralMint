import { useEffect, useRef, useState } from "react"
import http from "../api/http"
import useAppStore from "../store/appStore"

// Jobs already arrive over the WebSocket (job_progress / job_complete /
// job_failed). This poll is only a safety net for events missed while the
// socket was down, so it does NOT need to run flat-out forever: it skips while
// the tab is hidden, and backs off hard once nothing is in flight.
const ACTIVE_STATUSES = new Set(["running", "pending", "queued"])
const IDLE_MULTIPLIER = 6   // 5s → 30s when no job is in flight

// Cheap identity check so an unchanged payload doesn't hand the store a fresh
// array and re-render every subscriber on a timer.
function sameJobs(a, b) {
  if (a === b) return true
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i]
    if (x.id !== y.id || x.status !== y.status ||
        x.progress_pct !== y.progress_pct || x.current_step !== y.current_step) {
      return false
    }
  }
  return true
}

export default function useJobs(pollInterval = 5000) {
  const jobs = useAppStore((s) => s.jobs)
  const setJobs = useAppStore((s) => s.setJobs)
  const [jobTotal, setJobTotal] = useState(0)
  const timerRef = useRef(null)
  const tickRef = useRef(0)

  const fetchJobs = async (limit = 20, offset = 0) => {
    try {
      const { data } = await http.get("/api/jobs", { params: { limit, offset } })
      const next = data.jobs || []
      const prev = useAppStore.getState().jobs
      if (!sameJobs(prev, next)) setJobs(next)
      setJobTotal(data.total || 0)
      return next
    } catch (err) {
      console.error("Failed to fetch jobs:", err)
      return null
    }
  }

  useEffect(() => {
    fetchJobs()

    const tick = () => {
      if (document.visibilityState === "hidden") return
      // When nothing is in flight, only poll every IDLE_MULTIPLIER-th tick.
      const anyActive = useAppStore.getState().jobs.some((j) => ACTIVE_STATUSES.has(j.status))
      tickRef.current = anyActive ? 0 : tickRef.current + 1
      if (anyActive || tickRef.current >= IDLE_MULTIPLIER) {
        tickRef.current = 0
        fetchJobs()
      }
    }
    timerRef.current = setInterval(tick, pollInterval)

    // Catch up immediately on tab re-focus rather than waiting out a tick.
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        tickRef.current = 0
        fetchJobs()
      }
    }
    document.addEventListener("visibilitychange", onVisible)

    return () => {
      clearInterval(timerRef.current)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [pollInterval])

  return { jobs, jobTotal, fetchJobs }
}
