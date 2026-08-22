// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { memo, useEffect, useRef, useState } from "react"
import { Box, Button, IconButton, Stack, Tooltip, Typography } from "@mui/material"
import AddIcon from "@mui/icons-material/Add"
import CloseIcon from "@mui/icons-material/Close"
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesomeOutlined"
import ContentPasteIcon from "@mui/icons-material/ContentPasteGoOutlined"
import DeleteSweepIcon from "@mui/icons-material/DeleteSweepOutlined"
import { formatTime } from "../clipFormat"
import { parseTimestamp } from "./parseRanges"
import { MAX_RANGES, MIN_LEN_SEC } from "./useBenchRanges"

/* ── The pending cuts, as a column beside the video ────────────
   This was a full-width band UNDER the timeline: one row per range, each
   row a 1200px bar carrying about 200px of content. Four ranges cost ~180px
   of height — taken from the video, which is the thing you are actually
   looking at — to show four short timecodes, and the horizontal run of empty
   bar made a four-item list read as a table.

   A column instead. It sits to the right of the stage, so the list grows
   DOWNWARD into space the row already had and the player keeps its height no
   matter how many ranges are pending. Ten ranges now cost nothing.

   The other half of the change: the timecodes are EDITABLE. Dragging is
   right for finding a moment and wrong for "start it exactly at 1:30" — the
   old typed-rows tab could do that and the timeline could not, which is why
   people kept asking for the modal back. Each field commits into the same
   `update`/`settle` pair the drag handles use, so a typed time moves the
   block on the timeline, re-cues the IN/OUT frames and re-scopes playback
   exactly as if it had been dragged there. One model, two ways in.
*/

/**
 * A timecode you can type into, that still reads as text.
 *
 * A boxed TextField per bound would put four input outlines in a 250px column
 * and turn a list into a form. This is an <input> with the chrome removed
 * until you touch it.
 *
 * Committing is deliberately conservative. `formatTime` renders to whole
 * seconds, so a range sitting at 90.44s displays "1:30" — and re-committing
 * that string would silently move the cut by 0.44s just because the user
 * clicked in and tabbed out. So an untouched draft (`draft === shown`) is a
 * no-op, and only text the user actually changed is parsed.
 */
function TimeField({ value, onCommit, disabled, ariaLabel }) {
  const shown = formatTime(value)
  const [draft, setDraft] = useState(null)   // non-null only while focused
  const ref = useRef(null)

  // A drag on the timeline moves this bound while the field may be focused.
  // Whoever is typing wins — clobbering the draft mid-keystroke is how these
  // fields usually end up unusable — so the sync only runs when idle.
  useEffect(() => {
    if (draft === null && ref.current) ref.current.value = shown
  }, [shown, draft])

  const commit = () => {
    const text = draft
    setDraft(null)
    if (text == null || text === shown) return          // untouched — see above
    const secs = parseTimestamp(text)
    if (secs == null) { if (ref.current) ref.current.value = shown; return }
    onCommit(secs)
  }

  return (
    <Box
      component="input"
      ref={ref}
      defaultValue={shown}
      disabled={disabled}
      aria-label={ariaLabel}
      spellCheck={false}
      inputMode="numeric"
      onFocus={(e) => { setDraft(e.currentTarget.value); e.currentTarget.select() }}
      onChange={(e) => setDraft(e.currentTarget.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        // Scoped here, not bubbled: the bench's own handler already skips
        // INPUT, but Enter/Escape still need to mean commit/cancel locally.
        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur() }
        if (e.key === "Escape") {
          e.preventDefault()
          setDraft(null)
          if (ref.current) ref.current.value = shown
          e.currentTarget.blur()
        }
      }}
      // Clicking a field selects its row; stop the pointerdown from ALSO
      // reaching the row's own handler, which would re-seek and steal focus.
      onPointerDown={(e) => e.stopPropagation()}
      sx={(t) => ({
        width: 54, flexShrink: 0,
        px: 0.5, py: "1px",
        fontFamily: "ui-monospace, monospace",
        fontSize: "0.72rem", fontWeight: 600,
        textAlign: "center",
        color: "text.primary",
        bgcolor: "transparent",
        border: "1px solid transparent",
        borderRadius: "4px",
        outline: "none",
        cursor: disabled ? "default" : "text",
        "&:hover": disabled ? {} : {
          bgcolor: t.palette.mode === "dark" ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)",
          borderColor: "divider",
        },
        "&:focus": {
          bgcolor: t.palette.mode === "dark" ? "rgba(255,255,255,0.10)" : "#fff",
          borderColor: "primary.main",
        },
      })}
    />
  )
}

const Row = memo(function Row({
  range, index, selected, onSelect, onRemove, onEdit,
}) {
  const len = range.end - range.start
  const meta = range.meta

  return (
    <Box
      onPointerDown={() => onSelect(range.id)}
      sx={{
        px: 0.75, py: 0.5, borderRadius: 1.5, cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        bgcolor: selected ? "action.selected" : "transparent",
        "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
        transition: "border-color .12s ease, background-color .12s ease",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.25}>
        <Box sx={{
          width: 17, height: 17, borderRadius: "5px", flexShrink: 0, mr: 0.25,
          bgcolor: selected ? "primary.main" : "action.selected",
          color: selected ? "#fff" : "text.secondary",
          fontSize: "0.6rem", fontWeight: 800, lineHeight: "17px", textAlign: "center",
        }}>{index + 1}</Box>

        <TimeField
          value={range.start}
          ariaLabel={`Range ${index + 1} start`}
          // Clamp here rather than letting `update` do it silently: typing an
          // end before the start should pin to the floor, not swap the bounds
          // under the user.
          onCommit={(v) => onEdit(range.id, { start: Math.min(v, range.end - MIN_LEN_SEC) })}
        />
        <Typography sx={{ fontSize: "0.66rem", color: "text.disabled", flexShrink: 0 }}>→</Typography>
        <TimeField
          value={range.end}
          ariaLabel={`Range ${index + 1} end`}
          // Upper bound is left to `update`, which clamps to the source
          // duration — the floor is the only part the row has to know.
          onCommit={(v) => onEdit(range.id, { end: Math.max(v, range.start + MIN_LEN_SEC) })}
        />

        <Box sx={{ flex: 1, minWidth: 4 }} />
        <Typography sx={{
          fontSize: "0.6rem", fontWeight: 700, color: "text.secondary",
          flexShrink: 0, fontVariantNumeric: "tabular-nums",
        }}>
          {len < 60 ? `${Math.round(len)}s` : `${Math.floor(len / 60)}m${String(Math.round(len % 60)).padStart(2, "0")}`}
        </Typography>
        <IconButton
          size="small" aria-label={`Remove range ${index + 1}`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onRemove(range.id) }}
          sx={{ p: 0.15, ml: 0.25, color: "text.disabled", "&:hover": { color: "error.main" } }}
        >
          <CloseIcon sx={{ fontSize: 13 }} />
        </IconButton>
      </Stack>

      {/* The AI's own words for a block it proposed. Kept after adoption so
          "my cut" and "its cut" stay distinguishable in the list too. */}
      {meta && (meta.title || meta.reason) && (
        <Tooltip title={meta.reason || meta.title} placement="left">
          <Stack direction="row" spacing={0.4} alignItems="center" sx={{ mt: 0.15, pl: "21px", minWidth: 0 }}>
            <AutoAwesomeIcon sx={{ fontSize: 10, color: "warning.main", flexShrink: 0 }} />
            <Typography sx={{
              fontSize: "0.6rem", color: "text.secondary", minWidth: 0,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {meta.title || meta.reason}
              {meta.hook_score != null && ` · hook ${Number(meta.hook_score).toFixed(1)}`}
            </Typography>
          </Stack>
        </Tooltip>
      )}
    </Box>
  )
})

export default function RangeRail({
  ranges, activeId, atCap,
  onSelect, onRemove, onEdit, onAdd, onPaste, onClear,
  // The "Ask AI" control, rendered into this header. It lives in SourceBench
  // because the search is a background Job whose state belongs there; the
  // rail only says where it sits. It sits HERE, next to +, paste and clear,
  // because asking the AI is a fourth way to put a block in this list — not
  // a mode the bench switches into.
  aiSlot,
}) {
  return (
    <Box sx={{
      // 300, up from 252: the AI label line was the first thing to ellipsise
      // and it is the only thing saying WHY a proposed block is there.
      width: 300, flexShrink: 0,
      display: "flex", flexDirection: "column", minHeight: 0,
      pt: 2.2,   // clears the IN/OUT panes' label line so the tops align
    }}>
      <Stack direction="row" alignItems="center" spacing={0.25} sx={{ flexShrink: 0, mb: 0.4, pl: 0.25 }}>
        <Typography sx={{
          fontSize: "0.6rem", fontWeight: 800, letterSpacing: 0.8,
          color: "text.secondary",
        }}>
          RANGES
        </Typography>
        <Typography sx={{
          fontSize: "0.6rem", fontWeight: 700,
          color: atCap ? "warning.main" : "text.disabled",
        }}>
          {ranges.length}/{MAX_RANGES}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {aiSlot}
        <Tooltip title={atCap ? `The bench holds ${MAX_RANGES} ranges` : "Add a range at the playhead (N)"}>
          <span>
            <IconButton size="small" disabled={atCap} onClick={onAdd}
              aria-label="Add a range at the playhead" sx={{ p: 0.2 }}>
              <AddIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Paste times you already have — show notes, a chapter list, a message">
          <span>
            <IconButton size="small" disabled={atCap} onClick={onPaste}
              aria-label="Paste timestamps" sx={{ p: 0.2 }}>
              <ContentPasteIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Remove every pending range">
          <span>
            <IconButton size="small" disabled={!ranges.length} onClick={onClear}
              aria-label="Clear all ranges" sx={{ p: 0.2 }}>
              <DeleteSweepIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Box sx={{
        flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden",
        borderRadius: 1.5, border: "1px solid", borderColor: "divider",
        p: 0.5,
        // The list is the one thing here that can outgrow its box; scroll it
        // rather than letting it push the footer under the clip filmstrip,
        // which is what the old full-width band did.
        "&::-webkit-scrollbar": { width: 8 },
        "&::-webkit-scrollbar-thumb": { bgcolor: "action.selected", borderRadius: 4 },
      }}>
        {ranges.length === 0 ? (
          <Stack spacing={0.75} sx={{ p: 1 }}>
            <Typography sx={{ fontSize: "0.66rem", color: "text.disabled", lineHeight: 1.5 }}>
              No ranges yet. Drag across the filmstrip, press <strong>N</strong>,
              paste timestamps, or <strong>Ask AI</strong> to propose some.
            </Typography>
            <Button size="small" startIcon={<AddIcon sx={{ fontSize: 15 }} />} onClick={onAdd}
              sx={{ textTransform: "none", fontSize: "0.7rem", alignSelf: "flex-start" }}>
              Add at playhead
            </Button>
          </Stack>
        ) : (
          <Stack spacing={0.4}>
            {ranges.map((r, i) => (
              <Row
                key={r.id}
                range={r}
                index={i}
                selected={r.id === activeId}
                onSelect={onSelect}
                onRemove={onRemove}
                onEdit={onEdit}
              />
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  )
}
