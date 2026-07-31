import { Typography } from "@mui/material"
import GraphicEqOutlinedIcon from "@mui/icons-material/GraphicEqOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import { MEDIA_ACCEPT } from "../../components/tools/mediaAccept"

export default function ToolAudioEnhance() {
  useDocumentTitle("Enhance Audio")
  return (
    <ToolRunner
      title="Enhance Audio"
      description="Denoise hiss, normalize loudness, polish speech"
      icon={<GraphicEqOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/audio-enhance"
      acceptExts={MEDIA_ACCEPT}
    >
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        Applies noise reduction, a high-pass / low-pass trim for rumble and
        hiss, and EBU R128 loudness normalization to -16 LUFS. Takes a video or
        a bare audio file (audio in, mp3 out). No configuration needed — the
        defaults work for 99% of talking-head content.
      </Typography>
    </ToolRunner>
  )
}
