# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Clipper audit batch: cancellation, honest failure, and cache hygiene.

Each case here pins a behaviour that a green suite could not see before:
a cancelled suggestion search that finished anyway and overwrote the user's
"cancelled" with "success"; a provider refusal that degraded into arbitrary
duration slices labelled as viral clips; a NaN timestamp reaching ffmpeg as
`-ss nan`; `t=inf` turning into an OverflowError 500; a page load with the
storage volume unmounted deleting every video row; a failed per-clip probe
guessing 9:16; and a half-written cache JPEG observable at its final path.
"""
import asyncio
import math
import subprocess
from pathlib import Path

import pytest

from backend.services import clip_extractor as ce
from backend.services import ffmpeg_service


def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")


# ── _parse_timestamp: non-finite input is a 400, not an ffmpeg failure ────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_parse_timestamp_rejects_non_finite_numbers(bad):
    """json.loads accepts bare NaN/Infinity; NaN fails every later bounds
    comparison silently and used to reach ffmpeg as `-ss nan`."""
    with pytest.raises(ValueError, match="non-finite|negative"):
        ce._parse_timestamp(bad)


def test_parse_timestamp_still_accepts_ordinary_numbers():
    assert ce._parse_timestamp(45) == 45.0
    assert ce._parse_timestamp(45.5) == 45.5
    assert ce._parse_timestamp("10:38") == 638.0


# ── Provider refusals propagate; only "found nothing" falls back ──────────

def test_provider_refusal_is_classified_by_sdk_class_name():
    class AuthenticationError(Exception):
        """Shape of openai.AuthenticationError / anthropic.AuthenticationError."""

    class SomethingTransient(Exception):
        pass

    from backend.core.exceptions import AIKeyMissingError, AIProviderError
    assert ce._is_provider_refusal(AuthenticationError("bad key"))
    assert ce._is_provider_refusal(AIProviderError("provider down"))
    assert ce._is_provider_refusal(AIKeyMissingError("no key"))
    assert not ce._is_provider_refusal(SomethingTransient("blip"))
    assert not ce._is_provider_refusal(ValueError("bad json"))


def test_selection_reraises_a_refusal_instead_of_returning_nothing(monkeypatch):
    """Returning [] here marched a bad key through the relax-retries into the
    duration-based fallback — arbitrary time slices presented as the AI's
    picks, with no word about why."""
    class RateLimitError(Exception):
        pass

    class _AI:
        async def chat(self, **kw):
            raise RateLimitError("429")

    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda *a, **k: _AI())
    segments = [{"start": 0, "end": 5, "text": "hello there"}]
    with pytest.raises(RateLimitError):
        asyncio.run(ce._select_clip_windows(segments, "T", 600, 3, None))


def test_selection_still_falls_back_on_a_malformed_answer(monkeypatch):
    """A transient failure (bad JSON, a stray exception) keeps the old
    behaviour: no windows, and the caller's fallback decides."""
    class _AI:
        async def chat(self, **kw):
            raise ValueError("not json")

    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda *a, **k: _AI())
    segments = [{"start": 0, "end": 5, "text": "hello there"}]
    assert asyncio.run(ce._select_clip_windows(segments, "T", 600, 3, None)) == []


# ── run_suggest_clips honours a cancel ────────────────────────────────────

def _drive_suggest(monkeypatch, *, cancelled_after_transcript: bool):
    from backend.core import task_runner as tr
    from backend.services.clip_options import ExtractOptions

    writes: list[str] = []
    selection_calls = {"n": 0}

    class _Row:
        id = "vid-1"
        title = "A podcast"
        duration_seconds = 900

    class _Result:
        def __init__(self, v): self._v = v
        def scalar_one_or_none(self): return self._v

    class _DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def execute(self, *a, **k): return _Result(_Row())

    async def fake_update(job_id, status, **kw):
        writes.append(status)

    async def fake_segments(*a, **k):
        return [{"start": 0, "end": 5, "text": "words"}]

    async def fake_select(*a, **k):
        selection_calls["n"] += 1
        return [{"start": 10, "end": 40, "title": "t", "virality_score": 8}]

    async def fake_cancelled(job_id):
        return cancelled_after_transcript

    monkeypatch.setattr("backend.database.AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr("backend.agents.job_helper.update_job_status", fake_update)
    monkeypatch.setattr("backend.agents.job_helper.job_cancelled", fake_cancelled)
    monkeypatch.setattr(ce, "_load_or_transcribe_segments", fake_segments)
    monkeypatch.setattr(ce, "_select_clip_windows_with_retries", fake_select)

    asyncio.run(tr.run_suggest_clips(
        job_id="job-1234-abcd", downloaded_video_id="vid-1",
        opts=ExtractOptions(mode="ai", max_clips=3)))
    return writes, selection_calls["n"]


def test_a_cancelled_suggestion_never_makes_the_selection_call(monkeypatch):
    """Nothing interrupts the coroutine, so before the gates a cancel during
    Whisper let the runner make the selection call anyway and then write
    "success" over the user's "cancelled"."""
    writes, calls = _drive_suggest(monkeypatch, cancelled_after_transcript=True)
    assert calls == 0
    assert writes[-1] == "cancelled"
    assert "success" not in writes and "failed" not in writes


def test_an_uncancelled_suggestion_still_succeeds(monkeypatch):
    writes, calls = _drive_suggest(monkeypatch, cancelled_after_transcript=False)
    assert calls == 1
    assert writes[-1] == "success"


# ── A failed per-clip probe stays unknown, never "9:16" ───────────────────

def test_aspect_from_dims_default_none_is_honoured():
    from backend.services.video_utils import aspect_from_dims
    assert aspect_from_dims(0, 0, default=None) is None
    assert aspect_from_dims(1920, 1080, default=None) == "16:9"


# ── /frame: t=inf is a 422, not an OverflowError ──────────────────────────

@pytest.fixture
def client():
    from starlette.testclient import TestClient

    from backend.main import app
    with TestClient(app) as c:
        yield c


async def _seed_source(video_path, duration=None):
    from backend.database import AsyncSessionLocal
    from backend.models.downloaded_video import DownloadedVideo
    async with AsyncSessionLocal() as db:
        v = DownloadedVideo(
            user_id="local", title="audit source", platform="youtube",
            video_path=str(video_path), duration_seconds=duration,
        )
        db.add(v)
        await db.commit()
        return v.id


def test_frame_rejects_an_infinite_t(client, tmp_path):
    """Query(ge=0.0) passes inf, and with no duration the tail clamp is
    skipped — int(round(inf * 10)) was a naked 500."""
    src = tmp_path / "s.mp4"
    src.write_bytes(b"\x00" * 64)
    vid = asyncio.run(_seed_source(src, duration=None))
    assert client.get(f"/api/downloaded/{vid}/frame?t=inf&w=128").status_code == 422
    assert client.get(f"/api/downloaded/{vid}/frame?t=nan&w=128").status_code == 422


# ── The prune must not run with the storage volume absent ─────────────────

def test_prune_is_skipped_when_the_storage_root_is_missing(tmp_path, monkeypatch):
    """With the volume unmounted every row's is_file() is False; one page
    load used to delete the whole video table and its sibling files."""
    from datetime import datetime, timezone

    from backend.api.videos import list_videos
    from backend.config import settings
    from backend.database import AsyncSessionLocal, init_db
    from backend.models.generated_video import GeneratedVideo
    from sqlalchemy import delete, select

    marker = "__test_missing_volume__"
    asyncio.run(init_db())

    async def seed():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(GeneratedVideo).where(GeneratedVideo.source_type == marker))
            db.add(GeneratedVideo(
                id=f"{marker}-1", user_id="local", title="orphan", status="ready",
                aspect_ratio="9:16", video_path=str(tmp_path / "gone" / "v.mp4"),
                source_type=marker,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            await db.commit()

    async def count():
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(GeneratedVideo).where(GeneratedVideo.source_type == marker))).scalars().all()
            return len(rows)

    async def cleanup():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(GeneratedVideo).where(GeneratedVideo.source_type == marker))
            await db.commit()

    asyncio.run(seed())
    try:
        # Simulate the unmounted volume: STORAGE_ROOT points somewhere absent.
        monkeypatch.setattr(type(settings), "STORAGE_ROOT",
                            property(lambda self: tmp_path / "unmounted"))
        assert not settings.STORAGE_ROOT.exists()

        async def go():
            async with AsyncSessionLocal() as db:
                return await list_videos(db=db, status=None, source_type=marker,
                                         limit=20, offset=0)
        out = asyncio.run(go())
        # The row is neither pruned nor hidden: it renders as it is until
        # storage is back.
        assert asyncio.run(count()) == 1
        assert [v["id"] for v in out["videos"]] == [f"{marker}-1"]
    finally:
        asyncio.run(cleanup())


# ── Cache JPEGs are written temp-then-replace ─────────────────────────────

@needs_ffmpeg
def test_extract_frame_at_leaves_no_temp_sibling_on_failure(tmp_path):
    """The frame is served as an immutable cache entry: a truncated file at
    the final path would be pinned in the browser. On failure nothing may
    exist at the final path, and no temp sibling may be left behind."""
    src = tmp_path / "not-a-video.mp4"
    src.write_bytes(b"\x00" * 128)
    out = tmp_path / "cache" / "f.jpg"
    got = asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=src, timestamp=0.0, output_path=out))
    assert got is None
    assert not out.exists()
    assert list(out.parent.glob("*")) == []


@needs_ffmpeg
def test_extract_frame_at_success_leaves_exactly_the_final_file(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:d=2",
         "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, timeout=60,
    )
    out = tmp_path / "cache" / "f.jpg"
    got = asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=src, timestamp=1.0, output_path=out))
    assert got == out and out.stat().st_size > 0
    assert [p.name for p in out.parent.glob("*")] == ["f.jpg"]


@needs_ffmpeg
def test_filmstrip_success_leaves_exactly_the_final_file(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:d=4",
         "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, timeout=60,
    )
    out = tmp_path / "strips" / "s.jpg"
    got = asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=src, output_path=out, count=4, tile_height=32, duration=4.0))
    assert got == out and out.stat().st_size > 0
    assert [p.name for p in out.parent.glob("*")] == ["s.jpg"]


@needs_ffmpeg
def test_extract_thumbnail_refuses_a_corrupt_source_and_leaves_nothing(tmp_path):
    src = tmp_path / "corrupt.mp4"
    src.write_bytes(b"\x00" * 128)
    out = tmp_path / "thumbs" / "t.jpg"
    got = asyncio.run(ffmpeg_service.extract_thumbnail(src, output_path=out, timestamp=0.0))
    assert got is None
    assert not out.exists()
    assert list(out.parent.glob("*")) == []


@needs_ffmpeg
def test_extract_thumbnail_success_leaves_exactly_the_final_file(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:d=3",
         "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, timeout=60,
    )
    out = tmp_path / "thumbs" / "t.jpg"
    got = asyncio.run(ffmpeg_service.extract_thumbnail(src, output_path=out, timestamp=1.0))
    assert got == out and out.stat().st_size > 0
    assert [p.name for p in out.parent.glob("*")] == ["t.jpg"]


# ── probe_media reads its three fields independently ─────────────────────

def _fake_ffprobe(stdout: str):
    class _R:
        def __init__(self): self.stdout = stdout; self.returncode = 0
    return lambda *a, **k: _R()


def test_probe_media_keeps_dimensions_when_duration_is_na(monkeypatch):
    """ffprobe prints a literal N/A for a container with no duration;
    float("N/A") used to throw the dimensions away with it — and a
    1920x1080 file with zero dimensions is labelled 9:16 downstream."""
    from backend.services import video_utils
    monkeypatch.setattr(video_utils.subprocess, "run", _fake_ffprobe("1920\n1080\nN/A\n"))
    assert video_utils.probe_media("x.mp4") == (1920, 1080, 0.0)
    assert video_utils.aspect_label("x.mp4") == "16:9"


def test_probe_media_zeroes_a_non_finite_or_negative_duration(monkeypatch):
    from backend.services import video_utils
    monkeypatch.setattr(video_utils.subprocess, "run", _fake_ffprobe("640\n360\ninf\n"))
    assert video_utils.probe_media("x.mp4") == (640, 360, 0.0)
    monkeypatch.setattr(video_utils.subprocess, "run", _fake_ffprobe("640\n360\n-3\n"))
    assert video_utils.probe_media("x.mp4") == (640, 360, 0.0)


def test_probe_media_missing_fields_are_zero_not_an_exception(monkeypatch):
    from backend.services import video_utils
    monkeypatch.setattr(video_utils.subprocess, "run", _fake_ffprobe("1280\n"))
    assert video_utils.probe_media("x.mp4") == (1280, 0, 0.0)


def test_math_isfinite_is_the_guard_not_float():
    """`float("-inf")` parses — the trap this repo has hit before."""
    assert not math.isfinite(float("-inf"))
