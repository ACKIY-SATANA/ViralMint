# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The artifact gate: a job may not report success on a broken file.

"Verify the artifact before you call it done" existed as prose and was
enforced per-runner by whoever remembered. Every audit found the same shape of
bug — a runner finishes without raising, reports success, and hands back
something unplayable. These tests pin the shared gate that now sits in
`_tool_success`, the single point every file-producing tool passes through.

Two halves, and the second matters as much as the first:
  * it CATCHES the real historical failures (0-byte output, wrong geometry)
  * it does NOT false-positive on legitimate artifacts — a validator that
    fails work the user wanted is worse than no validator at all.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.services.output_validator import MIN_MEDIA_BYTES, validate_output


# ── fixtures: real media, built with ffmpeg ────────────────────────────────

def _mk_video(path: Path, seconds: float = 2, w: int = 320, h: int = 240,
              audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           "-i", f"testsrc=size={w}x{h}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-t", str(seconds), "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _mk_audio(path: Path, seconds: float = 2) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)],
        capture_output=True, check=True)
    return path


def _mk_image(path: Path, w: int = 320, h: int = 240) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=1:duration=1",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True)
    return path


def _v(path, expect=None):
    return asyncio.run(validate_output(path, expect))


# ── the failures we actually shipped ──────────────────────────────────────

def test_zero_byte_output_is_rejected(tmp_path):
    """Audio Enhance once reported success over exactly this."""
    empty = tmp_path / "out.mp3"
    empty.write_bytes(b"")
    r = _v(empty)
    assert not r.ok
    assert r.fatal_issues[0].check == "empty_file"
    assert "empty" in r.message().lower()


def test_truncated_file_is_rejected(tmp_path):
    """A partial write is not a smaller video — it's an unreadable one."""
    stub = tmp_path / "out.mp4"
    stub.write_bytes(b"\x00" * (MIN_MEDIA_BYTES + 64))
    r = _v(stub)
    assert not r.ok
    assert r.fatal_issues[0].check == "unreadable"


def test_missing_file_is_rejected(tmp_path):
    r = _v(tmp_path / "never-written.mp4")
    assert not r.ok
    assert r.fatal_issues[0].check == "missing_file"


def test_mp4_containing_only_audio_is_rejected(tmp_path):
    """A half-failed ffmpeg run yields a .mp4 with no picture in it."""
    src = _mk_audio(tmp_path / "a.mp3")
    mislabelled = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "aac",
                    str(mislabelled)], capture_output=True, check=True)
    r = _v(mislabelled)
    assert not r.ok
    assert r.fatal_issues[0].check == "no_video_stream"


def test_wrong_orientation_is_rejected_when_the_runner_asserted_it(tmp_path):
    """Reframe-to-vertical returning a landscape file is the blur_fill/reframe
    class of bug — invisible to any exit-code check."""
    landscape = _mk_video(tmp_path / "out.mp4", w=640, h=360)
    r = _v(landscape, {"orientation": "portrait"})
    assert not r.ok
    assert r.fatal_issues[0].check == "wrong_orientation"
    assert "landscape" in r.message() and "portrait" in r.message()


def test_short_output_is_rejected_when_a_minimum_was_declared(tmp_path):
    clip = _mk_video(tmp_path / "out.mp4", seconds=1)
    r = _v(clip, {"min_duration": 5.0})
    assert not r.ok
    assert r.fatal_issues[0].check == "too_short"


def test_missing_audio_is_rejected_when_the_runner_expected_it(tmp_path):
    silentish = _mk_video(tmp_path / "out.mp4", audio=False)
    r = _v(silentish, {"audio": True})
    assert not r.ok
    assert r.fatal_issues[0].check == "missing_audio"


# ── and, just as important, what it must NOT reject ────────────────────────

def test_a_normal_video_passes(tmp_path):
    r = _v(_mk_video(tmp_path / "out.mp4"))
    assert r.ok and r.has_video and r.has_audio
    assert r.width == 320 and r.height == 240
    assert r.duration > 1.5


def test_a_normal_audio_file_passes(tmp_path):
    r = _v(_mk_audio(tmp_path / "out.mp3"))
    assert r.ok and r.has_audio


def test_a_still_image_passes_despite_having_no_duration(tmp_path):
    """Thumbnails and AI images are single frames — a duration check would
    fail every one of them."""
    r = _v(_mk_image(tmp_path / "out.png"))
    assert r.ok, r.message()


def test_a_silent_video_passes_when_audio_was_not_demanded(tmp_path):
    """A GIF-to-mp4, a music visualiser still, a b-roll cutaway — silence is
    legitimate unless the runner said otherwise."""
    r = _v(_mk_video(tmp_path / "out.mp4", audio=False))
    assert r.ok, r.message()


def test_portrait_output_satisfies_a_portrait_expectation(tmp_path):
    r = _v(_mk_video(tmp_path / "out.mp4", w=360, h=640), {"orientation": "portrait"})
    assert r.ok, r.message()


def test_text_artifacts_are_size_checked_only(tmp_path):
    """.srt / .vtt / .zip have no streams; ffprobe has nothing to say."""
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n" * 40)
    assert _v(srt).ok
    empty = tmp_path / "empty.srt"
    empty.write_text("")
    assert not _v(empty).ok


def test_a_one_cue_subtitle_file_passes(tmp_path):
    """A 2-second clip's subtitles are ~40 bytes of real, wanted output —
    the media floor (512) must not apply to text artifacts. The first cut of
    the gate applied it to every suffix and would have failed legitimate
    short-clip subtitle / chapters / metadata exports."""
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n")
    assert srt.stat().st_size < 512  # the regression precondition
    r = _v(srt)
    assert r.ok, r.message()
    tiny_json = tmp_path / "meta.json"
    tiny_json.write_text('{"title": "T", "tags": ["a"]}')
    assert _v(tiny_json).ok


def test_validator_never_raises_on_a_hostile_path(tmp_path):
    """It runs on the success path of every tool — throwing here would turn a
    finished job into a lost one."""
    for p in (tmp_path, tmp_path / "no" / "such" / "dir" / "x.mp4", Path("/dev/null")):
        r = asyncio.run(validate_output(p))
        assert isinstance(r.ok, bool)


@pytest.mark.asyncio
async def test_tool_success_fails_the_job_instead_of_shipping_a_broken_file(tmp_path, monkeypatch):
    """The gate is WIRED into _tool_success, not merely available to it."""
    from backend.core import tool_runners

    calls: dict = {}

    async def fake_update(job_id, status, **kw):
        calls["status"] = status
        calls["error"] = kw.get("error_message")

    async def fake_send(msg, user_id):
        calls.setdefault("ws", []).append(msg.get("type"))

    monkeypatch.setattr("backend.agents.job_helper.update_job_status", fake_update)
    monkeypatch.setattr("backend.core.ws_manager.ws_manager.send", fake_send)

    empty = tmp_path / "out.mp4"
    empty.write_bytes(b"")
    delivered = await tool_runners._tool_success("job-1", empty, [], "local")

    assert delivered is False
    assert calls["status"] == "failed", "a 0-byte artifact was reported as success"
    assert "empty" in (calls["error"] or "").lower()
    assert "job_failed" in calls["ws"]


@pytest.mark.asyncio
async def test_tool_success_still_ships_a_good_file(tmp_path, monkeypatch):
    from backend.core import tool_runners

    calls: dict = {}

    async def fake_update(job_id, status, **kw):
        calls["status"] = status
        calls["output"] = kw.get("output_data")

    async def fake_send(msg, user_id):
        calls.setdefault("ws", []).append(msg.get("type"))

    monkeypatch.setattr("backend.agents.job_helper.update_job_status", fake_update)
    monkeypatch.setattr("backend.core.ws_manager.ws_manager.send", fake_send)

    good = _mk_video(tmp_path / "out.mp4")
    delivered = await tool_runners._tool_success("job-2", good, [], "local")

    assert delivered is True
    assert calls["status"] == "success"
    assert calls["output"]["file"] == str(good)
    assert "job_complete" in calls["ws"]


@pytest.mark.asyncio
async def test_the_reframe_runner_declares_its_geometry(tmp_path, monkeypatch):
    """Reframe's whole job is "make this vertical" — a convert that came back
    landscape is invisible to an exit code and obvious to ffprobe, so the
    runner must ASSERT it rather than hope."""
    from backend.core import tool_runners

    seen: dict = {}

    async def spy_success(job_id, out_path, cleanup, user_id, **kw):
        seen.update(kw)
        return True

    async def noop(*a, **k):
        return None

    landscape = _mk_video(tmp_path / "src.mp4", w=640, h=360)
    out = tmp_path / "out.mp4"

    async def fake_convert(*a, **k):
        _mk_video(out, w=360, h=640)
        return out

    monkeypatch.setattr(tool_runners, "_tool_success", spy_success)
    monkeypatch.setattr(tool_runners, "_tool_progress", noop)
    monkeypatch.setattr("backend.api.tools.tool_out_path", lambda job_id, ext: out)
    monkeypatch.setattr("backend.services.ffmpeg_service.convert_aspect_ratio", fake_convert)

    await tool_runners.run_tool_reframe("job-3", landscape)
    assert seen.get("expect") == {"orientation": "portrait"}


# ── A cue-less VTT must not pass ───────────────────────────────────────────
# "WEBVTT\n\n" is exactly 8 bytes and MIN_TEXT_BYTES is 8, so `size < floor`
# is False — a header-only subtitle export would slip through a pure size
# check while the SRT of the same empty export (1 byte) correctly failed. The
# two formats must not disagree, so VTT is judged on CONTENT: a real cue file
# has a timing arrow.

@pytest.mark.asyncio
async def test_a_header_only_vtt_is_rejected(tmp_path):
    from backend.services.output_validator import MIN_TEXT_BYTES, validate_output

    empty = tmp_path / "e.vtt"
    empty.write_text("WEBVTT\n\n", encoding="utf-8")
    assert empty.stat().st_size == MIN_TEXT_BYTES, (
        "the whole point: this is exactly ON the floor, so a size check "
        "waves it through"
    )
    res = await validate_output(empty)
    assert not res.ok, "a VTT with no cues must not report success"
    assert res.fatal_issues[0].check == "empty_file"


@pytest.mark.asyncio
async def test_a_real_vtt_still_passes(tmp_path):
    from backend.core.tool_runners import _build_subtitle_file
    from backend.services.output_validator import validate_output

    real = tmp_path / "r.vtt"
    _build_subtitle_file([{"start": 0.0, "end": 1.0, "text": "hello world"}], "vtt", real)
    res = await validate_output(real)
    assert res.ok


@pytest.mark.asyncio
async def test_our_own_cueless_export_also_fails(tmp_path):
    """Belt and braces: _build_subtitle_file with nothing to write lands at 7
    bytes, under the floor. The content check above is what holds if that
    writer ever gains a trailing newline."""
    from backend.core.tool_runners import _build_subtitle_file
    from backend.services.output_validator import validate_output

    out = tmp_path / "none.vtt"
    _build_subtitle_file([], "vtt", out)
    assert not (await validate_output(out)).ok
