import { useState, useCallback } from "react"
import { Box, Card, CardMedia, CardContent, Typography, Chip, Checkbox, Tooltip, Button, IconButton, Stack } from "@mui/material"
import CheckCircleIcon from "@mui/icons-material/CheckCircle"
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline"
import VideocamOffOutlinedIcon from "@mui/icons-material/VideocamOffOutlined"
import DownloadIcon from "@mui/icons-material/Download"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"
import LaunchIcon from "@mui/icons-material/Launch"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import ThumbUpIcon from "@mui/icons-material/ThumbUpOutlined"
import NewspaperIcon from "@mui/icons-material/Newspaper"
import BookmarkAddIcon from "@mui/icons-material/BookmarkAdd"
import BookmarkAddedIcon from "@mui/icons-material/BookmarkAdded"

function ViralityChip({ score }) {
  const color = score >= 70 ? "success" : score >= 40 ? "warning" : "error"
  return <Chip label={score.toFixed(1)} size="small" color={color} sx={{ fontWeight: 700, fontSize: "0.75rem" }} />
}

function VPHChip({ vph }) {
  if (!vph || vph < 100) return null
  const label = vph >= 5000 ? "TRENDING NOW" : vph >= 1000 ? "Rising" : "Active"
  const color = vph >= 5000 ? "error" : vph >= 1000 ? "warning" : "default"
  return <Chip label={`${label} \u00B7 ${formatViews(vph)}/hr`} size="small" color={color} variant="outlined" sx={{ fontSize: "0.65rem", height: 22 }} />
}

function OutlierChip({ score }) {
  if (!score || score < 3) return null
  let color, label
  if (score >= 20) { color = "error"; label = `🔥 ${score}x MONSTER` }
  else if (score >= 10) { color = "error"; label = `⚡ ${score}x BREAKOUT` }
  else if (score >= 5) { color = "warning"; label = `🚀 ${score}x STRONG` }
  else { color = "info"; label = `${score}x outlier` }
  return <Chip label={label} size="small" color={color} variant="outlined" sx={{ fontWeight: 700, fontSize: "0.65rem", height: 22 }} />
}

function formatViews(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function getWatchUrl(r) {
  if (r.platform === "youtube" && r.video_id) return `https://www.youtube.com/watch?v=${r.video_id}`
  if (r.platform === "tiktok" && r.video_url) return r.video_url
  if (r.video_url) return r.video_url
  return null
}

const PAGE_SIZE = 50

export default function CardGrid({ results, onSelect, onDownload, onDelete, selectedIds, onToggle }) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const [loadingId, setLoadingId] = useState(null)
  // Thumbnails whose host refused the image. Scout results carry the
  // platform's own CDN url, and TikTok's are SIGNED — they expire by design,
  // so a scout you ran yesterday renders a card with the browser's
  // broken-image glyph and the play button floating over nothing. Nothing can
  // un-expire the url; the card just has to stop claiming it has a picture.
  const [brokenThumbs, setBrokenThumbs] = useState(() => new Set())
  const markThumbBroken = useCallback((id) => {
    setBrokenThumbs((prev) => (prev.has(id) ? prev : new Set(prev).add(id)))
  }, [])
  const showMore = useCallback(() => setVisibleCount((c) => c + PAGE_SIZE), [])

  if (!results || results.length === 0) {
    return <Typography sx={{ color: "text.secondary" }}>No results yet.</Typography>
  }

  const handleDownload = async (e, id) => {
    e.stopPropagation()
    setLoadingId(id)
    try { await onDownload(id) } finally { setLoadingId(null) }
  }

  const visible = results.slice(0, visibleCount)

  return (
    <>
    <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 2 }}>
      {visible.map((r) => {
        const isSelected = selectedIds?.has(r.id)
        const isNews = r.platform === "news"
        const watchUrl = isNews ? null : getWatchUrl(r)

        // Parse news description JSON for snippet/source
        let newsDesc = null
        if (isNews && r.description) {
          try { newsDesc = typeof r.description === "string" ? JSON.parse(r.description) : r.description } catch { /* ignore */ }
        }

        return (
          <Card
            key={r.id}
            onClick={() => onSelect?.(r)}
            sx={{
              cursor: "pointer",
              position: "relative",
              borderColor: isSelected ? "secondary.main" : "divider",
              transition: "border-color 0.2s, box-shadow 0.2s",
              "&:hover": { borderColor: "primary.main", boxShadow: "0 0 0 1px rgba(201,100,66,0.2)" },
            }}
          >
            {onToggle && (
              <Checkbox
                checked={isSelected || false}
                onChange={(e) => { e.stopPropagation(); onToggle(r.id, e) }}
                onClick={(e) => e.stopPropagation()}
                size="small"
                sx={{ position: "absolute", top: 4, left: 4, zIndex: 2, p: 0, "& .MuiSvgIcon-root": { filter: "drop-shadow(0 1px 3px rgba(0,0,0,0.8)) drop-shadow(0 0 6px rgba(0,0,0,0.5))" } }}
              />
            )}

            {r.is_downloaded && (
              <Chip
                icon={isNews ? <BookmarkAddedIcon /> : <CheckCircleIcon />}
                label={isNews ? "Saved" : "Downloaded"}
                size="small"
                color="success"
                sx={{ position: "absolute", top: 8, right: 8, zIndex: 2 }}
              />
            )}

            {isNews ? (
              /* ── News article header area ── */
              <Box sx={{
                position: "relative", width: "100%", height: 160,
                bgcolor: (t) => t.palette.mode === "dark" ? "rgba(255,152,0,0.08)" : "rgba(255,152,0,0.05)",
                display: "flex", flexDirection: "column", justifyContent: "center", px: 2,
                overflow: "hidden",
              }}>
                {r.thumbnail_url && !brokenThumbs.has(r.id) ? (
                  <>
                    {/* Decorative wash behind the headline. A refused image
                        still paints the browser's broken glyph, so it takes
                        the same guard as the video thumbnail — faint, but a
                        smudge nobody can explain. */}
                    <CardMedia component="img" height={160} image={r.thumbnail_url} alt=""
                      onError={() => markThumbBroken(r.id)}
                      sx={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.15 }} />
                    <Box sx={{ position: "relative", zIndex: 1 }}>
                      <NewspaperIcon sx={{ fontSize: 28, color: "warning.main", mb: 0.5 }} />
                      <Typography variant="caption" sx={{ color: "text.secondary", display: "block", fontWeight: 600 }}>
                        {r.author || newsDesc?.source || "News"}
                      </Typography>
                      {newsDesc?.full_text_preview && (
                        <Typography variant="caption" sx={{
                          color: "text.secondary", display: "-webkit-box",
                          WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden",
                          lineHeight: 1.4, mt: 0.5, fontSize: "0.7rem",
                        }}>
                          {newsDesc.full_text_preview}
                        </Typography>
                      )}
                    </Box>
                  </>
                ) : (
                  <>
                    <NewspaperIcon sx={{ fontSize: 28, color: "warning.main", mb: 0.5 }} />
                    <Typography variant="caption" sx={{ color: "text.secondary", display: "block", fontWeight: 600 }}>
                      {r.author || newsDesc?.source || "News"}
                    </Typography>
                    {newsDesc?.full_text_preview && (
                      <Typography variant="caption" sx={{
                        color: "text.secondary", display: "-webkit-box",
                        WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden",
                        lineHeight: 1.4, mt: 0.5, fontSize: "0.7rem",
                      }}>
                        {newsDesc.full_text_preview}
                      </Typography>
                    )}
                  </>
                )}
              </Box>
            ) : (
              /* ── Video thumbnail area ── */
              <Box sx={{ position: "relative", width: "100%", height: 160, "&:hover .play-icon": { transform: "scale(1.15)", opacity: 1 } }}>
                {r.thumbnail_url && !brokenThumbs.has(r.id) ? (
                  <CardMedia component="img" height={160} image={r.thumbnail_url} alt={r.title}
                    onError={() => markThumbBroken(r.id)} />
                ) : (
                  // A quiet plate, not an empty box: the play button and the
                  // metadata below still sit on this area, and they read as
                  // broken layout without something behind them.
                  <Box sx={{
                    width: "100%", height: 160,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    bgcolor: "action.hover",
                  }}>
                    <VideocamOffOutlinedIcon sx={{ fontSize: 30, color: "text.disabled" }} />
                  </Box>
                )}
                {watchUrl && (
                  <Box onClick={(e) => { e.stopPropagation(); window.open(watchUrl, "_blank") }} sx={{
                    position: "absolute", top: 0, left: 0, width: "100%", height: "100%",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    cursor: "pointer",
                  }}>
                    <PlayCircleOutlineIcon className="play-icon" sx={{
                      fontSize: 52, color: "rgba(255,255,255,0.85)",
                      filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.5))",
                      opacity: 0.8, transition: "transform 0.15s, opacity 0.15s",
                    }} />
                  </Box>
                )}
              </Box>
            )}

            <CardContent sx={{ p: 1.5 }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 0.5, flexWrap: "wrap", gap: 0.5 }}>
                <Box sx={{ display: "flex", gap: 0.5, alignItems: "center", flexWrap: "wrap" }}>
                  <Chip label={isNews ? "NEWS" : r.platform} size="small" variant="outlined"
                    icon={isNews ? <NewspaperIcon sx={{ fontSize: "14px !important" }} /> : undefined}
                    sx={{ textTransform: "uppercase", fontSize: "0.65rem", height: 22,
                      ...(isNews && { borderColor: "warning.main", color: "warning.main" }) }} />
                  {!isNews && <VPHChip vph={r.views_per_hour} />}
                  {!isNews && <OutlierChip score={r.outlier_score} />}
                  {isNews && newsDesc?.word_count > 0 && (
                    <Chip label={`${newsDesc.word_count} words`} size="small" variant="outlined" sx={{ fontSize: "0.6rem", height: 20 }} />
                  )}
                  {isNews && newsDesc?.emotional_tone && (
                    <Chip label={newsDesc.emotional_tone} size="small" variant="outlined" color="info" sx={{ fontSize: "0.6rem", height: 20 }} />
                  )}
                </Box>
                <ViralityChip score={r.virality_score} />
              </Box>
              <Typography variant="body2" sx={{
                fontWeight: 500, mb: 0.5,
                overflow: "hidden", textOverflow: "ellipsis",
                display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
              }}>
                {r.title}
              </Typography>

              {isNews ? (
                /* News meta line */
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 1 }}>
                  {r.author || newsDesc?.source || ""}
                  {r.upload_date && ` · ${new Date(r.upload_date).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`}
                  {newsDesc?.engagement > 0 && ` · ${formatViews(newsDesc.engagement)} engagement`}
                </Typography>
              ) : (
                /* Video meta line */
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 1 }}>
                  {r.author} &middot; <VisibilityIcon sx={{ fontSize: 12, verticalAlign: "middle", mr: 0.3 }} />{formatViews(r.views)} &middot; <ThumbUpIcon sx={{ fontSize: 12, verticalAlign: "middle", mr: 0.3 }} />{formatViews(r.likes || 0)}
                  {r.upload_date && ` \u00B7 ${new Date(r.upload_date).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`}
                </Typography>
              )}

              {/* Actions */}
              <Stack direction="row" spacing={0.5} alignItems="center">
                {!r.is_downloaded && (
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={isNews ? <BookmarkAddIcon /> : <DownloadIcon />}
                    onClick={(e) => handleDownload(e, r.id)}
                    disabled={loadingId === r.id}
                    color={isNews ? "warning" : "primary"}
                  >
                    {loadingId === r.id ? "Saving..." : isNews ? "Save to Library" : "Download & Analyze"}
                  </Button>
                )}
                <Box sx={{ flex: 1 }} />
                {r.video_url && (
                  <Tooltip title={isNews ? "Open article" : "Open original"} arrow>
                    <IconButton
                      size="small"
                      onClick={(e) => { e.stopPropagation(); window.open(r.video_url, "_blank", "noopener") }}
                      sx={{ p: 0.5, color: "text.secondary", "&:hover": { color: "primary.main" } }}
                    >
                      <LaunchIcon sx={{ fontSize: "1.1rem" }} />
                    </IconButton>
                  </Tooltip>
                )}
                <Tooltip title="Delete" arrow>
                  <IconButton
                    size="small"
                    onClick={(e) => { e.stopPropagation(); onDelete(r.id) }}
                    sx={{ p: 0.5, color: "text.secondary", "&:hover": { color: "error.main" } }}
                  >
                    <DeleteOutlineIcon sx={{ fontSize: "1.1rem" }} />
                  </IconButton>
                </Tooltip>
              </Stack>
            </CardContent>
          </Card>
        )
      })}
    </Box>
    {visibleCount < results.length && (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 2 }}>
        <Button variant="outlined" onClick={showMore}>
          Show more ({results.length - visibleCount} remaining)
        </Button>
      </Box>
    )}
    </>
  )
}
