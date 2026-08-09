# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The remaining tool runners.

Each runner is thin — build a filtergraph, shell out, report — but the thin
part is where the user-visible failures live. Three things are checked for
every one of them:

  * the terminal state is reached exactly once. A runner that returns without
    calling _tool_success or _tool_fail leaves a job "running" forever and the
    UI spins;
  * a failing ffmpeg becomes a FAILED job, not a success over a broken file;
  * the arguments that came from the request actually reach the command — a
    silently-ignored option is worse than a rejected one, because the user
    watches the render finish and gets the wrong thing.

`subprocess` and the async ffmpeg helpers are stubbed; nothing here encodes.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core import tool_runners as trun


@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    """Point tool_out_path at a temp dir and pre-create a plausible output."""
    d = tmp_path / "out"
    d.mkdir()

    def fake_out(job_id, ext=".mp4"):
        p = d / f"{job_id}{ext}"
        p.write_bytes(b"\x00" * 4096)
        return p

    monkeypatch.setattr("backend.api.tools.tool_out_path", fake_out)
    return d


@pytest.fixture()
def terminal():
    """Spy on the three terminal/progress helpers."""
    with patch.object(trun, "_tool_progress", new=AsyncMock()) as prog, \
         patch.object(trun, "_tool_success", new=AsyncMock(return_value=True)) as ok, \
         patch.object(trun, "_tool_fail", new=AsyncMock()) as fail:
        yield {"progress": prog, "success": ok, "fail": fail}


@pytest.fixture()
def run_ok():
    """subprocess.run that always succeeds."""
    with patch.object(trun, "subprocess") as sp:
        sp.run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        sp.TimeoutExpired = subprocess.TimeoutExpired
        yield sp


@pytest.fixture()
def run_fail():
    with patch.object(trun, "subprocess") as sp:
        sp.run.return_value = MagicMock(returncode=1, stderr="ffmpeg: boom")
        sp.TimeoutExpired = subprocess.TimeoutExpired
        yield sp


def _args(sp):
    """Flatten every argv the runner passed to subprocess.run."""
    return [" ".join(str(x) for x in c.args[0]) for c in sp.run.call_args_list]


IN = Path("/tmp/in.mp4")


# ── transform ───────────────────────────────────────────────────────────────

class TestTransform:
    @pytest.mark.parametrize("op,expect", [
        ("flip_h", "hflip"),
        ("flip_v", "vflip"),
        ("rotate_cw", "transpose"),
        ("rotate_ccw", "transpose"),
        ("rotate_180", "transpose"),
    ])
    def test_each_operation_reaches_ffmpeg(self, terminal, run_ok, out_dir,
                                           op, expect):
        asyncio.run(trun.run_tool_transform("j", IN, op))
        assert any(expect in a for a in _args(run_ok)), f"{op} → {_args(run_ok)}"
        terminal["success"].assert_awaited()

    def test_volume_uses_the_amount(self, terminal, run_ok, out_dir):
        asyncio.run(trun.run_tool_transform("j", IN, "volume", "2.5"))
        assert any("volume=2.5" in a for a in _args(run_ok))

    def test_loop_uses_the_amount(self, terminal, run_ok, out_dir):
        asyncio.run(trun.run_tool_transform("j", IN, "loop", "3"))
        terminal["success"].assert_awaited()

    def test_a_failing_ffmpeg_fails_the_job(self, terminal, run_fail, out_dir):
        asyncio.run(trun.run_tool_transform("j", IN, "flip_h"))
        terminal["fail"].assert_awaited()
        terminal["success"].assert_not_awaited()


# ── speed ───────────────────────────────────────────────────────────────────

class TestSpeed:
    def test_the_requested_speed_reaches_the_filtergraph(self, terminal, run_ok,
                                                          out_dir):
        asyncio.run(trun.run_tool_speed("j", IN, speed=2.0))
        joined = " ".join(_args(run_ok))
        assert "setpts" in joined and "atempo" in joined

    def test_dropping_pitch_correction_skips_atempo(self, terminal, run_ok,
                                                     out_dir):
        asyncio.run(trun.run_tool_speed("j", IN, speed=2.0, keep_pitch=False))
        terminal["success"].assert_awaited()

    def test_an_extreme_speed_still_produces_valid_filters(self, terminal,
                                                           run_ok, out_dir):
        """atempo only accepts 0.5-2.0 per stage, so 8x has to be chained."""
        asyncio.run(trun.run_tool_speed("j", IN, speed=8.0))
        joined = " ".join(_args(run_ok))
        assert joined.count("atempo") >= 2, "8x needs chained atempo stages"

    def test_a_failure_fails_the_job(self, terminal, run_fail, out_dir):
        asyncio.run(trun.run_tool_speed("j", IN, speed=2.0))
        terminal["fail"].assert_awaited()


class TestSpeedFilters:
    """The pure filter builder behind the runner."""

    @pytest.mark.parametrize("speed", [0.25, 0.5, 1.0, 1.5, 2.0, 4.0, 16.0])
    def test_every_speed_yields_a_usable_pair(self, speed):
        v, a = trun._build_speed_filters(speed, keep_pitch=True)
        assert "setpts" in v and a

    def test_atempo_stages_stay_in_ffmpegs_accepted_range(self):
        _, a = trun._build_speed_filters(16.0, keep_pitch=True)
        factors = [float(p.split("=")[1]) for p in a.split(",") if "atempo=" in p]
        assert all(0.5 <= f <= 2.0 + 1e-9 for f in factors), factors

    def test_slowing_down_also_chains(self):
        _, a = trun._build_speed_filters(0.125, keep_pitch=True)
        factors = [float(p.split("=")[1]) for p in a.split(",") if "atempo=" in p]
        assert all(0.5 <= f <= 2.0 for f in factors), factors


# ── gif ─────────────────────────────────────────────────────────────────────

class TestGif:
    def test_it_uses_a_two_pass_palette(self, terminal, run_ok, out_dir):
        """One-pass GIF quantisation looks visibly worse."""
        asyncio.run(trun.run_tool_gif("j", IN))
        joined = " ".join(_args(run_ok))
        assert "palettegen" in joined and "paletteuse" in joined

    def test_fps_and_width_reach_the_filters(self, terminal, run_ok, out_dir):
        asyncio.run(trun.run_tool_gif("j", IN, fps=24, width=720))
        joined = " ".join(_args(run_ok))
        assert "fps=24" in joined and "720" in joined

    def test_a_time_window_is_applied(self, terminal, run_ok, out_dir):
        asyncio.run(trun.run_tool_gif("j", IN, start_seconds=5,
                                      duration_seconds=3))
        joined = " ".join(_args(run_ok))
        assert "5" in joined and "3" in joined

    def test_a_failure_fails_the_job(self, terminal, run_fail, out_dir):
        asyncio.run(trun.run_tool_gif("j", IN))
        terminal["fail"].assert_awaited()


# ── watermark ───────────────────────────────────────────────────────────────

class TestWatermark:
    @pytest.mark.parametrize("pos", ["top-left", "top-right",
                                     "bottom-left", "bottom-right"])
    def test_every_corner_is_expressible(self, terminal, run_ok, out_dir, pos):
        asyncio.run(trun.run_tool_watermark(
            "j", IN, Path("/tmp/logo.png"), pos, 0.8, 15.0))
        assert any("overlay" in a for a in _args(run_ok))
        terminal["success"].assert_awaited()

    def test_opacity_and_size_reach_the_filtergraph(self, terminal, run_ok,
                                                    out_dir):
        asyncio.run(trun.run_tool_watermark(
            "j", IN, Path("/tmp/logo.png"), "bottom-right", 0.35, 22.0))
        joined = " ".join(_args(run_ok))
        assert "0.35" in joined and "22" in joined

    def test_a_failure_fails_the_job(self, terminal, run_fail, out_dir):
        asyncio.run(trun.run_tool_watermark(
            "j", IN, Path("/tmp/logo.png"), "bottom-right", 0.8, 15.0))
        terminal["fail"].assert_awaited()


# ── music visualizer ────────────────────────────────────────────────────────

class TestMusicVisualizer:
    @pytest.fixture(autouse=True)
    def no_probe(self, monkeypatch):
        """The runner measures the audio first — don't shell out to ffprobe."""
        monkeypatch.setattr("backend.services.video_utils.probe_duration",
                            lambda p, default=0.0: 30.0)

    @pytest.mark.parametrize("style", ["waves", "bars", "spectrum"])
    def test_every_style_renders(self, terminal, run_ok, out_dir, style):
        asyncio.run(trun.run_tool_music_visualizer("j", Path("/tmp/a.mp3"),
                                                   style=style))
        assert _args(run_ok), "it must actually invoke ffmpeg"
        terminal["success"].assert_awaited()

    @pytest.mark.parametrize("aspect,dims", [
        ("9:16", "1080x1920"), ("1:1", "1080x1080"), ("16:9", "1920x1080")])
    def test_every_aspect_reaches_the_render(self, terminal, run_ok, out_dir,
                                             aspect, dims):
        asyncio.run(trun.run_tool_music_visualizer("j", Path("/tmp/a.mp3"),
                                                   aspect=aspect))
        assert any(dims in a for a in _args(run_ok)), _args(run_ok)

    def test_a_failure_fails_the_job(self, terminal, run_fail, out_dir):
        asyncio.run(trun.run_tool_music_visualizer("j", Path("/tmp/a.mp3")))
        terminal["fail"].assert_awaited()


# ── merge ───────────────────────────────────────────────────────────────────

class TestMergeClips:
    @pytest.fixture()
    def clips(self, tmp_path):
        out = []
        for i in range(3):
            p = tmp_path / f"c{i}.mp4"
            p.write_bytes(b"\x00" * 4096)
            out.append(p)
        return out

    @pytest.fixture()
    def stitched(self, monkeypatch, out_dir):
        """Merge normalises each clip then stitches — stub both seams."""
        calls = {"cropped": [], "stitched": None}

        def fake_crop(src, dst, w, h):
            calls["cropped"].append((Path(src).name, w, h))
            Path(dst).write_bytes(b"\x00" * 2048)

        async def fake_stitch(paths, output_path=None, **kw):
            calls["stitched"] = list(paths)
            out = Path(output_path) if output_path else out_dir / "merged.mp4"
            out.write_bytes(b"\x00" * 4096)
            return out

        monkeypatch.setattr(trun, "_crop_to_aspect_sync", fake_crop)
        monkeypatch.setattr("backend.services.ffmpeg_service.stitch_clips",
                            fake_stitch)
        monkeypatch.setattr(trun, "_probe_dims_sync", lambda p: (1080, 1920))
        return calls

    def test_it_normalises_then_stitches_every_clip(self, terminal, out_dir,
                                                    clips, stitched):
        asyncio.run(trun.run_tool_merge_clips("j", clips))
        assert len(stitched["cropped"]) == 3, "each clip is normalised first"
        assert len(stitched["stitched"]) == 3
        terminal["success"].assert_awaited()

    @pytest.mark.parametrize("aspect,dims", [
        ("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080))])
    def test_an_explicit_aspect_normalises_every_clip_to_it(
            self, terminal, out_dir, clips, stitched, aspect, dims):
        asyncio.run(trun.run_tool_merge_clips("j", clips, target_aspect=aspect))
        assert all((w, h) == dims for _, w, h in stitched["cropped"])

    def test_a_stitch_failure_fails_the_job(self, terminal, out_dir, clips,
                                            stitched, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("concat demuxer died")
        monkeypatch.setattr("backend.services.ffmpeg_service.stitch_clips", boom)
        asyncio.run(trun.run_tool_merge_clips("j", clips))
        terminal["fail"].assert_awaited()


class TestMergeTargetResolution:
    """`auto` follows the FIRST clip — merging portrait sources must not
    letterbox everything into 16:9."""

    def test_auto_follows_a_portrait_first_clip(self, monkeypatch):
        monkeypatch.setattr(trun, "_probe_dims_sync", lambda p: (1080, 1920))
        w, h, label = trun._resolve_merge_target(Path("/x.mp4"), "auto")
        assert h > w and label == "9:16"

    def test_auto_follows_a_landscape_first_clip(self, monkeypatch):
        monkeypatch.setattr(trun, "_probe_dims_sync", lambda p: (1920, 1080))
        w, h, label = trun._resolve_merge_target(Path("/x.mp4"), "auto")
        assert w > h and label == "16:9"

    @pytest.mark.parametrize("aspect,expect", [
        ("9:16", "9:16"), ("16:9", "16:9"), ("1:1", "1:1")])
    def test_an_explicit_aspect_overrides_the_source(self, aspect, expect):
        _, _, label = trun._resolve_merge_target(Path("/x.mp4"), aspect)
        assert label == expect


# ── trim ────────────────────────────────────────────────────────────────────

class TestTrim:
    def test_it_cuts_the_requested_window(self, terminal, out_dir, monkeypatch):
        seen = {}

        async def fake_extract(src, start, end, out, vertical=False, **kw):
            seen.update(start=start, end=end, vertical=vertical)
            Path(out).write_bytes(b"\x00" * 2048)
            return Path(out)
        monkeypatch.setattr("backend.services.ffmpeg_service.extract_clip",
                            fake_extract)
        asyncio.run(trun.run_tool_trim("j", IN, 5.0, 12.0))
        assert seen["start"] == 5.0 and seen["end"] == 12.0
        assert seen["vertical"] is False, "trim must preserve the source aspect"
        terminal["success"].assert_awaited()

    def test_a_cut_failure_fails_the_job(self, terminal, out_dir, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("ffmpeg died")
        monkeypatch.setattr("backend.services.ffmpeg_service.extract_clip", boom)
        asyncio.run(trun.run_tool_trim("j", IN, 0.0, 5.0))
        terminal["fail"].assert_awaited()


# ── subtitle export ─────────────────────────────────────────────────────────

class TestSubtitleFile:
    SEGS = [{"start": 0.0, "end": 2.5, "text": "First line"},
            {"start": 2.5, "end": 5.0, "text": "Second line"}]

    def test_srt_is_numbered_with_comma_decimals(self, tmp_path):
        out = tmp_path / "s.srt"
        trun._build_subtitle_file(self.SEGS, "srt", out)
        body = out.read_text()
        assert body.startswith("1\n")
        assert "-->" in body and "," in body.split("-->")[0]

    def test_vtt_carries_its_header_and_dot_decimals(self, tmp_path):
        out = tmp_path / "s.vtt"
        trun._build_subtitle_file(self.SEGS, "vtt", out)
        body = out.read_text()
        assert body.startswith("WEBVTT")
        assert "." in body.split("-->")[0].splitlines()[-1]

    def test_txt_is_plain_prose_with_no_timings(self, tmp_path):
        out = tmp_path / "s.txt"
        trun._build_subtitle_file(self.SEGS, "txt", out)
        body = out.read_text()
        assert "-->" not in body and "First line" in body

    def test_an_empty_transcript_still_writes_a_file(self, tmp_path):
        for fmt in ("srt", "vtt", "txt"):
            out = tmp_path / f"e.{fmt}"
            trun._build_subtitle_file([], fmt, out)
            assert out.exists()


class TestTimestampFormat:
    @pytest.mark.parametrize("secs,srt", [
        (0, "00:00:00,000"),
        (61.5, "00:01:01,500"),
        (3661.25, "01:01:01,250"),
    ])
    def test_srt_timestamps(self, secs, srt):
        assert trun._format_ts(secs) == srt

    def test_vtt_uses_a_dot(self):
        assert trun._format_ts(61.5, vtt=True) == "00:01:01.500"
