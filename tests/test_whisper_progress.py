# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Streaming-progress + serialization tests for whisper_service.transcribe.

Ported from the hosted variant after a real "two parallel Whisper jobs look stuck" report:

  1. transcribe() consumed the whole faster-whisper segment generator with
     list(), so a 40-minute audio showed a FROZEN progress bar for the entire
     15–30 min transcription. It now streams the generator and fires an
     optional on_progress(fraction) as the decode advances.
  2. Two concurrent transcriptions queued invisibly inside ctranslate2
     (num_workers=1) while both calling threads burned CPU. An explicit
     class-level gate now serializes the compute observably.

The model is faked; no audio is decoded.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from backend.services.whisper_service import WhisperService, whisper_service


def _seg(start, end, text="hi"):
    return SimpleNamespace(start=start, end=end, text=text, words=[])


class _FakeModel:
    """Yields segments with a controllable delay so throttling and
    concurrency are testable."""

    def __init__(self, n_segments=5, seg_seconds=60.0, delay=0.0):
        self.n = n_segments
        self.seg_seconds = seg_seconds
        self.delay = delay

    def transcribe(self, path, **kwargs):
        def gen():
            for i in range(self.n):
                if self.delay:
                    time.sleep(self.delay)
                yield _seg(i * self.seg_seconds, (i + 1) * self.seg_seconds)
        info = SimpleNamespace(language="en", language_probability=0.9)
        return gen(), info


@pytest.fixture
def fake_whisper(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 2048)
    monkeypatch.setattr(WhisperService, "load", classmethod(lambda cls, q="balanced": _FakeModel()))

    async def _has_audio(p):
        return True
    import backend.services.ffmpeg_service as ff
    monkeypatch.setattr(ff, "has_audio_stream", _has_audio)
    # 5 segments × 60s = 300s total
    import backend.services.video_utils as vu
    monkeypatch.setattr(vu, "probe_duration", lambda p: 300.0)
    return audio


@pytest.mark.asyncio
async def test_on_progress_streams_fractions(fake_whisper, monkeypatch):
    # Zero the emission throttle so every segment reports (delay-free fake).
    # NEVER patch time.monotonic for this — asyncio's loop clock uses it too.
    import backend.services.whisper_service as W
    monkeypatch.setattr(W, "_PROGRESS_EMIT_INTERVAL", 0.0)
    monkeypatch.setattr(WhisperService, "load",
                        classmethod(lambda cls, q="balanced": _FakeModel(n_segments=5)))
    fractions = []

    result = await whisper_service.transcribe(
        str(fake_whisper), on_progress=fractions.append)

    assert result["language"] == "en"
    assert len(result["segments"]) == 5
    assert fractions, "on_progress must fire during streaming"
    assert fractions == sorted(fractions), "fractions must be monotonic"
    assert fractions[-1] == pytest.approx(0.99), "fraction caps at 0.99 (300/300 clamped)"


@pytest.mark.asyncio
async def test_no_callback_still_transcribes(fake_whisper, monkeypatch):
    """Without on_progress the result shape is unchanged (the duration probe
    still runs — it also drives the batch-gate decision), and a probe FAILURE
    (0.0) must not break transcription."""
    import backend.services.video_utils as vu
    monkeypatch.setattr(vu, "probe_duration", lambda p: 0.0)

    result = await whisper_service.transcribe(str(fake_whisper))
    assert len(result["segments"]) == 5


@pytest.mark.asyncio
async def test_progress_callback_errors_are_swallowed(fake_whisper, monkeypatch):
    import backend.services.whisper_service as W
    monkeypatch.setattr(W, "_PROGRESS_EMIT_INTERVAL", 0.0)

    def bad_callback(frac):
        raise RuntimeError("UI exploded")

    result = await whisper_service.transcribe(
        str(fake_whisper), on_progress=bad_callback)
    assert len(result["segments"]) == 5, "a broken progress UI must not kill transcription"


@pytest.mark.asyncio
async def test_concurrent_transcriptions_serialize(fake_whisper, monkeypatch):
    """Two overlapping transcribe() calls must not run their decode loops
    concurrently — the gate serializes them."""
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    class _TrackedModel(_FakeModel):
        def transcribe(self, path, **kwargs):
            def gen():
                with lock:
                    active["now"] += 1
                    active["max"] = max(active["max"], active["now"])
                try:
                    for i in range(3):
                        time.sleep(0.05)
                        yield _seg(i * 60.0, (i + 1) * 60.0)
                finally:
                    with lock:
                        active["now"] -= 1
            info = SimpleNamespace(language="en", language_probability=0.9)
            return gen(), info

    monkeypatch.setattr(WhisperService, "load",
                        classmethod(lambda cls, q="balanced": _TrackedModel()))

    await asyncio.gather(
        whisper_service.transcribe(str(fake_whisper)),
        whisper_service.transcribe(str(fake_whisper)),
    )
    assert active["max"] == 1, "decode loops must be serialized by the gate"


@pytest.mark.asyncio
async def test_short_audio_bypasses_the_gate(fake_whisper, monkeypatch):
    """MONEY-PATH GUARD: short interactive transcriptions (Smart Video / brainrot caption
    timing on ~1-min TTS audio) must NEVER queue behind a long batch
    transcription. Audio under _SERIALIZE_MIN_SECONDS runs concurrently —
    the pre-review gate was unconditional and would have stalled paid
    features for tens of minutes behind an unrelated analyze job."""
    import backend.services.video_utils as vu
    monkeypatch.setattr(vu, "probe_duration", lambda p: 45.0)  # < 120s

    started = {"n": 0, "max": 0}
    lock = threading.Lock()
    release = threading.Event()

    class _BlockingModel(_FakeModel):
        def transcribe(self, path, **kwargs):
            def gen():
                with lock:
                    started["n"] += 1
                    started["max"] = max(started["max"], started["n"])
                # Hold both decodes open until BOTH have started — proves
                # neither waited on the other.
                release.wait(timeout=5)
                yield _seg(0, 45.0)
                with lock:
                    started["n"] -= 1
            info = SimpleNamespace(language="en", language_probability=0.9)
            return gen(), info

    monkeypatch.setattr(WhisperService, "load",
                        classmethod(lambda cls, q="balanced": _BlockingModel()))

    async def _watchdog():
        # Release the decoders once both are in flight (or time out the test).
        for _ in range(100):
            with lock:
                if started["max"] >= 2:
                    release.set()
                    return
            await asyncio.sleep(0.05)
        release.set()

    await asyncio.gather(
        whisper_service.transcribe(str(fake_whisper)),
        whisper_service.transcribe(str(fake_whisper)),
        _watchdog(),
    )
    assert started["max"] == 2, \
        "short transcriptions must run concurrently (no head-of-line blocking)"


@pytest.mark.asyncio
async def test_none_segment_end_does_not_regress_progress(fake_whisper, monkeypatch):
    """A segment with end=None maps to fraction 0.0 — the monotonic guard
    must swallow it instead of walking the bar backwards."""
    import backend.services.whisper_service as W
    monkeypatch.setattr(W, "_PROGRESS_EMIT_INTERVAL", 0.0)

    class _NoneEndModel(_FakeModel):
        def transcribe(self, path, **kwargs):
            def gen():
                yield _seg(0, 60.0)
                yield SimpleNamespace(start=60.0, end=None, text="x", words=[])
                yield _seg(120.0, 180.0)
            info = SimpleNamespace(language="en", language_probability=0.9)
            return gen(), info

    monkeypatch.setattr(WhisperService, "load",
                        classmethod(lambda cls, q="balanced": _NoneEndModel()))
    fractions = []
    await whisper_service.transcribe(str(fake_whisper), on_progress=fractions.append)
    assert fractions == sorted(fractions), "no backwards progress"
    assert 0.0 not in fractions, "the None-end segment must not emit 0.0"
