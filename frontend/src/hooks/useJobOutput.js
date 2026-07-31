// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useEffect, useRef, useState } from "react"
import http from "../api/http"
import useAppStore from "../store/appStore"

/**
 * The structured output of a finished tool job, for rich `resultPreview`s.
 *
 * Why this exists: ToolRunner hands `resultPreview` a job id whose structured
 * output the previews looked for at `activeJobs[jobId].output` — and the store
 * NEVER sets that field (`completeJob` writes only status/percent). So
 * `output` is permanently undefined, and every preview reading it fell through
 * to its "click Download instead" placeholder. Metadata and Auto Chapters
 * shipped that way: their copy-to-clipboard previews — the whole point of a
 * titles/tags/chapters tool — could not render, and Metadata's fallback even
 * blamed a cleared session for what was a missing wire.
 *
 * Resolution order:
 *   1. an `output` object a caller already has (no request)
 *   2. the live store entry, if some future code path populates it
 *   3. one GET /api/jobs/{id} — the authoritative record
 *
 * (3) is what makes previews survive a reload: the store is empty then, but
 * the job row isn't. The request is a loopback read and fires at most once per
 * job id.
 *
 * Returns { output, loading, error }. `output` is the parsed `output_json`
 * dict (the runner's own shape — top-level `metadata` / `chapters` / …), or
 * null while unknown.
 */
export default function useJobOutput(jobId, output) {
  const storeOutput = useAppStore((s) => (jobId ? s.activeJobs[jobId]?.output : null))
  const [fetched, setFetched] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // Guards a second fetch for the same job (StrictMode double-mount, re-render
  // on an unrelated store change). Reset when the job id changes.
  const fetchedFor = useRef(null)

  const direct = output || storeOutput || null

  useEffect(() => {
    if (fetchedFor.current !== jobId) {
      fetchedFor.current = null
      setFetched(null)
      setError(null)
    }
    if (!jobId || direct || fetchedFor.current === jobId) return
    fetchedFor.current = jobId
    let cancelled = false
    setLoading(true)
    http.get(`/api/jobs/${jobId}`)
      .then((res) => {
        if (cancelled) return
        const raw = res.data?.output_json
        // The API returns the raw column, so it's a JSON *string* — but accept
        // an already-parsed object too, in case that ever changes.
        const parsed = typeof raw === "string" ? JSON.parse(raw) : raw
        setFetched(parsed && typeof parsed === "object" ? parsed : null)
      })
      .catch((e) => { if (!cancelled) setError(e) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [jobId, direct])

  return { output: direct || fetched, loading, error }
}
