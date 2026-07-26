/**
 * Endpoint → backend job type.
 *
 * Almost every tool page posts to `/api/tools/<slug>` and the handler calls
 * `create_job("tool:<slug_snake>", …)`, so the type is derivable. A handful of
 * handlers predate that convention and name their job differently. Getting it
 * wrong is quiet, not loud: the appStore entry is tagged with a type nothing
 * matches, so ToolRunner's mid-job re-attach never fires and navigating away
 * and back mid-run shows the empty dropzone while the job is still running —
 * the user thinks their file is gone.
 *
 * This table is the ONE place that knowledge lives (ToolRunner previously
 * derived it inline in two separate places), and
 * tests/test_tool_jobtype_drift.py asserts it against the real `create_job`
 * literals in backend/api/tools.py, so a new mismatch fails CI instead of
 * silently costing re-attach.
 */

// slug (after /api/tools/) → the job type the backend really creates.
export const JOB_TYPE_OVERRIDES = {
  // POST /api/tools/subtitles → create_job("tool:subtitle_export", …)
  subtitles: "tool:subtitle_export",
}

/**
 * @param {string} endpoint e.g. "/api/tools/auto-chapters"
 * @returns {string} the backend job type, or "" when the endpoint isn't a
 *          /api/tools/ route.
 */
export function toolJobTypeFor(endpoint) {
  const slug = String(endpoint || "").split("/api/tools/")[1] || ""
  if (!slug) return ""
  return JOB_TYPE_OVERRIDES[slug] || `tool:${slug.replace(/-/g, "_")}`
}

export default toolJobTypeFor
