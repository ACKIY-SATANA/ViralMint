# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Singleton faster-whisper transcription service."""
import asyncio
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_QUALITY_MAP = {
    "fast":     "base",
    "balanced": "small",
    "accurate": "medium",
    "best":     "large-v3",
}

# Approximate download sizes for first-time use
WHISPER_MODEL_SIZES = {
    "base":     "~150 MB",
    "small":    "~500 MB",
    "medium":   "~1.5 GB",
    "large-v3": "~3 GB",
}


# Models heavy enough that keeping them resident after a one-off job is a real
# memory cost (~1.5 GB / ~3 GB RSS). These get evicted after an idle period;
# base/small are cheap enough to keep hot for the process lifetime.
_HEAVY_MODELS = {"medium", "large-v3"}
_IDLE_EVICT_SECONDS = 600  # 10 min


class WhisperService:
    _model = None
    _loaded_quality = None
    _lock = threading.Lock()
    _last_used = 0.0
    _evict_task = None

    @classmethod
    def _hub_dir(cls) -> Path:
        """Resolve the HuggingFace hub cache directory.

        MUST honour ``HF_HOME``: it's the standard way to relocate the model
        cache, and anyone self-hosting on a machine where ``$HOME`` is small or
        ephemeral will have set it. Hardcoding ``~/.cache/huggingface`` made
        ``is_model_cached()`` read a tree that faster-whisper never writes to in
        that setup, so freshly-downloaded models were reported as missing and a
        second full copy got downloaded.
        """
        hf_home = os.environ.get("HF_HOME")
        base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
        return base / "hub"

    @classmethod
    def is_model_cached(cls, quality: str = "balanced") -> bool:
        """Check if the Whisper model is already downloaded in the HF cache."""
        model_name = WHISPER_QUALITY_MAP.get(quality, "small")
        model_dir = cls._hub_dir() / f"models--Systran--faster-whisper-{model_name}"
        if not model_dir.exists():
            return False
        # Check for .incomplete files — model is still downloading
        blobs = model_dir / "blobs"
        if blobs.exists():
            for f in blobs.iterdir():
                if f.suffix == ".incomplete":
                    return False
        return True

    @classmethod
    def load(cls, quality: str = "balanced"):
        with cls._lock:
            if cls._model is None or cls._loaded_quality != quality:
                from faster_whisper import WhisperModel
                model_name = WHISPER_QUALITY_MAP.get(quality, "small")
                logger.info(f"Loading Whisper model: {model_name}")
                cls._model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                )
                cls._loaded_quality = quality
                logger.info(f"Whisper model '{model_name}' loaded")
            cls._last_used = time.monotonic()
            return cls._model

    @classmethod
    def unload(cls) -> bool:
        """Drop the resident model. Returns True if something was freed."""
        with cls._lock:
            if cls._model is None:
                return False
            freed = cls._loaded_quality
            cls._model = None
            cls._loaded_quality = None
        import gc
        gc.collect()
        logger.info("Whisper model '%s' evicted (idle)", freed)
        return True

    @classmethod
    async def _evict_when_idle(cls):
        """Free a heavy model once it has gone unused for _IDLE_EVICT_SECONDS.

        Without this, a single "best"-quality analyze pins ~3 GB of RSS for the
        lifetime of the process — and the app sits in the tray holding it.
        """
        try:
            while True:
                with cls._lock:
                    quality = cls._loaded_quality
                    last = cls._last_used
                if quality is None:
                    return
                if WHISPER_QUALITY_MAP.get(quality) not in _HEAVY_MODELS:
                    return
                idle = time.monotonic() - last
                if idle >= _IDLE_EVICT_SECONDS:
                    cls.unload()
                    return
                await asyncio.sleep(min(60, _IDLE_EVICT_SECONDS - idle + 1))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the sweeper kill anything
            logger.debug("Whisper idle-evict sweeper stopped: %s", e)

    @classmethod
    def _arm_evictor(cls):
        if WHISPER_QUALITY_MAP.get(cls._loaded_quality) not in _HEAVY_MODELS:
            return
        if cls._evict_task is not None and not cls._evict_task.done():
            return
        try:
            cls._evict_task = asyncio.get_running_loop().create_task(cls._evict_when_idle())
        except RuntimeError:
            pass  # no running loop (sync context) — skip, not worth failing over

    async def transcribe(
        self,
        audio_path: str | Path,
        language: str = None,
        quality: str = "balanced",
        timing_only: bool = False,
    ) -> dict:
        """
        Transcribe audio file. Returns:
        { "text": "...", "language": "en", "segments": [...] }

        `quality` is explicit on purpose. It used to be implicit — transcribe()
        called load() with no argument, which meant two things went wrong at
        once: a caller that had deliberately pre-loaded a bigger model (the
        analyzer does, for its `whisper_quality` setting) had it immediately
        thrown away and RE-loaded as "small", silently ignoring the user's
        choice and paying for the load twice; and conversely, whatever model an
        unrelated earlier job left resident could be reused by a later one.

        `timing_only=True` selects a greedy + VAD decode. Use it when the caller
        only needs word timings for caption alignment over clean, single-speaker
        audio (e.g. our own TTS output) rather than a publishable transcript —
        it is substantially faster on CPU for a negligible accuracy cost on that
        kind of input. Leave it False for anything the user will read.
        """
        # Pre-check: confirm the file actually has an audio stream before
        # invoking whisper. Without this, faster-whisper crashes with a
        # cryptic "tuple index out of range" when given a silent file
        # (e.g. video-only mp4 from a downloader that missed the audio
        # track).
        from backend.services.ffmpeg_service import has_audio_stream
        if not await has_audio_stream(Path(audio_path)):
            raise ValueError(f"No audio stream found in {audio_path}")

        model = self.load(quality)

        def _run():
            segments_gen, info = model.transcribe(
                str(audio_path),
                beam_size=1 if timing_only else 5,
                vad_filter=timing_only,
                language=language,
                word_timestamps=True,
            )
            segments = list(segments_gen)
            text = " ".join([s.text.strip() for s in segments])
            return {
                "text": text,
                "language": info.language,
                "language_probability": info.language_probability,
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text.strip(),
                        "words": [
                            {"word": w.word, "start": float(w.start), "end": float(w.end), "probability": float(w.probability)}
                            for w in (s.words or [])
                        ],
                    }
                    for s in segments
                ],
            }

        try:
            return await asyncio.to_thread(_run)
        finally:
            type(self)._last_used = time.monotonic()
            type(self)._arm_evictor()


whisper_service = WhisperService()
