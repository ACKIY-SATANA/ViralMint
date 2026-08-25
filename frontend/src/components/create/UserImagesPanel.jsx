// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
//
// "Your images" for the Smart Video studio (/stock). The user's own stills
// fill scenes of an otherwise-stock video, in order from the hook; scenes
// they don't cover still come from Pexels.
//
// Deliberately NOT the same control as ImageUpload's single start image: that
// one is a MODE (one picture becomes the whole video) and this one is an
// ingredient. The rail only offers one of them at a time for that reason.
import { useRef, useState } from "react"
import { Box, Typography, Button, Stack, IconButton, Tooltip } from "@mui/material"
import AddPhotoAlternateIcon from "@mui/icons-material/AddPhotoAlternate"
import CloseIcon from "@mui/icons-material/Close"
import http from "../../api/http"

// Mirrors the backend's scene ceiling (pexels_service.MAX_SCENES). Past this
// the extras genuinely cannot be placed, so the picker stops accepting them
// rather than uploading files the render will report as unused.
export const MAX_USER_IMAGES = 12

export default function UserImagesPanel({ images, setImages, onError }) {
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef(null)

  const remaining = MAX_USER_IMAGES - images.length

  const handleFiles = async (e) => {
    const picked = Array.from(e.target.files || [])
    // Reset immediately so picking the same file twice in a row still fires.
    e.target.value = ""
    if (!picked.length) return

    const accepted = picked.slice(0, remaining)
    if (picked.length > accepted.length) {
      onError?.(
        `Only ${MAX_USER_IMAGES} images fit in one video — the rest weren't added.`,
      )
    }

    setUploading(true)
    try {
      // Sequential, not Promise.all: the order the user picked them in IS the
      // order they appear in the video, and concurrent uploads finish out of
      // order.
      const added = []
      for (const file of accepted) {
        const fd = new FormData()
        fd.append("file", file)
        try {
          const res = await http.post("/api/media/upload", fd, {
            headers: { "Content-Type": "multipart/form-data" },
          })
          added.push(res.data.url)
        } catch (err) {
          onError?.(
            `Couldn't upload ${file.name}: ${err.response?.data?.detail || err.message}`,
          )
        }
      }
      if (added.length) setImages([...images, ...added])
    } finally {
      setUploading(false)
    }
  }

  const removeAt = (idx) => setImages(images.filter((_, i) => i !== idx))

  return (
    <Box>
      <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 1 }}>
        Your photos fill the first scenes, starting with the hook. The rest of
        the video still uses stock footage.
      </Typography>

      {images.length > 0 && (
        <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.75, mb: 1 }}>
          {images.map((url, idx) => (
            <Box key={`${url}-${idx}`} sx={{ position: "relative" }}>
              <Box
                component="img"
                src={url}
                alt={`Scene ${idx + 1}`}
                sx={{
                  width: 62, height: 62, objectFit: "cover",
                  borderRadius: 1.5, border: 1, borderColor: "divider",
                  display: "block",
                }}
              />
              {/* The position IS the meaning here — image 1 opens the video —
                  so it is stated on the tile rather than implied by order. */}
              <Box
                sx={{
                  position: "absolute", bottom: 2, left: 2,
                  px: 0.5, borderRadius: 0.75,
                  bgcolor: "rgba(0,0,0,0.65)", color: "#fff",
                  fontSize: "0.6rem", lineHeight: 1.5, fontWeight: 700,
                }}
              >
                {idx + 1}
              </Box>
              <Tooltip title={`Remove image ${idx + 1}`}>
                <IconButton
                  size="small"
                  aria-label={`Remove image ${idx + 1}`}
                  onClick={() => removeAt(idx)}
                  sx={{
                    position: "absolute", top: -6, right: -6,
                    width: 20, height: 20,
                    bgcolor: "rgba(0,0,0,0.7)", color: "#fff",
                    "&:hover": { bgcolor: "rgba(0,0,0,0.9)" },
                  }}
                >
                  <CloseIcon sx={{ fontSize: 12 }} />
                </IconButton>
              </Tooltip>
            </Box>
          ))}
        </Stack>
      )}

      <Button
        size="small"
        variant="outlined"
        fullWidth
        startIcon={<AddPhotoAlternateIcon />}
        disabled={uploading || remaining <= 0}
        onClick={() => inputRef.current?.click()}
        sx={{ textTransform: "none" }}
      >
        {uploading
          ? "Uploading…"
          : remaining <= 0
            ? `All ${MAX_USER_IMAGES} scenes covered`
            : images.length
              ? `Add more (${remaining} left)`
              : "Add your images"}
      </Button>
      <input
        ref={inputRef}
        type="file"
        hidden
        multiple
        accept="image/*"
        onChange={handleFiles}
      />
    </Box>
  )
}
