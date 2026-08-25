# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""One still -> one scene-sized silent clip.

Real ffmpeg. This helper's whole job is producing something that stitches
alongside stock footage, so the assertions are about geometry, length and the
absence of an audio track — the three things that make a concat fail or a
video desync.
"""
import subprocess

import pytest

from backend.core.exceptions import VideoGenerationError
from backend.services.ffmpeg_service import generate_single_kenburns_clip
from backend.services.video_utils import probe_dimensions, probe_duration


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")


def _image(path, w=800, h=800, color="red"):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}",
         "-frames:v", "1", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


def _has_audio(path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    return bool(out.stdout.strip())


@needs_ffmpeg
async def test_the_clip_lands_on_the_requested_scene_geometry(tmp_path):
    """A clip that isn't exactly the target size cannot be concatenated with
    the stock clips beside it."""
    out = await generate_single_kenburns_clip(
        image_path=_image(tmp_path / "img.png"),
        duration=2.0,
        output_path=tmp_path / "clip.mp4",
        target_w=1080, target_h=1920,
    )
    assert probe_dimensions(out) == (1080, 1920)


@needs_ffmpeg
async def test_a_landscape_target_is_honoured_too(tmp_path):
    out = await generate_single_kenburns_clip(
        image_path=_image(tmp_path / "img.png", 400, 1200),
        duration=2.0,
        output_path=tmp_path / "clip.mp4",
        target_w=1920, target_h=1080,
    )
    assert probe_dimensions(out) == (1920, 1080)


@needs_ffmpeg
async def test_the_clip_is_the_length_the_scene_asked_for(tmp_path):
    out = await generate_single_kenburns_clip(
        image_path=_image(tmp_path / "img.png"),
        duration=3.5,
        output_path=tmp_path / "clip.mp4",
    )
    assert abs(probe_duration(out, default=0.0) - 3.5) < 0.2


@needs_ffmpeg
async def test_the_clip_carries_no_audio_track(tmp_path):
    """The voice track is merged over the stitched video at the end; an audio
    stream here would survive the concat and fight it."""
    out = await generate_single_kenburns_clip(
        image_path=_image(tmp_path / "img.png"),
        duration=1.5,
        output_path=tmp_path / "clip.mp4",
    )
    assert not _has_audio(out)


@needs_ffmpeg
async def test_it_normalizes_by_default_so_a_cutout_is_safe(tmp_path):
    """Default-on, because a caller that forgets gets a silently wrong image
    rather than an error."""
    Image = pytest.importorskip("PIL.Image")
    src = tmp_path / "cutout.png"
    im = Image.new("RGBA", (400, 400), (0, 128, 0, 0))
    im.save(src)

    out = await generate_single_kenburns_clip(
        image_path=src, duration=1.5, output_path=tmp_path / "clip.mp4",
    )
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.2", "-i", str(out), "-frames:v", "1", str(frame)],
        capture_output=True, timeout=60, check=True,
    )
    r, g, b = Image.open(frame).convert("RGB").getpixel((50, 50))
    assert g < 40, f"hidden green survived: {(r, g, b)}"


@needs_ffmpeg
async def test_normalization_leaves_no_scratch_behind(tmp_path):
    """A per-scene helper runs once per scene per render — leaking one
    normalized PNG each time fills the scratch dir quietly."""
    from backend.config import settings

    settings.TMP_DIR.mkdir(parents=True, exist_ok=True)
    before = set(settings.TMP_DIR.glob("*kb_one_*"))

    await generate_single_kenburns_clip(
        image_path=_image(tmp_path / "img.png"),
        duration=1.5,
        output_path=tmp_path / "clip.mp4",
    )

    assert set(settings.TMP_DIR.glob("*kb_one_*")) == before


@needs_ffmpeg
async def test_an_unreadable_image_raises_rather_than_writing_a_broken_clip(tmp_path):
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not a png")
    with pytest.raises(VideoGenerationError):
        await generate_single_kenburns_clip(
            image_path=junk, duration=1.5, output_path=tmp_path / "clip.mp4",
        )
