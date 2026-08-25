# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Ken Burns motion grammar: no anamorphic stretch, and pans that move.

These run REAL ffmpeg. They exist because both defects they cover are
invisible to any assertion about return codes — a distorted clip and a frozen
pan are both perfectly valid H.264.
"""
import subprocess

import pytest

from backend.services.ffmpeg_service import (
    _KENBURNS_EFFECTS,
    _kenburns_image_vf,
    generate_kenburns_video,
)
from backend.services.video_utils import probe_media


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")


def _make_image(path, w, h, color="red"):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}",
         "-frames:v", "1", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


# ── The filter chain itself (pure, no ffmpeg needed) ───────────────────────

@pytest.mark.parametrize("effect", _KENBURNS_EFFECTS)
def test_every_effect_cover_crops_to_the_target_aspect_first(effect):
    """zoompan's window inherits the INPUT aspect, so the input must already
    be at the target aspect or `s=` stretches it. 2x target for smoothness."""
    vf = _kenburns_image_vf(effect, 90, 1080, 1920, 30)
    assert vf.startswith(
        "scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
    )
    assert "zoompan=" in vf
    assert vf.endswith("format=yuv420p")


@pytest.mark.parametrize("effect", _KENBURNS_EFFECTS)
def test_no_effect_scales_to_the_old_fixed_width(effect):
    """`scale=8000:-1` preserved the SOURCE aspect — that was the distortion."""
    assert "8000" not in _kenburns_image_vf(effect, 90, 1080, 1920, 30)


@pytest.mark.parametrize("effect", ["pan_left", "pan_right", "pan_up"])
def test_pan_travel_range_stays_in_input_space(effect):
    """The travel range must be zoompan's own clamp (`iw - iw/zoom`), not a
    mix of input pixels and output pixels — that overshot the clamp and left
    the pan pinned to one edge for half its duration."""
    vf = _kenburns_image_vf(effect, 90, 1080, 1920, 30)
    assert "iw/1.3-1080" not in vf and "ih/1.3-1920" not in vf
    assert ("(iw-iw/zoom)" in vf) or ("(ih-ih/zoom)" in vf)


def test_the_frame_count_reaches_the_zoompan_duration():
    assert ":d=123:" in _kenburns_image_vf("zoom_in", 123, 1080, 1920, 30)


# ── Real renders ───────────────────────────────────────────────────────────

@needs_ffmpeg
async def test_a_square_image_is_not_stretched_into_a_portrait_frame(tmp_path):
    """A 1:1 source into a 9:16 target used to come out anamorphically
    elongated. The output must be exactly the target frame."""
    img = _make_image(tmp_path / "square.png", 800, 800)
    out = await generate_kenburns_video(
        image_paths=[img], output_path=tmp_path / "kb.mp4",
        aspect_ratio="9:16", duration_per_image=3,
    )
    w, h, _dur = probe_media(out)
    assert (w, h) == (1080, 1920)


@needs_ffmpeg
async def test_a_landscape_image_renders_a_landscape_frame(tmp_path):
    img = _make_image(tmp_path / "wide.png", 1600, 400)
    out = await generate_kenburns_video(
        image_paths=[img], output_path=tmp_path / "kb.mp4",
        aspect_ratio="16:9", duration_per_image=3,
    )
    w, h, _dur = probe_media(out)
    assert (w, h) == (1920, 1080)


@needs_ffmpeg
@pytest.mark.parametrize("effect", ["pan_left", "pan_right"])
def test_a_pan_keeps_moving_for_its_whole_duration(tmp_path, effect):
    """The regression proof for the frozen pan.

    A frozen pan is a perfectly valid H.264 file, so the only honest test
    renders one and looks at the pixels. The metric is mean absolute
    difference between decoded frames over a busy `testsrc2` pattern —
    deliberately NOT mean brightness, which saturates at both ends of a
    gradient and reads a moving pan as stationary.

    Measured on this fixture (MAD per half, 0-255 scale):

        pan_left  old: first 0.32  second 104.73   <- pinned, then lurched
                  new: first 67.88 second  64.08
        pan_right old: first 108.29 second 0.00    <- ran out, then stopped
                  new: first 68.11 second 63.88

    Both directions matter and testing one hides the other: the reversed
    expressions (`*(1-on/d)`) start beyond zoompan's clamp and are frozen at
    the START, the forward ones (`*on/d`) run past it and freeze at the END.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow needed to read pixels")
    from PIL import ImageChops, ImageStat

    src = tmp_path / "busy.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=2160x3840",
         "-frames:v", "1", str(src)],
        capture_output=True, timeout=30, check=True,
    )

    clip = tmp_path / "pan.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vf", _kenburns_image_vf(effect, 90, 540, 960, 30),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-t", "3", str(clip)],
        capture_output=True, timeout=180, check=True,
    )

    def _frame_at(t: float):
        png = tmp_path / f"f_{t}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(clip),
             "-frames:v", "1", str(png)],
            capture_output=True, timeout=60, check=True,
        )
        return Image.open(png).convert("L")

    start, mid, end = (_frame_at(t) for t in (0.0, 1.5, 2.9))

    def _moved(a, b) -> float:
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]

    # The defect is a frozen HALF. Old measured 0.00-0.32 there; new measures
    # 63-68 in every half, so the threshold has two orders of magnitude of room.
    first_half, second_half = _moved(start, mid), _moved(mid, end)
    assert first_half > 5.0, (
        f"{effect} is frozen for its first half (MAD {first_half:.2f}, "
        f"second half {second_half:.2f})"
    )
    assert second_half > 5.0, (
        f"{effect} is frozen for its second half (MAD {second_half:.2f}, "
        f"first half {first_half:.2f})"
    )
