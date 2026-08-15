// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { createContext, useContext } from "react"

// What ToolRunner knows about the media currently loaded into its dropzone,
// published to the tool-specific config `children`.
//
// Why this exists: the config panel used to be blind to its own input. A tool
// whose controls are timestamps (Trim, Video→GIF) asked for a start/end into a
// video whose length it had no way to learn, and a tool that crops has nothing
// to draw on at all. ToolRunner mounts the selected file as a real <video> and
// reports what that element tells it — one source, no extra probe request.
//
//   duration    seconds (0 until metadata loads, or if it never does)
//   width       intrinsic pixel width of the loaded video (0 = unknown)
//   height      intrinsic pixel height (0 = unknown). Together these let a
//               tool state the exact shape of what it will produce instead of
//               assuming 16:9 and being wrong on every vertical short.
//   previewUrl  object URL for the selected file; "" when nothing is loaded
const ToolInputContext = createContext({
  duration: 0, width: 0, height: 0, previewUrl: "",
})

export function useToolInput() {
  return useContext(ToolInputContext)
}

export default ToolInputContext
