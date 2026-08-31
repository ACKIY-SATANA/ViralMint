import { useState, useRef, useEffect, useMemo } from "react"
import {
  Box, Typography, TextField, FormControl, InputLabel, Select, MenuItem,
  Stack, Button, IconButton, CircularProgress, Tooltip, Chip,
  Autocomplete,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import RecordVoiceOverOutlinedIcon from "@mui/icons-material/RecordVoiceOverOutlined"
import UploadFileOutlinedIcon from "@mui/icons-material/UploadFileOutlined"
import CloseIcon from "@mui/icons-material/Close"
import PlayArrowIcon from "@mui/icons-material/PlayArrow"
import StopIcon from "@mui/icons-material/Stop"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import ToolRunner from "../../components/tools/ToolRunner"
import PromptField from "../../components/tools/PromptField"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Fallback voice lists — used ONLY when the /api/config/voices/<provider>
// endpoint is unreachable (e.g., backend down during page load). The live
// list, when available, is much richer. These fallbacks keep the tool
// usable as a last resort.
// ⚠️ These must name voices the ENGINE accepts, and they used to name voices
// no engine here has ever had: Kore / Puck / Charon / Leda / Orus are Gemini
// names, and OpenAI's speech API rejects every one of them. Kept in step with
// tts_service.list_voices by tests/test_voice_vocabulary_parity.py.
const FALLBACK_OPENAI_VOICES = [
  { voice_id: "alloy",   name: "Alloy",   gender: "Neutral", desc: "Balanced, neutral (recommended)", recommended: true },
  { voice_id: "nova",    name: "Nova",    gender: "Female",  desc: "Bright, energetic (recommended)", recommended: true },
  { voice_id: "echo",    name: "Echo",    gender: "Male",    desc: "Even, measured" },
  { voice_id: "fable",   name: "Fable",   gender: "Neutral", desc: "Warm, British" },
  { voice_id: "onyx",    name: "Onyx",    gender: "Male",    desc: "Deep, authoritative" },
  { voice_id: "shimmer", name: "Shimmer", gender: "Female",  desc: "Soft, gentle" },
]
// Regenerated FROM edge_tts_service.RECOMMENDED_VOICES rather than re-typed:
// four of the six names had drifted (Brian "natural" vs "warm", Emma "natural"
// vs "clear", Guy and Jenny carrying a locale where the engine gives a
// description), so a voice renamed itself under the user the moment the live
// list arrived.
const FALLBACK_EDGE_VOICES = [
  { voice_id: "en-US-AndrewMultilingualNeural", name: "⭐ Andrew (Male, natural)", category: "Male", locale: "en-US" },
  { voice_id: "en-US-AvaMultilingualNeural", name: "⭐ Ava (Female, natural)", category: "Female", locale: "en-US" },
  { voice_id: "en-US-BrianMultilingualNeural", name: "⭐ Brian (Male, warm)", category: "Male", locale: "en-US" },
  { voice_id: "en-US-EmmaMultilingualNeural", name: "⭐ Emma (Female, clear)", category: "Female", locale: "en-US" },
  { voice_id: "en-US-AriaNeural", name: "⭐ Aria (Female, classic)", category: "Female", locale: "en-US" },
  { voice_id: "en-US-GuyNeural", name: "⭐ Guy (Male, classic)", category: "Male", locale: "en-US" },
  { voice_id: "en-US-JennyNeural", name: "⭐ Jenny (Female, friendly)", category: "Female", locale: "en-US" },
]

// Blank-page starters — drop in a skeleton the user can fill, then "✨ Enhance"
// it into a polished script. Each is a template with bracketed gaps.
const SCRIPT_STARTERS = [
  { label: "3 quick tips", text: "Here are three quick tips that will change how you [topic].\n\nFirst, [tip one].\nSecond, [tip two].\nAnd third, [tip three].\n\nTry it and let me know how it goes." },
  { label: "Story hook", text: "I almost gave up on [thing] — until I figured out one small change.\n\nHere's exactly what happened, and the simple shift that turned it around." },
  { label: "Product promo", text: "If you struggle with [problem], you need to see this.\n\nIt [benefit] in seconds — no [pain point] required. Here's how it works." },
  { label: "Did you know", text: "Did you know that [surprising fact]? Most people have no idea.\n\nHere's why it matters — and the one thing you should do about it today." },
]

const MAX_CHARS = 10_000
const VIDEO_MAX_MB = 1000  // mirrors ToolRunner default + backend VIDEO_MAX_BYTES

export default function ToolVoiceover() {
  useDocumentTitle("Voice-over")
  const [text, setText] = useState("")
  const [provider, setProvider] = useState("edge_tts")
  const [voiceId, setVoiceId] = useState(FALLBACK_EDGE_VOICES[0].voice_id)
  const [voiceInstructions, setVoiceInstructions] = useState("")
  const [video, setVideo] = useState(null)
  const videoInputRef = useRef(null)

  // Voice list is fetched from /api/config/voices/<provider> and cached
  // per-provider so switching back-and-forth between OpenAI and Edge
  // doesn't re-fetch. Hardcoded fallbacks (above) are used only if the
  // first fetch for a provider fails entirely — keeps the tool usable
  // when the backend is briefly unreachable.
  const [voicesByProvider, setVoicesByProvider] = useState({})  // { openai_tts: [...], edge_tts: [...] }
  const [voicesLoading, setVoicesLoading] = useState(false)
  const [voicesError, setVoicesError] = useState(null)

  const fallbackFor = (p) => p === "openai_tts" ? FALLBACK_OPENAI_VOICES : FALLBACK_EDGE_VOICES
  const voices = voicesByProvider[provider] ?? fallbackFor(provider)

  // Fetch voices for the current provider on mount + provider change.
  // Cached: skip the call if we already have voices for this provider.
  //
  // Edge TTS specifically: the backend returns all Microsoft voices
  // with the curated set marked by a ⭐ prefix on the name field. We keep
  // ONLY the starred ones — the long tail is overwhelming for creators.
  useEffect(() => {
    if (voicesByProvider[provider]) return  // already cached
    let cancelled = false
    setVoicesLoading(true)
    setVoicesError(null)
    http.get(`/api/config/voices/${provider}`)
      .then((res) => {
        if (cancelled) return
        let list = res.data?.voices ?? []
        if (provider === "edge_tts") {
          list = list.filter((v) => v.name?.startsWith("⭐"))
        }
        if (list.length > 0) {
          setVoicesByProvider((prev) => ({ ...prev, [provider]: list }))
        } else {
          // Empty response — fall back to the hardcoded list.
          setVoicesError("No voices returned by backend, using fallback list")
        }
      })
      .catch((err) => {
        if (cancelled) return
        const msg = err.response?.data?.detail || err.message || "Network error"
        setVoicesError(`Couldn't load voice list (${msg}). Using fallback.`)
        // Tool stays usable via fallbackFor(provider) — see `voices` above.
      })
      .finally(() => { if (!cancelled) setVoicesLoading(false) })
    return () => { cancelled = true }
  }, [provider, voicesByProvider])

  // After voices load (or provider switch), make sure voiceId is valid for
  // the current provider's list.
  useEffect(() => {
    if (voices.length === 0) return
    if (!voices.some((v) => v.voice_id === voiceId)) {
      setVoiceId(voices[0].voice_id)
    }
  }, [voices, voiceId])

  // Resolve the selected voice OBJECT for the Autocomplete value prop.
  const selectedVoice = useMemo(
    () => voices.find((v) => v.voice_id === voiceId) ?? null,
    [voices, voiceId],
  )

  // ── Per-voice preview playback (POST /api/tts/preview returns raw mp3) ──
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [previewVoice, setPreviewVoice] = useState(null)   // voice currently playing
  const [previewLoading, setPreviewLoading] = useState(null) // voice being fetched
  const audioRef = useRef(null)
  const blobUrlRef = useRef(null)

  const stopPreview = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.onended = null
      audioRef.current.onerror = null
      // The documented way to detach a media element is removeAttribute +
      // load(). https://html.spec.whatwg.org/multipage/media.html
      audioRef.current.removeAttribute("src")
      try { audioRef.current.load() } catch { /* ignore — load on a
        detached element can throw on some browsers */ }
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current)
      blobUrlRef.current = null
    }
    setPreviewVoice(null)
  }

  const playPreview = async (voice) => {
    // Toggle: clicking the playing voice's button stops it.
    if (previewVoice === voice) {
      stopPreview()
      return
    }
    stopPreview()  // interrupt any other playback
    setPreviewLoading(voice)
    try {
      const res = await http.post(
        "/api/tts/preview",
        { provider, voice_id: voice },
        { responseType: "blob" },
      )
      const url = URL.createObjectURL(res.data)
      blobUrlRef.current = url
      if (!audioRef.current) audioRef.current = new Audio()
      const audio = audioRef.current
      audio.src = url
      audio.onended = () => {
        setPreviewVoice(null)
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url)
          blobUrlRef.current = null
        }
      }
      audio.onerror = () => {
        showSnackbar("Couldn't play preview audio", "error")
        setPreviewVoice(null)
      }
      await audio.play()
      setPreviewVoice(voice)
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || "Preview failed"
      showSnackbar(`Voice preview failed: ${msg}`, "error")
    } finally {
      setPreviewLoading(null)
    }
  }

  // Cleanup on unmount or provider change.
  useEffect(() => () => stopPreview(), [])
  useEffect(() => { stopPreview() }, [provider])

  const chars = text.length
  const overLimit = chars > MAX_CHARS

  return (
    <ToolRunner
      title="Voice-over Generator"
      description="Turn a script into natural TTS audio"
      icon={<RecordVoiceOverOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/voiceover"
      hideFileInput
      extraFiles={{ video }}
      canSubmit={chars > 0 && !overLimit}
      disabledReason={overLimit ? "Script is over the character limit" : "Enter a script to generate"}
      fieldBuilder={() => ({
        text,
        provider,
        voice_id: voiceId,
        // voice_instructions only meaningful for OpenAI TTS (supports a
        // free-form delivery instruction). Edge TTS ignores it. Send only
        // when non-empty so the backend treats it as unset.
        ...(provider === "openai_tts" && voiceInstructions.trim()
          ? { voice_instructions: voiceInstructions.trim() }
          : {}),
      })}
      scriptInput={
        <Stack spacing={2}>
          {!text.trim() && (
            <Box>
              <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.75, color: "text.secondary" }}>
                Start from a template
              </Typography>
              <Stack direction="row" useFlexGap flexWrap="wrap" sx={{ gap: 0.75 }}>
                {SCRIPT_STARTERS.map((s) => (
                  <Chip
                    key={s.label}
                    label={s.label}
                    size="small"
                    onClick={() => setText(s.text)}
                    sx={{ fontSize: "0.74rem", height: 26 }}
                  />
                ))}
              </Stack>
            </Box>
          )}
          <PromptField
            minRows={6}
            maxRows={20}
            enhanceKind="voiceover"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste your script here..."
            error={overLimit}
            helperText={
              overLimit
                ? `Over ${MAX_CHARS.toLocaleString()} char limit — reduce to submit`
                : `${chars.toLocaleString()} / ${MAX_CHARS.toLocaleString()} chars`
            }
          />

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <FormControl size="small" sx={{ minWidth: 180 }}>
              <InputLabel>Provider</InputLabel>
              <Select value={provider} label="Provider" onChange={(e) => setProvider(e.target.value)}>
                <MenuItem value="edge_tts">Edge TTS (free)</MenuItem>
                <MenuItem value="openai_tts">OpenAI TTS (standard)</MenuItem>
              </Select>
            </FormControl>
            <Autocomplete
              size="small"
              sx={{ minWidth: 260, flex: 1 }}
              options={voices}
              value={selectedVoice}
              loading={voicesLoading}
              loadingText="Loading voices…"
              noOptionsText={voicesError ?? "No voices match"}
              onChange={(_e, newVoice) => {
                if (newVoice) setVoiceId(newVoice.voice_id)
              }}
              isOptionEqualToValue={(opt, val) => opt.voice_id === val.voice_id}
              getOptionLabel={(opt) => opt.name ?? opt.voice_id}
              filterOptions={(opts, { inputValue }) => {
                const q = inputValue.trim().toLowerCase()
                if (!q) return opts
                return opts.filter((o) =>
                  (o.name || "").toLowerCase().includes(q) ||
                  (o.locale || "").toLowerCase().includes(q) ||
                  (o.voice_id || "").toLowerCase().includes(q)
                )
              }}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Voice"
                  InputProps={{
                    ...params.InputProps,
                    endAdornment: (
                      <>
                        {voicesLoading ? <CircularProgress size={16} /> : null}
                        {params.InputProps.endAdornment}
                      </>
                    ),
                  }}
                />
              )}
              renderOption={(props, option) => {
                const isPlaying = previewVoice === option.voice_id
                const isLoading = previewLoading === option.voice_id
                const secondary = option.desc
                  ? option.desc
                  : [option.category, option.locale].filter(Boolean).join(" · ")
                const { onClick, onMouseDown, onTouchStart, ...liProps } = props
                return (
                  <Box component="li" {...liProps} sx={{ display: "flex", alignItems: "center", gap: 1, pr: 1, p: 0 }}>
                    <Box
                      onClick={onClick}
                      onMouseDown={onMouseDown}
                      onTouchStart={onTouchStart}
                      sx={{ flex: 1, minWidth: 0, cursor: "pointer", py: 1, pl: 2 }}
                    >
                      <Typography variant="body2" noWrap>{option.name ?? option.voice_id}</Typography>
                      {secondary && (
                        <Typography variant="caption" sx={{ color: "text.secondary" }} noWrap>
                          {secondary}
                        </Typography>
                      )}
                    </Box>
                    <Tooltip title={isPlaying ? "Stop preview" : "Preview voice"} placement="left" arrow>
                      <IconButton
                        size="small"
                        onClick={() => playPreview(option.voice_id)}
                        sx={{
                          p: 0.25,
                          color: isPlaying ? "primary.main" : "text.secondary",
                          "&:hover": { color: "primary.main" },
                        }}
                      >
                        {isLoading
                          ? <CircularProgress size={14} />
                          : isPlaying
                            ? <StopIcon sx={{ fontSize: 18 }} />
                            : <PlayArrowIcon sx={{ fontSize: 18 }} />}
                      </IconButton>
                    </Tooltip>
                  </Box>
                )
              }}
            />
          </Stack>

          {/* Voice instructions — OpenAI TTS only. Lets the user steer the
              delivery. Hidden for Edge TTS which doesn't support this field. */}
          {provider === "openai_tts" && (
            <TextField
              size="small"
              fullWidth
              value={voiceInstructions}
              onChange={(e) => setVoiceInstructions(e.target.value)}
              label="Voice instructions (optional)"
              placeholder="e.g. Speak slowly with emphasis on key words. Use a friendly, conversational tone."
              helperText="Steer how the OpenAI voice delivers the script. Leave blank for default."
            />
          )}

          {/* Optional video overlay */}
          <Box>
            <Typography variant="caption" sx={{ fontWeight: 600, display: "block" }}>
              Optional: overlay voice on a video
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 1 }}>
              The video's original audio will be replaced by the generated voice.
            </Typography>
            <input
              ref={videoInputRef}
              type="file"
              hidden
              accept=".mp4,.mov,.mkv,.webm,.m4v"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (!f) { setVideo(null); return }
                if (f.size > VIDEO_MAX_MB * 1024 * 1024) {
                  showSnackbar(`Video too large (max ${VIDEO_MAX_MB} MB)`, "error")
                  e.target.value = ""
                  return
                }
                setVideo(f)
              }}
            />
            {video ? (
              <GlassPanel sx={{ p: 1.5, display: "flex", alignItems: "center", gap: 1.5 }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>{video.name}</Typography>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {(video.size / 1024 / 1024).toFixed(1)} MB — output will be .mp4
                  </Typography>
                </Box>
                <IconButton size="small" onClick={() => setVideo(null)}><CloseIcon fontSize="small" /></IconButton>
              </GlassPanel>
            ) : (
              <Button
                variant="outlined"
                startIcon={<UploadFileOutlinedIcon />}
                onClick={() => videoInputRef.current?.click()}
                sx={{ textTransform: "none" }}
              >
                Choose video (optional)
              </Button>
            )}
          </Box>
        </Stack>
      }
    />
  )
}
