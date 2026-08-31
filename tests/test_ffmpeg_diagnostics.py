# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""ffmpeg failures must say what went wrong, and must not keep trying.

Two defects that a green suite could not see, because neither one FAILS
anything — they just make a failure undiagnosable and expensive.
"""
import asyncio
import subprocess

import pytest


# ── ffmpeg errors must say what went wrong ──────────────────────────────────


def test_ffmpeg_error_drops_the_banner_and_keeps_the_cause():
    """ffmpeg prints ~200 chars of version banner before it says anything, so
    `stderr[:200]` logs a constant string and discards the diagnosis: every
    thumbnail failure logged the identical "ffmpeg version 7.1 Copyright ...
    configuration: --prefix=" with no cause attached."""
    from backend.services.ffmpeg_service import ffmpeg_error

    stderr = (
        "ffmpeg version 7.1 Copyright (c) 2000-2024 the FFmpeg developers\n"
        "  built with Apple clang version 13.1.6\n"
        "  configuration: --prefix=/Volumes/tempdisk/sw --extra-cflags=-fno-stack\n"
        "  libavutil      59. 39.100 / 59. 39.100\n"
        "[in#0 @ 0x99] moov atom not found\n"
        "Error opening input: Invalid data found when processing input\n"
    )
    out = ffmpeg_error(stderr)
    assert "moov atom not found" in out
    assert "Invalid data found" in out
    assert "ffmpeg version" not in out and "configuration:" not in out


def test_ffmpeg_error_never_returns_nothing():
    """It feeds a log line on a failure path; returning "" would replace one
    unhelpful message with no message."""
    from backend.services.ffmpeg_service import ffmpeg_error
    assert ffmpeg_error("") == "(no stderr)"
    assert ffmpeg_error("ffmpeg version 7.1 only, no error lines")


def test_the_failing_call_sites_use_it():
    """The helper is only worth having where the bad truncation was. Both
    sites feed the Clipper bench, which asks for frames on every drag."""
    import inspect

    from backend.services import ffmpeg_service

    for fn in (ffmpeg_service.extract_thumbnail, ffmpeg_service.extract_frame_at):
        src = inspect.getsource(fn)
        assert "ffmpeg_error(" in src, f"{fn.__name__} still truncates from the front"
        assert "stderr[:" not in src, f"{fn.__name__} still truncates from the front"


# ── a file that decodes nothing must be asked once, not 32 times ────────────


def test_the_filmstrip_stops_after_one_undecodable_cell(tmp_path):
    """A file that decodes NO frames used to cost 2N ffmpeg spawns — every
    cell failed and every failure retried once — plus N log lines, repeated on
    every request, because the bench rebuilds the strip each time that source
    is selected.

    The realistic source is not junk: a download cancelled mid-write leaves a
    valid header, a plausible duration, and no usable data. A user produces one
    by pressing Stop.
    """
    from backend.services import ffmpeg_service
    from backend.services.ffmpeg_service import extract_filmstrip

    good = tmp_path / "good.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-t", "6", "-movflags", "+faststart", str(good)],
        capture_output=True)
    if r.returncode != 0 or not good.exists():
        pytest.skip("ffmpeg not available to build the fixture")

    raw = good.read_bytes()
    partial = tmp_path / "partial.mp4"
    partial.write_bytes(raw[:len(raw) // 12])   # header survives, frames don't

    calls = []
    real = ffmpeg_service.extract_frame_at

    async def counting(*a, **kw):
        calls.append(kw.get("timestamp"))
        return await real(*a, **kw)

    ffmpeg_service.extract_frame_at = counting
    try:
        out = asyncio.run(extract_filmstrip(
            video_path=partial, output_path=tmp_path / "s.jpg", count=16))
        assert out is None, "a strip cannot be built from a file with no frames"
        assert len(calls) <= 2, (
            f"asked for {len(calls)} frames from a file that decodes none — "
            f"the fail-fast probe is gone"
        )

        calls.clear()
        assert asyncio.run(extract_filmstrip(
            video_path=good, output_path=tmp_path / "ok.jpg", count=8)), (
            "a healthy source must still build a strip")
        assert len(calls) >= 8, "the good path must still extract every cell"
    finally:
        ffmpeg_service.extract_frame_at = real


def test_the_filmstrip_stops_when_only_the_TAIL_is_missing(tmp_path):
    """The commoner damage shape, and the one a head-only probe misses.

    A download truncated at the END keeps a valid header, a plausible duration
    AND its opening frames — so cell 0 succeeds and every later cell fails.
    Measured live before this was fixed: a 45s source cut to 7% of its bytes
    passed the probe and then burned 61 ffmpeg spawns on cells that could
    never decode, every time the source was selected.

    Bailing is outcome-identical: a strip needs every cell, so a tail that
    cannot decode fails the strip either way.
    """
    from backend.services import ffmpeg_service
    from backend.services.ffmpeg_service import extract_filmstrip

    good = tmp_path / "good.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
         "-t", "45", "-movflags", "+faststart", str(good)],
        capture_output=True)
    if r.returncode != 0 or not good.exists():
        pytest.skip("ffmpeg not available to build the fixture")

    raw = good.read_bytes()
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(raw[:int(len(raw) * 0.07)])

    # The fixture has to be the shape the test claims: a readable HEAD.
    head = asyncio.run(ffmpeg_service.extract_frame_at(
        video_path=truncated, timestamp=0.0, output_path=tmp_path / "head.jpg"))
    if not head:
        pytest.skip("this ffmpeg build decodes nothing from the fixture — "
                    "the head-failure case already covers that")

    calls = []
    real = ffmpeg_service.extract_frame_at

    async def counting(*a, **kw):
        calls.append(kw.get("timestamp"))
        return await real(*a, **kw)

    ffmpeg_service.extract_frame_at = counting
    try:
        out = asyncio.run(extract_filmstrip(
            video_path=truncated, output_path=tmp_path / "s.jpg", count=32))
        assert out is None, "a strip cannot be built without its last cell"
        assert len(calls) <= 4, (
            f"asked for {len(calls)} frames from a source whose tail cannot "
            f"decode — the head probe passed and the tail probe is missing"
        )
    finally:
        ffmpeg_service.extract_frame_at = real


def test_a_healthy_source_still_builds_every_cell(tmp_path):
    """The probes must not cost the good path anything. Both ends are cells
    the strip needs anyway, so they are reused, not re-extracted."""
    from backend.services import ffmpeg_service
    from backend.services.ffmpeg_service import extract_filmstrip

    good = tmp_path / "good.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-t", "30", "-movflags", "+faststart", str(good)],
        capture_output=True)
    if r.returncode != 0:
        pytest.skip("ffmpeg not available to build the fixture")

    calls = []
    real = ffmpeg_service.extract_frame_at

    async def counting(*a, **kw):
        calls.append(kw.get("timestamp"))
        return await real(*a, **kw)

    ffmpeg_service.extract_frame_at = counting
    try:
        out = asyncio.run(extract_filmstrip(
            video_path=good, output_path=tmp_path / "ok.jpg", count=16))
        assert out and out.exists() and out.stat().st_size > 1000
        # Exactly 16 extractions: no cell is done twice by the probes.
        assert len(calls) == 16, f"expected 16 extractions, got {len(calls)}"
        assert len(set(calls)) == 16, "a cell was extracted more than once"
    finally:
        ffmpeg_service.extract_frame_at = real
