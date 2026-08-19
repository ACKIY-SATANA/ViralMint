// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * useLibraryIndex — data layer for the Library page.
 *
 * One question to one endpoint (`/api/library/items`), which unions generated
 * videos, downloads, tool outputs and music tracks and answers with facet
 * counts. It replaced the five-fetcher arrangement in the deleted
 * `useLibrarySections` (a slice per source_type + a slice per asset kind + a
 * music count probe), and with it the wart that made search a lie:
 * filtering used to run client-side over whatever pages happened to be loaded,
 * so a match on page 3 was invisible from page 1 and the page had to render a
 * banner apologizing for it. `q` is now a server filter over the whole library.
 *
 * Filter state lives in the page (it is URL-synced); this hook owns fetching,
 * request cancellation, the debounce on typing, and paging.
 *
 * Paging REPLACES the page rather than appending to it. Append-paging measured
 * 240 tiles → 5,228 DOM nodes and 403 <img> elements after three "Load more"
 * clicks, and that grows without limit; a page swap keeps the cost flat no
 * matter how deep you go.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import http from "../api/http"
import useAppStore from "../store/appStore"

export const PAGE_SIZES = [24, 48, 96]
export const DEFAULT_PAGE_SIZE = 48
const SEARCH_DEBOUNCE_MS = 250

const EMPTY_FACETS = {
  all: 0,
  media: { video: 0, image: 0, audio: 0, doc: 0 },
  origin: { created: 0, edited: 0, imported: 0 },
  producers: [],
}

/** Serialize page filter state into query params the index understands. */
function toParams({ media, origins, producer, q, sort }) {
  const params = {}
  if (media && media !== "all") params.media = media
  if (origins?.length) params.origin = origins.join(",")
  if (producer && producer !== "all") params.producer = producer
  if (q) params.q = q
  if (sort) params.sort = sort
  return params
}

export default function useLibraryIndex({
  media, origins, producer, q, sort, view,
  page = 1, pageSize = DEFAULT_PAGE_SIZE,
}) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)

  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [libraryTotal, setLibraryTotal] = useState(0)
  const [facets, setFacets] = useState(EMPTY_FACETS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [families, setFamilies] = useState([])
  const [familyTotal, setFamilyTotal] = useState(0)
  const [loners, setLoners] = useState([])
  const [matchedKeys, setMatchedKeys] = useState(null)   // null = "everything matches"
  const [familiesLoading, setFamiliesLoading] = useState(false)

  const [taxonomy, setTaxonomy] = useState(null)

  // Debounced needle so typing doesn't fire a request per keystroke.
  const [needle, setNeedle] = useState(q || "")
  useEffect(() => {
    const t = setTimeout(() => setNeedle(q || ""), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [q])

  const originKey = (origins || []).join(",")
  const filters = useMemo(
    () => ({ media, origins: origins || [], producer, q: needle, sort }),
    [media, originKey, producer, needle, sort],  // eslint-disable-line react-hooks/exhaustive-deps
  )

  // Guards a late response from a superseded request overwriting a newer one
  // (fast typing + a tab click race otherwise flip the grid back).
  const reqIdRef = useRef(0)

  const fetchPage = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setLoading(true)
    try {
      const { data } = await http.get("/api/library/items", {
        params: { ...toParams(filters), limit: pageSize, offset: (page - 1) * pageSize },
      })
      if (reqId !== reqIdRef.current) return          // superseded
      setItems(data.items || [])
      setTotal(data.total || 0)
      setLibraryTotal(data.library_total || 0)
      setFacets(data.facets || EMPTY_FACETS)
      setError(null)
    } catch (e) {
      if (reqId !== reqIdRef.current) return
      const msg = e.response?.data?.detail || "Could not load your library"
      setError(msg)
      showSnackbar(msg, "error")
    } finally {
      if (reqId === reqIdRef.current) setLoading(false)
    }
  }, [filters, page, pageSize, showSnackbar])

  // Same supersession guard as fetchPage — two quick filter toggles in tree
  // view could otherwise resolve out of order and leave the tree (and the
  // dimming set) showing the OLDER filter's data.
  const famReqIdRef = useRef(0)

  const fetchFamilies = useCallback(async () => {
    const reqId = ++famReqIdRef.current
    setFamiliesLoading(true)
    try {
      const { data } = await http.get("/api/library/families", {
        params: { ...toParams(filters), limit: pageSize, offset: (page - 1) * pageSize },
      })
      if (reqId !== famReqIdRef.current) return       // superseded
      setFamilies(data.families || [])
      setFamilyTotal(data.family_total || 0)
      setLoners(data.loners || [])
      setMatchedKeys(new Set(data.matched_keys || []))
    } catch (e) {
      if (reqId !== famReqIdRef.current) return
      showSnackbar(e.response?.data?.detail || "Could not group your library", "error")
    } finally {
      if (reqId === famReqIdRef.current) setFamiliesLoading(false)
    }
  }, [filters, page, pageSize, showSnackbar])

  // Grid data follows the filters. The tree view fetches lazily (and only when
  // it's the visible view) because it walks the whole library to build lineage.
  useEffect(() => { fetchPage() }, [fetchPage])
  useEffect(() => { if (view === "tree") fetchFamilies() }, [view, fetchFamilies])

  useEffect(() => {
    let alive = true
    http.get("/api/library/taxonomy")
      .then(({ data }) => { if (alive) setTaxonomy(data) })
      .catch(() => { /* labels fall back to the local defaults */ })
    return () => { alive = false }
  }, [])

  const refresh = useCallback(() => {
    fetchPage()
    if (view === "tree") fetchFamilies()
  }, [fetchPage, fetchFamilies, view])

  /** Drop an item locally after a delete, so the grid doesn't wait on a refetch. */
  const removeLocal = useCallback((key) => {
    setItems((prev) => prev.filter((i) => i.key !== key))
    setTotal((t) => Math.max(0, t - 1))
    setLibraryTotal((t) => Math.max(0, t - 1))
  }, [])

  const pageCount = Math.max(1, Math.ceil((view === "tree" ? familyTotal : total) / pageSize))

  return {
    items, total, libraryTotal, facets, loading, error,
    pageCount,
    families, loners, familyTotal, matchedKeys, familiesLoading,
    taxonomy,
    refresh, removeLocal,
  }
}
