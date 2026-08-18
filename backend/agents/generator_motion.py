# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Motion Studio → Library helpers.

Shared helpers for landing a rendered Motion Studio MP4 into the Videos library:
a thumbnail (ffmpeg) and a `GeneratedVideo` row. Used by the studio's
render-import path (StudioService.import_renders) so a motion piece lands in
the Library, and can be exported and reused, exactly like every other
generation. The embedded studio owns rendering and export.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Pixel dimensions per aspect (for the authoring brief).
ASPECT_DIMS = {
    "9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
    "9:16-4k": (2160, 3840), "16:9-4k": (3840, 2160), "1:1-4k": (2160, 2160),
}

# The lengths a user can pick for a motion piece, in seconds. ONE list, so the
# UI, the authoring prompt and any future entry point cannot drift apart on
# what is offerable. MOTION_DURATION_MAX is what the prompts quote as the
# ceiling.
MOTION_DURATIONS = (5, 10, 15, 30)
MOTION_DURATION_MAX = max(MOTION_DURATIONS)


class MotionGeneratorAgent:
    """Thumbnail + GeneratedVideo helpers for Motion Studio renders."""

    @staticmethod
    async def _thumbnail(video_path: Path) -> Path | None:
        """First-second frame via the shared ffmpeg helper (returns None if
        ffmpeg is missing or the grab fails)."""
        from backend.services import ffmpeg_service
        return await ffmpeg_service.extract_thumbnail(video_path, timestamp=1.0)

    @staticmethod
    async def _save(*, user_id, title, variables, niche, video_path, thumb_path,
                    audio_path, aspect_ratio, duration_seconds,
                    script, caption_status) -> str:
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        gv = GeneratedVideo(
            user_id=user_id,
            title=title,
            # The narration script is the meaningful "script" when present;
            # otherwise stash the composition variables for reference.
            script=script or json.dumps(variables),
            niche=niche,
            video_path=str(video_path),
            audio_path=str(audio_path) if audio_path else None,
            thumbnail_path=str(thumb_path) if thumb_path else None,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            gen_tier="motion",
            source_type="motion_graphics",
            caption_status=caption_status,
            metadata_status=None,
            youtube_title=title,
            estimated_cost_usd=0.0,   # rendered locally
            status="ready",
        )
        async with AsyncSessionLocal() as db:
            db.add(gv)
            await db.commit()
            await db.refresh(gv)
            return gv.id
