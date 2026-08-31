import { useState } from "react"
import {
  Box, Stack, Typography, FormControl, InputLabel, Select, MenuItem,
  RadioGroup, Radio, FormControlLabel, Alert, IconButton, Tooltip,
  CircularProgress,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined"
import VolumeUpOutlinedIcon from "@mui/icons-material/VolumeUpOutlined"
import StopIcon from "@mui/icons-material/Stop"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import { CAPTION_STYLES } from "../../components/tools/captionOptions"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Mirror of backend's SUPPORTED_LANGUAGES.
const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "it", name: "Italian" },
  { code: "pt", name: "Portuguese" },
  { code: "nl", name: "Dutch" },
  { code: "pl", name: "Polish" },
  { code: "ru", name: "Russian" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "zh", name: "Chinese (Mandarin)" },
  { code: "ar", name: "Arabic" },
  { code: "hi", name: "Hindi" },
  { code: "tr", name: "Turkish" },
  { code: "sv", name: "Swedish" },
  { code: "da", name: "Danish" },
  { code: "no", name: "Norwegian" },
  { code: "fi", name: "Finnish" },
]

// ⚠️ These have to be voices the DUB ENGINE accepts, and the dub runs on
// Edge TTS. They used to be Gemini names (Kore, Puck, Zephyr, …), which
// edge_tts rejects outright with `ValueError: Invalid voice 'Kore'` — so full
// dub failed for every voice the page offered, whichever one you picked.
// Derived from edge_tts_service.RECOMMENDED_VOICES and pinned by
// tests/test_voice_vocabulary_parity.py. Multilingual voices lead: this is the
// TRANSLATE page, and only they speak the target language.
const DUB_VOICES = [
  { id: "en-US-AndrewMultilingualNeural", label: "Andrew (Male, natural)" },
  { id: "en-US-AvaMultilingualNeural", label: "Ava (Female, natural)" },
  { id: "en-US-BrianMultilingualNeural", label: "Brian (Male, warm)" },
  { id: "en-US-EmmaMultilingualNeural", label: "Emma (Female, clear)" },
  { id: "en-US-AriaNeural", label: "Aria (Female, classic)" },
  { id: "en-US-GuyNeural", label: "Guy (Male, classic)" },
  { id: "en-US-JennyNeural", label: "Jenny (Female, friendly)" },
]

export default function ToolTranslate() {
  useDocumentTitle("Translate + Dub")
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [target, setTarget] = useState("es")
  const [mode, setMode] = useState("captions_only")  // "captions_only" | "full_dub"
  const [voiceId, setVoiceId] = useState(DUB_VOICES[0].id)
  const [captionStyle, setCaptionStyle] = useState("viral")
  const [emojiStyle, setEmojiStyle] = useState("moderate")

  // Voice preview — same pattern as Voiceover.jsx
  const [previewVoice, setPreviewVoice] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(null)
  const [audioRef, setAudioRef] = useState(null)
  const [blobUrlRef, setBlobUrlRef] = useState(null)

  const stopPreview = () => {
    if (audioRef) { audioRef.pause(); audioRef.src = "" }
    if (blobUrlRef) { URL.revokeObjectURL(blobUrlRef); setBlobUrlRef(null) }
    setPreviewVoice(null)
  }

  const playPreview = async (voice) => {
    if (previewVoice === voice) { stopPreview(); return }
    stopPreview()
    setPreviewLoading(voice)
    try {
      const res = await http.post(
        "/api/tts/preview",
        // The dub runs on Edge TTS, so the preview must too — previewing
        // one engine and dubbing with another is how the lists drifted
        // apart in the first place.
        { provider: "edge_tts", voice_id: voice },
        { responseType: "blob" },
      )
      const url = URL.createObjectURL(res.data)
      setBlobUrlRef(url)
      const audio = new Audio(url)
      audio.onended = () => { setPreviewVoice(null); URL.revokeObjectURL(url); setBlobUrlRef(null) }
      audio.onerror = () => { showSnackbar("Couldn't play preview", "error"); setPreviewVoice(null) }
      setAudioRef(audio)
      await audio.play()
      setPreviewVoice(voice)
    } catch (e) {
      showSnackbar(`Preview failed: ${e.response?.data?.detail || e.message}`, "error")
    } finally {
      setPreviewLoading(null)
    }
  }

  const fieldBuilder = () => {
    const fields = {
      target_language: target,
      mode,
      caption_style: captionStyle,
      emoji_style: emojiStyle,
    }
    if (mode === "full_dub") fields.voice_id = voiceId
    return fields
  }

  const targetName = LANGUAGES.find((l) => l.code === target)?.name || target

  return (
    <ToolRunner
      title="Translate + Dub"
      description={`Translate captions and optionally dub the audio into ${LANGUAGES.length}+ languages`}
      icon={<TranslateOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/translate"
      fieldBuilder={fieldBuilder}
      processLabel={mode === "full_dub" ? `Translate + dub to ${targetName}` : `Translate captions to ${targetName}`}
    >
      <Stack spacing={2.5}>
        {/* Target language */}
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>Target language</InputLabel>
          <Select value={target} label="Target language" onChange={(e) => setTarget(e.target.value)}>
            {LANGUAGES.map((l) => (
              <MenuItem key={l.code} value={l.code}>{l.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Mode picker */}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.75 }}>
            What to translate
          </Typography>
          <RadioGroup value={mode} onChange={(e) => setMode(e.target.value)}>
            <GlassPanel sx={{ p: 1, mb: 0.75, cursor: "pointer" }}
              onClick={() => setMode("captions_only")}>
              <FormControlLabel
                value="captions_only"
                control={<Radio size="small" />}
                label={
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.85rem" }}>
                      Translated captions only
                    </Typography>
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      Keeps original audio. Burns {targetName} captions on top. Faster, cheaper.
                    </Typography>
                  </Box>
                }
              />
            </GlassPanel>
            <GlassPanel sx={{ p: 1, cursor: "pointer" }}
              onClick={() => setMode("full_dub")}>
              <FormControlLabel
                value="full_dub"
                control={<Radio size="small" />}
                label={
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.85rem" }}>
                      Full dub (audio + captions)
                    </Typography>
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      Replaces audio with {targetName} voiceover. Adds matching captions.
                    </Typography>
                  </Box>
                }
              />
            </GlassPanel>
          </RadioGroup>
        </Box>

        {/* Voice picker — only relevant for full_dub */}
        {mode === "full_dub" && (
          <FormControl size="small" sx={{ minWidth: 260 }}>
            <InputLabel>Voice (multilingual)</InputLabel>
            <Select
              value={voiceId}
              label="Voice (multilingual)"
              onChange={(e) => setVoiceId(e.target.value)}
              renderValue={(v) => DUB_VOICES.find((d) => d.id === v)?.label || v}
            >
              {DUB_VOICES.map(({ id: v, label }) => {
                const isPlaying = previewVoice === v
                const isLoading = previewLoading === v
                return (
                  <MenuItem key={v} value={v} sx={{ display: "flex", alignItems: "center", gap: 1, pr: 1 }}>
                    <Box sx={{ flex: 1 }}>{label}</Box>
                    <Tooltip title={isPlaying ? "Stop preview" : "Preview voice"} placement="left" arrow>
                      <IconButton
                        size="small"
                        onMouseDown={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); playPreview(v) }}
                        sx={{ p: 0.25, color: isPlaying ? "primary.main" : "text.secondary" }}
                      >
                        {isLoading ? <CircularProgress size={14} />
                         : isPlaying ? <StopIcon sx={{ fontSize: 18 }} />
                         : <VolumeUpOutlinedIcon sx={{ fontSize: 18 }} />}
                      </IconButton>
                    </Tooltip>
                  </MenuItem>
                )
              })}
            </Select>
          </FormControl>
        )}

        {/* Caption style */}
        <Stack direction="row" spacing={2} flexWrap="wrap">
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Caption style</InputLabel>
            <Select value={captionStyle} label="Caption style" onChange={(e) => setCaptionStyle(e.target.value)}>
              {CAPTION_STYLES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Emoji intensity</InputLabel>
            <Select value={emojiStyle} label="Emoji intensity" onChange={(e) => setEmojiStyle(e.target.value)}>
              <MenuItem value="none">None</MenuItem>
              <MenuItem value="minimal">Minimal</MenuItem>
              <MenuItem value="moderate">Moderate</MenuItem>
              <MenuItem value="heavy">Heavy</MenuItem>
            </Select>
          </FormControl>
        </Stack>

        {/* Notes */}
        <Alert severity="info" variant="outlined" sx={{ fontSize: "0.78rem" }}>
          {mode === "full_dub" ? (
            <>
              The dubbed voiceover may run slightly longer or shorter than the original audio.
              ViralMint applies a small speed-up (up to 1.15×) to fit; beyond that the original
              video duration wins and any extra dub at the end is trimmed.
            </>
          ) : (
            <>
              Captions are generated with the same timestamps as the original speech, so they
              stay in sync. Original audio plays unchanged.
            </>
          )}
        </Alert>
      </Stack>
    </ToolRunner>
  )
}
