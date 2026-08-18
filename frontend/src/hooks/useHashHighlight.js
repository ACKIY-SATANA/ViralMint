// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useEffect, useState } from "react"
import { useLocation } from "react-router-dom"

/** How long the arrival pulse stays lit. */
const HIGHLIGHT_MS = 2200

/**
 * "Did the user just deep-link to me?" — returns true for a couple of seconds
 * when `location.hash` matches `anchor`, and scrolls the element into view once
 * per arrival.
 *
 * Reads the ROUTER's hash rather than `window.location.hash`: navigating to
 * /settings#motion-graphics while already parked on /settings changes no
 * browser-level location, so a `window.location`-based effect would never fire
 * and the deep link would land the user at the top of the page wondering what
 * it was supposed to show them.
 *
 * @param {string} anchor  The bare anchor name, no "#".
 * @param {object} ref     Ref to the element to scroll into view.
 * @returns {boolean}
 */
export default function useHashHighlight(anchor, ref) {
  const { hash, key } = useLocation()
  const [highlight, setHighlight] = useState(false)

  useEffect(() => {
    if (!anchor || hash !== `#${anchor}`) return undefined
    setHighlight(true)
    // Let the section paint before scrolling to it, or the browser measures a
    // half-rendered page and lands short.
    const scroll = setTimeout(() => {
      ref?.current?.scrollIntoView({ behavior: "smooth", block: "center" })
    }, 80)
    const t = setTimeout(() => setHighlight(false), HIGHLIGHT_MS)
    return () => { clearTimeout(scroll); clearTimeout(t) }
    // `key` changes on every navigation, so re-clicking the same deep link
    // while already on it re-fires the pulse instead of doing nothing.
  }, [anchor, hash, key, ref])

  return highlight
}
