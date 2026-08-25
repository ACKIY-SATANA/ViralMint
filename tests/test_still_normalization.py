# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Every still is composited, bounded and single-frame before it is animated.

Real ffmpeg throughout. The defect these cover is silent by construction: a
cut-out whose alpha was dropped instead of composited is a perfectly valid
image file, it just has the wrong pixels in it.
"""
import subprocess

import pytest

from backend.services.ffmpeg_service import (
    MAX_STILL_DIMENSION,
    _capped_still_dimensions,
    _normalize_still_sync,
    generate_kenburns_video,
    normalize_still,
)
from backend.core.exceptions import VideoGenerationError
from backend.services.video_utils import probe_dimensions


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")

PIL = pytest.importorskip("PIL", reason="Pillow needed to author/read test pixels")
from PIL import Image  # noqa: E402


def _cutout(path, size=400, hidden=(0, 128, 0)):
    """An opaque white disc on a FULLY TRANSPARENT field whose hidden RGB is
    green — i.e. what a background-removal tool hands us."""
    im = Image.new("RGBA", (size, size), (*hidden, 0))
    px = im.load()
    r = size // 6
    for y in range(size):
        for x in range(size):
            if (x - size // 2) ** 2 + (y - size // 2) ** 2 < r * r:
                px[x, y] = (255, 255, 255, 255)
    im.save(path)
    return path


# ── The dimension cap (pure) ───────────────────────────────────────────────

def test_a_normal_photo_is_left_at_its_own_size():
    assert _capped_still_dimensions(1920, 1080) == (1920, 1080)


def test_an_oversized_still_is_capped_on_its_longest_edge_preserving_aspect():
    w, h = _capped_still_dimensions(12000, 6000)
    assert max(w, h) == MAX_STILL_DIMENSION
    assert abs((w / h) - 2.0) < 0.01


def test_a_tall_still_caps_on_height():
    w, h = _capped_still_dimensions(3000, 9000)
    assert max(w, h) == MAX_STILL_DIMENSION and h > w


def test_capped_dimensions_are_always_even():
    """Odd dimensions break several encoders downstream."""
    for dims in [(801, 601), (12001, 6001), (3, 3)]:
        w, h = _capped_still_dimensions(*dims)
        assert w % 2 == 0 and h % 2 == 0, dims


# ── Compositing (the reason this pass exists) ──────────────────────────────

@needs_ffmpeg
def test_a_transparent_cutout_is_composited_onto_black_not_flattened(tmp_path):
    """The regression proof.

    Measured on this exact fixture WITHOUT the pass, straight through the Ken
    Burns chain: the transparent field came back (0, 127, 0) — the hidden
    green that `format=yuv420p` left behind when it discarded the alpha.
    """
    src = _cutout(tmp_path / "cutout.png")
    dst = tmp_path / "norm.png"
    _normalize_still_sync(src, dst)

    out = Image.open(dst).convert("RGB")
    corner = out.getpixel((5, 5))
    assert max(corner) <= 4, f"transparent field kept its hidden colour: {corner}"

    # The opaque subject must survive intact.
    centre = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert min(centre) >= 250, f"subject was damaged: {centre}"


@needs_ffmpeg
def test_the_normalized_still_has_no_alpha_channel_left(tmp_path):
    src = _cutout(tmp_path / "cutout.png")
    dst = tmp_path / "norm.png"
    _normalize_still_sync(src, dst)
    assert Image.open(dst).mode == "RGB"


@needs_ffmpeg
async def test_the_kenburns_render_no_longer_leaks_the_hidden_colour(tmp_path):
    """The wiring test: normalization has to be reached by the render path,
    not merely available to it."""
    src = _cutout(tmp_path / "cutout.png")
    out = await generate_kenburns_video(
        image_paths=[src], output_path=tmp_path / "kb.mp4",
        aspect_ratio="9:16", duration_per_image=3,
    )
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.2", "-i", str(out), "-frames:v", "1", str(frame)],
        capture_output=True, timeout=60, check=True,
    )
    corner = Image.open(frame).convert("RGB").getpixel((5, 5))
    # Generous on encoder noise, but nowhere near a 128-green.
    assert corner[1] < 40, f"hidden green reached the render: {corner}"


# ── Bounding and animation ─────────────────────────────────────────────────

@needs_ffmpeg
async def test_a_camera_sized_panorama_is_capped_before_it_reaches_zoompan(tmp_path):
    src = tmp_path / "pano.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=9000x3000",
         "-frames:v", "1", str(src)],
        capture_output=True, timeout=60, check=True,
    )
    dst = tmp_path / "norm.png"
    w, h = await normalize_still(src, dst)
    assert max(w, h) == MAX_STILL_DIMENSION
    pw, ph = probe_dimensions(dst)
    assert (pw, ph) == (w, h)


@needs_ffmpeg
def test_an_animated_gif_is_reduced_to_a_single_frame(tmp_path):
    """A GIF is accepted rather than rejected — the user picked it and
    "nothing happened" is a worse experience than getting frame 0."""
    frames = [Image.new("RGB", (200, 200), c) for c in ("red", "blue", "green")]
    gif = tmp_path / "anim.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=200, loop=0)

    dst = tmp_path / "norm.png"
    _normalize_still_sync(gif, dst)

    out = Image.open(dst).convert("RGB")
    assert getattr(out, "n_frames", 1) == 1
    r, g, b = out.getpixel((100, 100))
    assert r > 200 and g < 60 and b < 60, f"expected frame 0 (red), got {(r, g, b)}"


@needs_ffmpeg
def test_an_unreadable_file_raises_instead_of_writing_a_broken_still(tmp_path):
    junk = tmp_path / "not-an-image.png"
    junk.write_bytes(b"this is not a PNG")
    dst = tmp_path / "norm.png"
    with pytest.raises(VideoGenerationError):
        _normalize_still_sync(junk, dst)
    assert not dst.exists()


@needs_ffmpeg
def test_an_opaque_photo_survives_normalization_unchanged_in_size(tmp_path):
    """The common case has to be a no-op in every way that matters."""
    src = tmp_path / "photo.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=orange:s=1600x900",
         "-frames:v", "1", str(src)],
        capture_output=True, timeout=60, check=True,
    )
    dst = tmp_path / "norm.png"
    assert _normalize_still_sync(src, dst) == (1600, 900)
    r, g, b = Image.open(dst).convert("RGB").getpixel((800, 450))
    assert r > 200 and 100 < g < 190 and b < 60, (r, g, b)
