# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""TTS voice preview endpoint.

Generates a short (~10s) speech sample on demand so a voice can be auditioned
before it narrates a whole video. Both voice pickers have always POSTed here;
the route did not exist, so every preview click answered 404 and the page
showed "Preview failed" with nothing a user could do about it.

Sample text rotates, so repeat plays of the same voice sound different — that
is the point of a preview. Edge TTS is free, so nothing here costs anything;
OpenAI TTS spends the user's own key (BYOK) on ~150 characters, and a missing
key is reported as such rather than as a crash.
"""
import logging
import random
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings
from backend.core.exceptions import VoiceGenerationError

logger = logging.getLogger(__name__)
router = APIRouter()


# ~12-25 words each (~6-12s spoken). A mix of declarative, conversational and
# hook-style phrasing, so the listener hears how a voice handles prosody rather
# than one sentence it happens to be good at.
_SAMPLES = [
    "Hi there. This is a quick preview of how I sound. Use me to narrate your videos with clear, natural speech.",
    "Welcome back to the channel. Today we're diving into something I've been excited about for weeks.",
    "Imagine waking up and seeing thousands of new subscribers. That's the kind of growth we're chasing.",
    "In just sixty seconds, you'll know exactly how to ten-x your engagement on every short you post.",
    "Here's a quick reminder: your viewers don't care how good your camera is. They care if you keep them watching.",
    "Most people think you need fancy gear to make great content. The truth might surprise you.",
    "Let's talk about something nobody tells beginners. The algorithm doesn't reward perfect; it rewards consistent.",
    "Stop scrolling for a second. What I'm about to share might change how you think about your channel forever.",
]


# Every click writes a fresh mp3 (the sample text is fresh, so caching would
# defeat the point). Left alone that is an unbounded scratch tree with no owner
# and no sweeper — the same shape as the bench's frame cache before it got one.
# TMP_DIR itself must never be blanket-purged (it holds yt-dlp's cookie jar and
# uploaded media), so this prunes its OWN directory and nothing else.
_KEEP_PREVIEWS = 24


def _prune_previews(preview_dir: Path) -> None:
    """Keep the newest few samples; drop the rest. Never raises."""
    try:
        files = sorted(preview_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True)
        for stale in files[_KEEP_PREVIEWS:]:
            stale.unlink(missing_ok=True)
    except OSError as e:
        logger.debug("preview prune skipped: %s", e)


class _PreviewBody(BaseModel):
    provider: str          # "edge_tts" | "openai_tts"
    voice_id: str


@router.post("/tts/preview")
async def tts_preview(body: _PreviewBody):
    """Generate a short speech sample in the requested voice. Returns mp3."""
    provider = (body.provider or "").strip()
    # voice_id lands in a cache FILENAME, so strip anything that isn't a plain
    # token — a path separator here would be a traversal via the request body.
    voice_id = re.sub(r"[^A-Za-z0-9_-]", "", (body.voice_id or "").strip())
    if not voice_id:
        raise HTTPException(400, "voice_id is required")
    if provider not in ("edge_tts", "openai_tts"):
        raise HTTPException(400, f"Unknown provider: {provider}")

    sample_text = random.choice(_SAMPLES)
    preview_dir = settings.TMP_DIR / "tts_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    output_path: Path = preview_dir / f"{provider}_{voice_id}_{uuid4().hex[:8]}.mp3"
    _prune_previews(preview_dir)

    try:
        if provider == "edge_tts":
            from backend.services import edge_tts_service
            await edge_tts_service.generate_voice(
                sample_text, voice_id=voice_id, output_path=output_path,
            )
        else:
            from backend.services import openai_tts_service
            # Same key the Smart Video pipeline reads for this provider
            # (generator._generate_voice) — one answer to "where does the
            # OpenAI TTS key come from", not two.
            api_key = settings.OPENAI_API_KEY
            if not api_key:
                raise HTTPException(400, "Add your OpenAI API key in Settings to preview this voice")
            await openai_tts_service.generate_voice(
                sample_text, voice_id=voice_id, api_key=api_key, output_path=output_path,
            )
    except HTTPException:
        # Don't re-wrap our own structured errors — re-wrapping turns a clear
        # 400 into a confusing "Voice preview crashed: 400: …" 500.
        raise
    except (VoiceGenerationError, ValueError) as e:
        # ValueError is what edge_tts raises for a voice id it doesn't know,
        # which is exactly what a drifted picker list produces.
        logger.warning("TTS preview failed: provider=%s voice=%s err=%s", provider, voice_id, e)
        raise HTTPException(502, f"Voice preview failed: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("TTS preview crashed: provider=%s voice=%s", provider, voice_id)
        raise HTTPException(500, f"Voice preview crashed: {e}")

    return FileResponse(
        output_path,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",       # each call is fresh sample text
            "X-Sample-Text": sample_text[:140],
        },
    )

