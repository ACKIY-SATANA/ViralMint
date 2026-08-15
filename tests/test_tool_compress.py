# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""run_tool_compress + its two pure helpers.

The helpers carry the decisions that are easy to get quietly wrong — the
level→CRF mapping and, above all, the never-upscale rule. Picking "HD 720p"
for a 480p clip must keep the source size: upscaling invents pixels, makes the
file BIGGER, and the user pressed a button called Compress.

The runner tests cover the two outcomes worth reporting rather than hiding: a
no-op resolution request, and a result that came out larger than the input.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.core import tool_runners as tr
from backend.core.tool_runners import compress_scale_filter, compress_settings


class TestCompressSettings:
    def test_levels_are_ordered_smallest_to_best(self) -> None:
        crfs = [compress_settings(lv)[0] for lv in
                ("maximum", "high", "medium", "low", "minimal")]
        # Higher CRF = more compression = smaller file, so the sequence must
        # fall monotonically from "maximum" (smallest) to "minimal" (best).
        assert crfs == sorted(crfs, reverse=True)
        assert len(set(crfs)) == len(crfs), "two levels map to the same CRF"

    def test_audio_bitrate_rises_with_quality(self) -> None:
        rates = [int(compress_settings(lv)[1].rstrip("k")) for lv in
                 ("maximum", "high", "medium", "low", "minimal")]
        assert rates == sorted(rates)

    def test_unknown_level_falls_back_to_high(self) -> None:
        assert compress_settings("banana") == compress_settings("high")
        assert compress_settings("") == compress_settings("high")
        assert compress_settings(None) == compress_settings("high")

    def test_level_is_case_insensitive(self) -> None:
        assert compress_settings("MAXIMUM") == compress_settings("maximum")


class TestCompressScaleFilter:
    def test_original_never_scales(self) -> None:
        assert compress_scale_filter(1920, 1080, "original") == ("", False)

    def test_downscale_targets_height_and_derives_even_width(self) -> None:
        vf, scaled = compress_scale_filter(1920, 1080, "1280x720")
        assert scaled is True
        # -2 keeps the aspect AND forces an even width; libx264 rejects odd.
        assert vf == "scale=-2:720:flags=lanczos"

    def test_never_upscales_a_smaller_source(self) -> None:
        """The headline rule: 480p asked to become 720p stays 480p."""
        assert compress_scale_filter(854, 480, "1280x720") == ("", False)

    def test_equal_height_is_not_a_downscale(self) -> None:
        assert compress_scale_filter(1280, 720, "1280x720") == ("", False)

    def test_portrait_source_scales_by_height_too(self) -> None:
        """1080x1920 asked for 720p → 720 TALL, not 720 wide. The presets are
        named by height, and a vertical short is the common case here."""
        vf, scaled = compress_scale_filter(1080, 1920, "1280x720")
        assert scaled is True
        assert vf == "scale=-2:720:flags=lanczos"

    def test_unknown_dimensions_skip_scaling(self) -> None:
        """probe_media returns (0, 0, 0.0) on failure — scaling blind would
        risk upscaling, so we simply re-encode at source size."""
        assert compress_scale_filter(0, 0, "640x360") == ("", False)

    def test_unknown_resolution_label_skips_scaling(self) -> None:
        assert compress_scale_filter(1920, 1080, "4000x3000") == ("", False)


@pytest.fixture
def terminal_spies():
    with patch.object(tr, "_tool_progress", new=AsyncMock()) as prog, \
         patch.object(tr, "_tool_success", new=AsyncMock()) as ok, \
         patch.object(tr, "_tool_fail", new=AsyncMock()) as fail:
        yield {"progress": prog, "success": ok, "fail": fail}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 10_000)
    monkeypatch.setattr(
        "backend.api.tools.tool_out_path",
        lambda job_id, ext=".mp4": tmp_path / f"{job_id}{ext}",
    )
    return {"src": src, "dir": tmp_path}


@pytest.mark.asyncio
class TestCompressRunner:
    async def _run(self, paths, job, *, out_size, dims=(1920, 1080), **kw):
        out = paths["dir"] / f"{job}.mp4"

        async def _to_thread(fn, *a, **k):
            # probe_media is called through to_thread too — distinguish by
            # whether args were passed (the encode closure takes none).
            if a or k:
                return (dims[0], dims[1], 12.0)
            out.write_bytes(b"y" * out_size)
            return None

        with patch.object(tr.asyncio, "to_thread", new=_to_thread):
            await tr.run_tool_compress(job, paths["src"], **kw)
        return out

    async def test_reports_both_sizes_and_the_saving(self, terminal_spies, paths):
        with patch("backend.core.ws_manager.ws_manager.send_constraint_warning",
                   new=AsyncMock()):
            await self._run(paths, "c2", out_size=4_000)

        extra = terminal_spies["success"].call_args.kwargs["extra_output"]
        assert extra["input_bytes"] == 10_000
        assert extra["output_bytes"] == 4_000
        assert extra["saved_pct"] == 60.0

    async def test_upscale_request_warns_and_does_not_scale(self, terminal_spies, paths):
        sent: list[str] = []

        async def _capture(**kwargs):
            sent.append(kwargs.get("message", ""))

        with patch("backend.core.ws_manager.ws_manager.send_constraint_warning",
                   new=AsyncMock(side_effect=_capture)):
            await self._run(paths, "c3", out_size=4_000, dims=(854, 480),
                            resolution="1280x720")

        assert any("already 854×480" in m for m in sent)
        extra = terminal_spies["success"].call_args.kwargs["extra_output"]
        assert extra["scaled"] is False

    async def test_bigger_output_warns_but_still_succeeds(self, terminal_spies, paths):
        """An already-compressed source can grow. That's a real outcome of the
        work the user asked for — report it, don't fail it and don't hide it."""
        sent: list[str] = []

        async def _capture(**kwargs):
            sent.append(kwargs.get("message", ""))

        with patch("backend.core.ws_manager.ws_manager.send_constraint_warning",
                   new=AsyncMock(side_effect=_capture)):
            await self._run(paths, "c4", out_size=20_000)

        terminal_spies["success"].assert_awaited()
        terminal_spies["fail"].assert_not_awaited()
        assert any("already well compressed" in m for m in sent)

    async def test_encode_failure_routes_through_tool_fail(self, terminal_spies, paths):
        async def _to_thread(fn, *a, **k):
            if a or k:
                return (1920, 1080, 12.0)
            raise RuntimeError("Compression failed: bad codec")

        with patch.object(tr.asyncio, "to_thread", new=_to_thread), \
             patch("backend.core.ws_manager.ws_manager.send_constraint_warning",
                   new=AsyncMock()):
            await tr.run_tool_compress("c5", paths["src"])

        terminal_spies["fail"].assert_awaited()
        terminal_spies["success"].assert_not_awaited()
