// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * SourceTree — the "By source" view (prototype).
 *
 * Same items as the grid, grouped by what they came from. A downloaded video
 * shows its clips, the captioned cut of a clip, its extracted audio and its
 * subtitle file as one family, so the question "is this downloaded or audio or
 * generated?" stops needing an answer: you find things where you made them.
 *
 * Families with lineage lead; everything nothing was made from yet collapses
 * into one tail block, because a wall of "nothing derived yet" rows would bury
 * the only thing this view exists to show.
 *
 * The colored rail from the tile becomes the family's spine here — same
 * device, structural duty.
 */
import { useState } from "react"
import { Box, Typography, Stack, Tooltip, Collapse, alpha, useTheme } from "@mui/material"
import MovieRoundedIcon from "@mui/icons-material/MovieRounded"
import ImageRoundedIcon from "@mui/icons-material/ImageRounded"
import GraphicEqRoundedIcon from "@mui/icons-material/GraphicEqRounded"
import DescriptionRoundedIcon from "@mui/icons-material/DescriptionRounded"
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded"
import { ORIGINS, originColor, originTextColor, fmtDay } from "./assetModel"
import { formatClock } from "../../utils/format"

const MEDIA_ICON = {
  video: MovieRoundedIcon,
  image: ImageRoundedIcon,
  audio: GraphicEqRoundedIcon,
  doc: DescriptionRoundedIcon,
}

export default function SourceTree({ families, loners, onOpen, dimmedIds }) {
  const [showLoners, setShowLoners] = useState(false)

  return (
    <Stack spacing={1.5}>
      {families.map(({ root, children }) => (
        <Family key={root.key} root={root} nodes={children} onOpen={onOpen} dimmedIds={dimmedIds} />
      ))}

      {loners.length > 0 && (
        <Box sx={{ mt: 0.5 }}>
          <Stack direction="row" alignItems="center" spacing={0.75}
            onClick={() => setShowLoners((v) => !v)}
            sx={{ cursor: "pointer", py: 1, color: "text.secondary", "&:hover": { color: "text.primary" } }}>
            <ExpandMoreRoundedIcon sx={{
              fontSize: 18,
              transform: showLoners ? "none" : "rotate(-90deg)",
              transition: "transform .15s ease",
            }} />
            <Typography sx={{ fontSize: "0.78rem", fontWeight: 600 }}>
              Nothing made from these yet
            </Typography>
            <Typography sx={{ fontSize: "0.74rem", color: "text.disabled", fontVariantNumeric: "tabular-nums" }}>
              {loners.length}
            </Typography>
          </Stack>
          <Collapse in={showLoners}>
            <Box sx={{
              display: "grid", gap: 0.75, pb: 1,
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            }}>
              {loners.map((item) => (
                <LonerRow key={item.key} item={item} onOpen={onOpen} dimmed={dimmedIds?.has(item.key)} />
              ))}
            </Box>
          </Collapse>
        </Box>
      )}
    </Stack>
  )
}

function Family({ root, nodes, onOpen, dimmedIds }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const oc = originColor(root.origin, isDark)
  const RootIcon = MEDIA_ICON[root.media]
  const dimmed = dimmedIds?.has(root.key)
  const count = countNodes(nodes)

  return (
    <Box sx={{
      position: "relative", borderRadius: "14px", overflow: "hidden",
      border: 1, borderColor: "divider", bgcolor: "background.paper",
      boxShadow: theme.customShadows?.sm,
    }}>
      <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, bgcolor: oc, opacity: 0.9 }} />

      {/* Root */}
      <Stack direction="row" spacing={1.5} alignItems="center"
        onClick={() => onOpen?.(root)}
        sx={{
          pl: 2, pr: 1.5, py: 1.25, cursor: "pointer", opacity: dimmed ? 0.45 : 1,
          "&:hover": { bgcolor: "action.hover" },
        }}>
        <Box sx={{
          width: 96, height: 54, borderRadius: "8px", flexShrink: 0, overflow: "hidden",
          bgcolor: alpha(oc, isDark ? 0.16 : 0.08),
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          {root.thumb_url
            ? <Box component="img" src={root.thumb_url} alt="" sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
            : <RootIcon sx={{ fontSize: 20, color: alpha(oc, 0.6) }} />}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography noWrap sx={{ fontSize: "0.88rem", fontWeight: 700, letterSpacing: "-0.01em" }}>
            {root.title}
          </Typography>
          <Stack direction="row" spacing={0.6} alignItems="center" sx={{ mt: 0.3 }}>
            <OriginBadge origin={root.origin} />
            <Meta>{root.producer}</Meta>
            {root.platform && <><Dot /><Meta>{root.platform}</Meta></>}
            {root.duration_seconds != null && <><Dot /><Meta>{formatClock(root.duration_seconds)}</Meta></>}
            <Dot /><Meta>{fmtDay(root.created_at)}</Meta>
          </Stack>
        </Box>
        <Typography sx={{
          flexShrink: 0, fontSize: "0.7rem", fontWeight: 700, color: alpha(oc, 0.9),
          fontVariantNumeric: "tabular-nums",
        }}>
          {count} derived
        </Typography>
      </Stack>

      {/* Derivatives — nested, each level on its own dashed spine */}
      <Box sx={{ pl: 3.75, pr: 1.5, pb: 1.25 }}>
        <Branch nodes={nodes} onOpen={onOpen} dimmedIds={dimmedIds} />
      </Box>
    </Box>
  )
}

function Branch({ nodes, onOpen, dimmedIds }) {
  return (
    <Box sx={{ ml: 0.5, pl: 1.75, borderLeft: "1px dashed", borderColor: "divider" }}>
      {nodes.map((n) => (
        <Box key={n.item.key}>
          <ChildRow item={n.item} onOpen={onOpen} dimmed={dimmedIds?.has(n.item.key)} />
          {n.children.length > 0 && <Branch nodes={n.children} onOpen={onOpen} dimmedIds={dimmedIds} />}
        </Box>
      ))}
    </Box>
  )
}

function ChildRow({ item, onOpen, dimmed }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const oc = originColor(item.origin, isDark)
  const Icon = MEDIA_ICON[item.media]

  return (
    <Stack direction="row" alignItems="center" spacing={1}
      onClick={() => onOpen?.(item)}
      sx={{
        py: 0.55, pl: 1, pr: 1, borderRadius: "8px", cursor: "pointer",
        position: "relative", opacity: dimmed ? 0.4 : 1,
        "&:hover": { bgcolor: "action.hover" },
        // tick joining the row to its parent's spine
        "&::before": {
          content: '""', position: "absolute", left: -14, top: "50%",
          width: 14, borderTop: "1px dashed", borderColor: "divider",
        },
      }}>
      <Box sx={{
        width: 44, height: 26, borderRadius: "5px", flexShrink: 0, overflow: "hidden",
        bgcolor: alpha(oc, isDark ? 0.18 : 0.09),
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {item.thumb_url
          ? <Box component="img" src={item.thumb_url} alt="" sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <Icon sx={{ fontSize: 13, color: alpha(oc, 0.7) }} />}
      </Box>
      <Typography noWrap sx={{ fontSize: "0.78rem", fontWeight: 500, maxWidth: 420 }}>
        {item.title}
      </Typography>
      <OriginBadge origin={item.origin} />
      <Meta>{item.producer}</Meta>
      {item.duration_seconds != null && <><Dot /><Meta>{formatClock(item.duration_seconds)}</Meta></>}
      {item.hook_score != null && <><Dot /><Meta>hook {item.hook_score}</Meta></>}
      <Box sx={{ flex: 1 }} />
      <Meta sx={{ color: "text.disabled" }}>{fmtDay(item.created_at)}</Meta>
    </Stack>
  )
}

function LonerRow({ item, onOpen, dimmed }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const oc = originColor(item.origin, isDark)
  const Icon = MEDIA_ICON[item.media]
  return (
    <Stack direction="row" alignItems="center" spacing={1}
      onClick={() => onOpen?.(item)}
      sx={{
        py: 0.6, px: 1, borderRadius: "10px", cursor: "pointer", minWidth: 0,
        border: 1, borderColor: "divider", bgcolor: "background.paper",
        opacity: dimmed ? 0.45 : 1,
        "&:hover": { bgcolor: "action.hover", borderColor: alpha(oc, 0.4) },
      }}>
      <Box sx={{ width: 3, alignSelf: "stretch", borderRadius: 2, bgcolor: oc, opacity: 0.85, flexShrink: 0 }} />
      <Box sx={{
        width: 40, height: 24, borderRadius: "5px", flexShrink: 0, overflow: "hidden",
        bgcolor: alpha(oc, isDark ? 0.18 : 0.09), display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {item.thumb_url
          ? <Box component="img" src={item.thumb_url} alt="" sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <Icon sx={{ fontSize: 12, color: alpha(oc, 0.7) }} />}
      </Box>
      <Typography noWrap sx={{ fontSize: "0.76rem", fontWeight: 500, flex: 1, minWidth: 0 }}>
        {item.title}
      </Typography>
      <Meta sx={{ flexShrink: 0 }}>{item.producer}</Meta>
    </Stack>
  )
}

function countNodes(nodes) {
  return nodes.reduce((n, x) => n + 1 + countNodes(x.children), 0)
}

export function OriginBadge({ origin, size = "sm" }) {
  const theme = useTheme()
  const oc = originColor(origin, theme.palette.mode === "dark")
  return (
    <Tooltip title={ORIGINS[origin]?.hint || ""} enterDelay={500}>
      <Stack direction="row" alignItems="center" spacing={0.4} sx={{ flexShrink: 0 }}>
        <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: oc }} />
        <Typography sx={{
          fontSize: size === "sm" ? "0.6rem" : "0.68rem", fontWeight: 700,
          letterSpacing: "0.07em", textTransform: "uppercase",
          color: originTextColor(origin, theme.palette.mode === "dark"),
        }}>
          {ORIGINS[origin]?.label}
        </Typography>
      </Stack>
    </Tooltip>
  )
}

function Meta({ children, sx }) {
  return <Typography noWrap sx={{ fontSize: "0.7rem", color: "text.secondary", flexShrink: 0, ...sx }}>{children}</Typography>
}
function Dot() {
  return <Typography sx={{ fontSize: "0.7rem", color: "text.disabled", flexShrink: 0 }}>·</Typography>
}
