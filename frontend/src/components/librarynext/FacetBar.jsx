// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * FacetBar — the two axes, made visible (prototype).
 *
 * Row 1 is WHAT IT IS (media). Row 2 is WHERE IT CAME FROM (origin) plus the
 * narrowing controls. Keeping them on separate rows is the whole point: the
 * current page mixes both into one tab strip, so items with two true answers
 * have to be filed under one.
 */
import {
  Box, Typography, Stack, Select, MenuItem, IconButton, Tooltip,
  ToggleButton, ToggleButtonGroup, alpha, useTheme,
} from "@mui/material"
import ViewComfyRoundedIcon from "@mui/icons-material/ViewComfyRounded"
import ViewModuleRoundedIcon from "@mui/icons-material/ViewModuleRounded"
import GridViewRoundedIcon from "@mui/icons-material/GridViewRounded"
import AccountTreeRoundedIcon from "@mui/icons-material/AccountTreeRounded"
import MovieRoundedIcon from "@mui/icons-material/MovieRounded"
import ImageRoundedIcon from "@mui/icons-material/ImageRounded"
import GraphicEqRoundedIcon from "@mui/icons-material/GraphicEqRounded"
import DescriptionRoundedIcon from "@mui/icons-material/DescriptionRounded"
import AppsRoundedIcon from "@mui/icons-material/AppsRounded"
import { ORIGINS, ORIGIN_KEYS, MEDIA_KEYS, MEDIA, originColor } from "./assetModel"

const MEDIA_ICON = {
  all: AppsRoundedIcon,
  video: MovieRoundedIcon,
  image: ImageRoundedIcon,
  audio: GraphicEqRoundedIcon,
  doc: DescriptionRoundedIcon,
}

export default function FacetBar({
  media, onMediaChange,
  origins, onToggleOrigin,
  producer, onProducerChange, producerOptions,
  sort, onSortChange,
  view, onViewChange,
  dense, onDenseChange,
  mediaCounts, originCounts,
}) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const tabs = ["all", ...MEDIA_KEYS]

  return (
    <Box sx={{ px: { xs: 2, md: 3 }, pt: 1.5, pb: 1.25 }}>
      {/* ── Axis 1: media ── */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.1 }}>
        <Box sx={{
          display: "inline-flex", gap: 0.4, p: 0.4, borderRadius: "999px",
          bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.035),
        }}>
          {tabs.map((k) => {
            const Icon = MEDIA_ICON[k]
            const active = media === k
            const count = mediaCounts[k] ?? 0
            return (
              <Box
                key={k}
                role="tab"
                aria-selected={active}
                tabIndex={0}
                onClick={() => onMediaChange(k)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onMediaChange(k) } }}
                sx={{
                  display: "flex", alignItems: "center", gap: 0.6,
                  px: 1.3, height: 30, borderRadius: "999px", cursor: "pointer",
                  bgcolor: active ? "background.paper" : "transparent",
                  boxShadow: active ? theme.customShadows?.sm : "none",
                  color: active ? "text.primary" : "text.secondary",
                  transition: "all 0.12s ease",
                  "&:hover": { color: "text.primary" },
                }}
              >
                <Icon sx={{ fontSize: 16, color: active ? "primary.main" : "inherit" }} />
                <Typography sx={{ fontSize: "0.79rem", fontWeight: active ? 700 : 500, letterSpacing: "-0.01em" }}>
                  {k === "all" ? "All" : MEDIA[k].label}
                </Typography>
                <Typography sx={{
                  fontSize: "0.68rem", fontWeight: 600, fontVariantNumeric: "tabular-nums",
                  color: active ? "text.secondary" : "text.disabled",
                }}>
                  {count}
                </Typography>
              </Box>
            )
          })}
        </Box>
        <Typography sx={{ fontSize: "0.7rem", color: "text.disabled", display: { xs: "none", lg: "block" } }}>
          what it is
        </Typography>
      </Stack>

      {/* ── Axis 2: origin + narrowing ── */}
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
        <Typography sx={{
          fontSize: "0.62rem", fontWeight: 700, letterSpacing: "0.09em",
          textTransform: "uppercase", color: "text.disabled", mr: 0.2,
        }}>
          From
        </Typography>
        {ORIGIN_KEYS.map((k) => {
          const active = origins.includes(k)
          const oc = originColor(k, isDark)
          return (
            <Tooltip key={k} title={ORIGINS[k].hint} enterDelay={500}>
              <Box
                role="checkbox"
                aria-checked={active}
                tabIndex={0}
                onClick={() => onToggleOrigin(k)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggleOrigin(k) } }}
                sx={{
                  display: "inline-flex", alignItems: "center", gap: 0.6,
                  height: 28, px: 1.1, borderRadius: "999px", cursor: "pointer",
                  border: 1,
                  borderColor: active ? alpha(oc, 0.5) : "divider",
                  bgcolor: active ? alpha(oc, isDark ? 0.16 : 0.09) : "transparent",
                  color: active ? oc : "text.secondary",
                  transition: "all 0.12s ease",
                  "&:hover": { borderColor: alpha(oc, 0.5), color: oc },
                }}
              >
                <Box sx={{
                  width: 7, height: 7, borderRadius: "50%",
                  bgcolor: active ? oc : alpha(oc, 0.4),
                }} />
                <Typography sx={{ fontSize: "0.76rem", fontWeight: active ? 700 : 500 }}>
                  {ORIGINS[k].label}
                </Typography>
                <Typography sx={{
                  fontSize: "0.66rem", fontWeight: 600, fontVariantNumeric: "tabular-nums",
                  color: active ? alpha(oc, 0.75) : "text.disabled",
                }}>
                  {originCounts[k] ?? 0}
                </Typography>
              </Box>
            </Tooltip>
          )
        })}

        <Typography sx={{ fontSize: "0.7rem", color: "text.disabled", display: { xs: "none", lg: "block" }, ml: 0.3 }}>
          where it came from
        </Typography>

        <Box sx={{ width: "1px", height: 20, bgcolor: "divider", mx: 0.5 }} />

        <Select
          size="small" value={producer} onChange={(e) => onProducerChange(e.target.value)}
          aria-label="Filter by tool"
          sx={{ height: 28, fontSize: "0.76rem", "& .MuiSelect-select": { py: 0 }, minWidth: 132 }}
        >
          <MenuItem value="all" sx={{ fontSize: "0.8rem" }}>Any tool</MenuItem>
          {producerOptions.map((p) => (
            <MenuItem key={p.key} value={p.key} sx={{ fontSize: "0.8rem" }}>
              {p.label} <Box component="span" sx={{ ml: 0.6, color: "text.disabled" }}>{p.count}</Box>
            </MenuItem>
          ))}
        </Select>

        <Select
          size="small" value={sort} onChange={(e) => onSortChange(e.target.value)}
          aria-label="Sort"
          sx={{ height: 28, fontSize: "0.76rem", "& .MuiSelect-select": { py: 0 }, minWidth: 116 }}
        >
          <MenuItem value="newest" sx={{ fontSize: "0.8rem" }}>Newest</MenuItem>
          <MenuItem value="oldest" sx={{ fontSize: "0.8rem" }}>Oldest</MenuItem>
          <MenuItem value="longest" sx={{ fontSize: "0.8rem" }}>Longest</MenuItem>
          <MenuItem value="title" sx={{ fontSize: "0.8rem" }}>Title A–Z</MenuItem>
        </Select>

        <Box sx={{ flex: 1 }} />

        <ToggleButtonGroup
          size="small" exclusive value={view}
          onChange={(_, v) => v && onViewChange(v)}
          sx={{ height: 28, "& .MuiToggleButton-root": { px: 1.1, fontSize: "0.72rem", textTransform: "none", gap: 0.5 } }}
        >
          <ToggleButton value="grid" aria-label="Grid view">
            <GridViewRoundedIcon sx={{ fontSize: 15 }} /> Grid
          </ToggleButton>
          <ToggleButton value="tree" aria-label="Group by source">
            <AccountTreeRoundedIcon sx={{ fontSize: 15 }} /> By source
          </ToggleButton>
        </ToggleButtonGroup>

        <Tooltip title={dense ? "Comfortable rows" : "Dense rows"}>
          <IconButton size="small" onClick={() => onDenseChange(!dense)}
            aria-label={dense ? "Comfortable rows" : "Dense rows"}
            sx={{ border: 1, borderColor: "divider", borderRadius: "8px", width: 28, height: 28 }}>
            {dense ? <ViewModuleRoundedIcon sx={{ fontSize: 16 }} /> : <ViewComfyRoundedIcon sx={{ fontSize: 16 }} />}
          </IconButton>
        </Tooltip>
      </Stack>
    </Box>
  )
}
