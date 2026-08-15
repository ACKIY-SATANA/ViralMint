# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""crop_rect + run_tool_crop.

`crop_rect` turns a normalized box that arrived over HTTP into the four
integers ffmpeg gets. Everything that can quietly ruin a crop lives in that
conversion:

  * an odd width or height — libx264 refuses outright;
  * an odd OFFSET — with yuv420p chroma subsampling that shifts colour against
    luma, which no error reports and only some footage makes obvious;
  * a box that rounds one pixel past the right/bottom edge — fails on certain
    source sizes only, which is the worst kind to ship;
  * a degenerate box — better a sentence than an ffmpeg stderr dump.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.core import tool_runners as tr
from backend.core.tool_runners import CROP_MIN_PIXELS, crop_rect


class TestCropRect:
    def test_full_frame_is_the_whole_source(self) -> None:
        assert crop_rect(1920, 1080, 0, 0, 1, 1) == (1920, 1080, 0, 0)

    def test_centre_half_box(self) -> None:
        w, h, x, y = crop_rect(1920, 1080, 0.25, 0.25, 0.5, 0.5)
        assert (w, h, x, y) == (960, 540, 480, 270)

    def test_every_value_is_even(self) -> None:
        """libx264 rejects odd dimensions; yuv420p chroma hates odd offsets."""
        for src in ((1920, 1080), (1280, 720), (1079, 607), (854, 480)):
            for box in ((0.13, 0.27, 0.41, 0.53), (0.07, 0.11, 0.83, 0.79)):
                w, h, x, y = crop_rect(*src, *box)
                assert w % 2 == 0 and h % 2 == 0, (src, box, w, h)
                assert x % 2 == 0 and y % 2 == 0, (src, box, x, y)

    def test_never_extends_past_the_frame(self) -> None:
        """The rounding-overshoot case: x+w must stay inside the source."""
        for src_w, src_h in ((1920, 1080), (1279, 719), (641, 361)):
            for box in ((0.9, 0.9, 0.5, 0.5), (0.999, 0.999, 1.0, 1.0),
                        (0.333, 0.666, 0.667, 0.334)):
                try:
                    w, h, x, y = crop_rect(src_w, src_h, *box)
                except ValueError:
                    continue  # too small is a legitimate outcome here
                assert x + w <= src_w, (src_w, box, x, w)
                assert y + h <= src_h, (src_h, box, y, h)

    def test_out_of_range_values_are_clamped_not_rejected(self) -> None:
        """A normalized box is user input arriving over HTTP."""
        w, h, x, y = crop_rect(1920, 1080, -0.5, -0.5, 2.0, 2.0)
        assert (x, y) == (0, 0)
        assert (w, h) == (1920, 1080)

    def test_box_running_off_the_right_edge_is_truncated(self) -> None:
        w, h, x, y = crop_rect(1000, 1000, 0.8, 0.0, 0.5, 1.0)
        assert x == 800
        assert x + w <= 1000

    def test_degenerate_box_raises_a_readable_error(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            crop_rect(1920, 1080, 0.5, 0.5, 0.001, 0.001)

    def test_box_at_exactly_the_minimum_is_allowed(self) -> None:
        # 16/1920 of the width is exactly CROP_MIN_PIXELS across.
        w, h, _, _ = crop_rect(1920, 1080, 0, 0, CROP_MIN_PIXELS / 1920,
                               CROP_MIN_PIXELS / 1080)
        assert w >= CROP_MIN_PIXELS and h >= CROP_MIN_PIXELS

    def test_unknown_source_dimensions_raise(self) -> None:
        """probe_media returns (0, 0, 0.0) on failure — cropping blind would
        produce a meaningless box."""
        with pytest.raises(ValueError, match="dimensions"):
            crop_rect(0, 0, 0, 0, 1, 1)

    def test_portrait_source(self) -> None:
        w, h, x, y = crop_rect(1080, 1920, 0.0, 0.25, 1.0, 0.5)
        assert (w, h, x, y) == (1080, 960, 0, 480)


@pytest.fixture
def terminal_spies():
    with patch.object(tr, "_tool_progress", new=AsyncMock()) as prog, \
         patch.object(tr, "_tool_success", new=AsyncMock()) as ok, \
         patch.object(tr, "_tool_fail", new=AsyncMock()) as fail:
        yield {"progress": prog, "success": ok, "fail": fail}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 1000)
    monkeypatch.setattr(
        "backend.api.tools.tool_out_path",
        lambda job_id, ext=".mp4": tmp_path / f"{job_id}{ext}",
    )
    return {"src": src, "dir": tmp_path}


@pytest.mark.asyncio
class TestCropRunner:
    async def test_happy_path_reports_the_rect(
        self, terminal_spies, paths,
    ):
        async def _to_thread(fn, *a, **k):
            if a or k:
                return (1920, 1080, 12.0)
            (paths["dir"] / "k1.mp4").write_bytes(b"y" * 500)
            return None

        with patch.object(tr.asyncio, "to_thread", new=_to_thread):
            await tr.run_tool_crop("k1", paths["src"], 0.25, 0.25, 0.5, 0.5)

        terminal_spies["success"].assert_awaited()
        _, kwargs = terminal_spies["success"].call_args
        extra = kwargs["extra_output"]
        assert (extra["width"], extra["height"]) == (960, 540)
        assert (extra["x"], extra["y"]) == (480, 270)
        assert (extra["source_width"], extra["source_height"]) == (1920, 1080)

    async def test_degenerate_box_fails_before_ffmpeg_runs(
        self, terminal_spies, paths,
    ):
        encoded = False

        async def _to_thread(fn, *a, **k):
            nonlocal encoded
            if a or k:
                return (1920, 1080, 12.0)
            encoded = True
            return None

        with patch.object(tr.asyncio, "to_thread", new=_to_thread):
            await tr.run_tool_crop("k2", paths["src"], 0.5, 0.5, 0.0005, 0.0005)

        terminal_spies["fail"].assert_awaited()
        terminal_spies["success"].assert_not_awaited()
        assert not encoded, "ffmpeg was invoked on a box we already knew was bad"

    async def test_unprobeable_source_fails_cleanly(self, terminal_spies, paths):
        async def _to_thread(fn, *a, **k):
            if a or k:
                return (0, 0, 0.0)
            return None

        with patch.object(tr.asyncio, "to_thread", new=_to_thread):
            await tr.run_tool_crop("k3", paths["src"], 0, 0, 1, 1)

        terminal_spies["fail"].assert_awaited()
        terminal_spies["success"].assert_not_awaited()
