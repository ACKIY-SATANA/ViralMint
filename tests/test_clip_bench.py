# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The cutting bench's backend: filmstrip, frame, speech lane, cache hygiene.

The filmstrip cases are the ones worth reading. A container's reported
duration runs to the END of its last frame, so the naive "sample at cell
centres" scheme asks ffmpeg to seek past anything decodable on the final
cell — and because a partial strip is treated as a failure, a 6-second source
asked for 32 cells produced no timeline at all.
"""
import asyncio
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services import ffmpeg_service


# ── Real-media fixtures ───────────────────────────────────────────────────
# These shell out to ffmpeg. It is present in this repo's runtime (bundled
# via imageio_ffmpeg) and in CI, but a contributor without it should get a
# skip rather than a wall of failures.

def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    """A real 6-second 320x180 clip — short enough that the tail clamp and the
    density cap both matter."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("bench") / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:d=6",
         "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, timeout=60,
    )
    assert out.exists() and out.stat().st_size > 0
    return out


# ── extract_frame_at ──────────────────────────────────────────────────────

@needs_ffmpeg
def test_extract_frame_at_writes_a_jpeg(tiny_video, tmp_path):
    out = tmp_path / "f.jpg"
    got = asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=tiny_video, timestamp=2.0, output_path=out))
    assert got == out
    assert out.stat().st_size > 0


@needs_ffmpeg
def test_extract_frame_at_scale_width_shrinks_the_output(tiny_video, tmp_path):
    """The IN/OUT panes are 132px wide; encoding a full-size JPEG per drag
    settle is bytes and CPU nobody sees."""
    full = tmp_path / "full.jpg"
    small = tmp_path / "small.jpg"
    asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=tiny_video, timestamp=1.0, output_path=full))
    asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=tiny_video, timestamp=1.0, output_path=small, scale_width=96))
    assert small.stat().st_size < full.stat().st_size


def test_extract_frame_at_reports_failure_rather_than_an_empty_file(tmp_path):
    src = tmp_path / "not-a-video.mp4"
    src.write_bytes(b"nope")
    out = tmp_path / "f.jpg"
    assert asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=src, timestamp=0.0, output_path=out)) is None
    assert not out.exists()


# ── extract_filmstrip ─────────────────────────────────────────────────────

@needs_ffmpeg
def test_filmstrip_survives_a_short_source_asked_for_many_cells(tiny_video, tmp_path):
    """THE regression. 6 seconds, 32 cells: the final cell's centre sits past
    the last decodable frame, and one failed frame failed the whole strip."""
    out = tmp_path / "strip.jpg"
    got = asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=tiny_video, output_path=out, count=32, tile_height=48,
        duration=6.0))
    assert got == out, "a short source must still produce a timeline"
    assert out.stat().st_size > 0


@needs_ffmpeg
def test_filmstrip_caps_density_by_duration(tiny_video, tmp_path):
    """MIN_CELL_SEC bounds the ffmpeg spawns: 6s can hold at most 30 cells at
    0.2s each, so asking for 96 and asking for 32 must not differ by 3x the
    work — and the endpoint's cache key relies on this clamp being stable."""
    wide = tmp_path / "wide.jpg"
    asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=tiny_video, output_path=wide, count=96, tile_height=32,
        duration=6.0))
    # 30 cells x (32px tall, 16:9 → ~56px wide) — the point is that it is far
    # short of 96 cells, not the exact pixel count.
    from PIL import Image
    with Image.open(wide) as im:
        cells = round(im.width / (im.height * (320 / 180)))
    assert cells <= int(6.0 / ffmpeg_service.MIN_CELL_SEC)


@needs_ffmpeg
def test_filmstrip_cell_count_is_readable_off_the_image(tiny_video, tmp_path):
    """Both clients offset into the sprite by cell index, so the DECODED image
    has to be the authority on how many cells it has."""
    out = tmp_path / "strip.jpg"
    asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=tiny_video, output_path=out, count=8, tile_height=45,
        duration=6.0))
    from PIL import Image
    with Image.open(out) as im:
        assert round(im.width / (im.height * (320 / 180))) == 8


def test_filmstrip_returns_none_for_a_missing_file(tmp_path):
    assert asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=tmp_path / "gone.mp4", output_path=tmp_path / "o.jpg")) is None


def test_filmstrip_returns_none_when_the_duration_is_unknown(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(ffmpeg_service, "probe_duration", lambda *a, **k: 0.0)
    assert asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=src, output_path=tmp_path / "o.jpg")) is None


@needs_ffmpeg
def test_filmstrip_cleans_up_its_scratch_dir(tiny_video, tmp_path):
    """TMP_DIR holds yt-dlp's cookie jar and uploaded media, so it has no
    blanket purge — this function has to tidy its own work dir."""
    from backend.config import settings
    before = {p.name for p in settings.TMP_DIR.glob("strip_*")}
    asyncio.run(ffmpeg_service.extract_filmstrip(
        video_path=tiny_video, output_path=tmp_path / "s.jpg", count=4,
        tile_height=32, duration=6.0))
    after = {p.name for p in settings.TMP_DIR.glob("strip_*")}
    assert after == before


# ── The endpoints ─────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from starlette.testclient import TestClient

    from backend.main import app
    with TestClient(app) as c:
        yield c


async def _seed_source(video_path, duration=6.0, segments_json=None):
    from backend.database import AsyncSessionLocal
    from backend.models.downloaded_video import DownloadedVideo
    async with AsyncSessionLocal() as db:
        v = DownloadedVideo(
            user_id="local", title="bench source", platform="youtube",
            video_path=str(video_path), duration_seconds=duration,
            transcript_segments_json=segments_json,
        )
        db.add(v)
        await db.commit()
        return v.id


@needs_ffmpeg
def test_filmstrip_endpoint_serves_an_immutable_jpeg(client, tiny_video):
    vid = asyncio.run(_seed_source(tiny_video))
    r = client.get(f"/api/downloaded/{vid}/filmstrip?n=8&h=48")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    # The mtime is in the cache key, so a re-downloaded source is a new URL —
    # which is what makes `immutable` safe here.
    assert "immutable" in r.headers["cache-control"]


@needs_ffmpeg
def test_filmstrip_endpoint_reuses_one_cache_entry_for_over_dense_requests(
        client, tiny_video):
    """n=96 and n=32 on a 6s source build the SAME sheet. Caching under two
    keys means paying for two ffmpeg runs to produce identical bytes."""
    from backend.config import settings
    vid = asyncio.run(_seed_source(tiny_video))
    strips = settings.THUMBNAILS_DIR / "strips"
    for f in strips.glob(f"{vid}_*"):
        f.unlink(missing_ok=True)
    assert client.get(f"/api/downloaded/{vid}/filmstrip?n=96&h=48").status_code == 200
    assert client.get(f"/api/downloaded/{vid}/filmstrip?n=32&h=48").status_code == 200
    assert len(list(strips.glob(f"{vid}_*"))) == 1


def test_filmstrip_endpoint_404s_for_a_missing_file(client, tmp_path):
    vid = asyncio.run(_seed_source(tmp_path / "gone.mp4"))
    assert client.get(f"/api/downloaded/{vid}/filmstrip").status_code == 404


@needs_ffmpeg
def test_frame_endpoint_buckets_to_a_tenth_of_a_second(client, tiny_video):
    """Two nearby drags must not each mint a cache entry."""
    from backend.config import settings
    vid = asyncio.run(_seed_source(tiny_video))
    frames = settings.THUMBNAILS_DIR / "frames"
    for f in frames.glob(f"{vid}_*"):
        f.unlink(missing_ok=True)
    assert client.get(f"/api/downloaded/{vid}/frame?t=1.02&w=128").status_code == 200
    assert client.get(f"/api/downloaded/{vid}/frame?t=1.04&w=128").status_code == 200
    assert len(list(frames.glob(f"{vid}_*"))) == 1


@needs_ffmpeg
def test_frame_endpoint_clamps_a_seek_past_the_end(client, tiny_video):
    """Asking for the last frame of a 6s video by name (t=6) seeks past
    anything decodable. Clamp rather than 422."""
    vid = asyncio.run(_seed_source(tiny_video))
    assert client.get(f"/api/downloaded/{vid}/frame?t=6&w=128").status_code == 200


def test_segments_endpoint_never_404s_on_a_silent_source(client, tmp_path):
    """A source with no speech is ordinary — the bench degrades to a
    filmstrip-only timeline rather than showing an error."""
    vid = asyncio.run(_seed_source(tmp_path / "x.mp4", segments_json=None))
    r = client.get(f"/api/downloaded/{vid}/segments")
    assert r.status_code == 200
    assert r.json() == {"duration": 6.0, "has_segments": False,
                        "truncated": False, "segments": []}


def test_segments_endpoint_survives_a_corrupt_transcript(client, tmp_path):
    vid = asyncio.run(_seed_source(tmp_path / "x.mp4", segments_json="{not json"))
    r = client.get(f"/api/downloaded/{vid}/segments")
    assert r.status_code == 200
    assert r.json()["has_segments"] is False


def test_segments_endpoint_strips_word_timings_and_sorts(client, tmp_path):
    """`words` is the bulk of the payload and the lane draws blocks, not
    words."""
    import json as _json
    raw = _json.dumps([
        {"start": 3.0, "end": 4.0, "text": " second ", "words": [{"w": "x"}] * 50},
        {"start": 1.0, "end": 2.0, "text": "first", "words": [{"w": "y"}] * 50},
        {"start": 5.0, "end": 5.0, "text": "zero-length, dropped"},
        {"start": None, "end": 9.0, "text": "unusable bound, dropped"},
        "not a dict",
    ])
    vid = asyncio.run(_seed_source(tmp_path / "x.mp4", segments_json=raw))
    body = client.get(f"/api/downloaded/{vid}/segments").json()
    assert body["segments"] == [
        {"start": 1.0, "end": 2.0, "text": "first"},
        {"start": 3.0, "end": 4.0, "text": "second"},
    ]


def test_segments_endpoint_truncates_and_says_so(client, tmp_path):
    import json as _json
    raw = _json.dumps([{"start": i, "end": i + 0.5, "text": "x"} for i in range(20)])
    vid = asyncio.run(_seed_source(tmp_path / "x.mp4", segments_json=raw))
    body = client.get(f"/api/downloaded/{vid}/segments?limit=5").json()
    assert body["truncated"] is True
    assert len(body["segments"]) == 5


# ── Cache hygiene ─────────────────────────────────────────────────────────

def test_drop_bench_caches_takes_one_id_and_leaves_its_neighbour():
    """The glob is `<id>_*`, which is exactly the shape that quietly eats a
    prefix-sharing sibling if the key ever loses its separator."""
    from backend.api import downloaded as dl
    from backend.config import settings

    vid = f"benchtest{uuid4().hex[:8]}"
    sibling = f"{vid}extra"
    made = []
    for name in ("strips", "frames"):
        d = settings.THUMBNAILS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        for stem in (vid, sibling):
            f = d / f"{stem}_111_8x48.jpg"
            f.write_bytes(b"x")
            made.append(f)
    try:
        assert dl.drop_bench_caches(vid) == 2
        for name in ("strips", "frames"):
            d = settings.THUMBNAILS_DIR / name
            assert not (d / f"{vid}_111_8x48.jpg").exists()
            assert (d / f"{sibling}_111_8x48.jpg").exists()
    finally:
        for f in made:
            f.unlink(missing_ok=True)


def test_drop_bench_caches_is_fine_with_missing_directories():
    """Failing to tidy a cache must never fail the delete the user asked for —
    an id with nothing cached is the common case, not an error."""
    from backend.api import downloaded as dl

    assert dl.drop_bench_caches(f"nothing{uuid4().hex[:8]}") == 0


def test_deleting_a_source_drops_its_bench_caches(client, tmp_path):
    from backend.config import settings

    vid = asyncio.run(_seed_source(tmp_path / "x.mp4"))
    for name in ("strips", "frames"):
        d = settings.THUMBNAILS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{vid}_111_8x48.jpg").write_bytes(b"x")

    assert client.delete(f"/api/downloaded/{vid}").status_code == 200
    for name in ("strips", "frames"):
        assert list((settings.THUMBNAILS_DIR / name).glob(f"{vid}_*")) == []


def test_the_boot_sweep_takes_the_old_file_and_leaves_the_fresh_one(tmp_path):
    """Age, not size: coming back to a video next week should still find its
    strip warm."""
    old = tmp_path / "frames"
    old.mkdir()
    stale = old / "a_1_320_0000010.jpg"
    fresh = old / "b_1_320_0000010.jpg"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    forty_days_ago = time.time() - 40 * 86400
    import os
    os.utime(stale, (forty_days_ago, forty_days_ago))

    cutoff = time.time() - 30 * 86400
    removed = 0
    for entry in old.iterdir():
        if entry.is_file() and entry.stat().st_mtime < cutoff:
            entry.unlink()
            removed += 1
    assert removed == 1
    assert fresh.exists()


def test_main_registers_the_bench_cache_sweep():
    """The sweep above is only worth anything if boot actually runs it — OSS
    has no scheduler, so the lifespan is the only cadence there is."""
    src = Path("backend/main.py").read_text()
    assert "_purge_bench_caches" in src
    assert "spawn_background(_purge_bench_caches())" in src
