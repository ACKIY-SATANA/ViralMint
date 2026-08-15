# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The merge normalizer must normalize the stream LAYOUT, not just codec/fps.

`-c:a aac` has nothing to encode on an input with no audio stream, so a silent
source normalized to a video-ONLY clip. The concat demuxer then takes the first
file's layout:

  * silent clip FIRST + talking clip second → merge with NO audio stream at
    all — the user's audio silently discarded, job "success";
  * talking clip first + silent second → the audio track ends mid-file.

A silent rendered intro card in front of a talking video is the common real
shape of the first case. The crossfade path has the same trigger — it drops
audio entirely when ANY clip is audio-less — so the fix lives in the
normalizer: every clip that passes through comes out WITH an audio track.

These tests invoke real ffmpeg on tiny generated fixtures — the bug was in the
interaction between ffmpeg flags, which no mock can witness.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core.tool_runners import _crop_to_aspect_sync

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_clip(path: Path, seconds: float, *, with_audio: bool) -> None:
    cmd = ["ffmpeg", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=15:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=60)


def _stream_types(path: Path) -> list[str]:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _durations(path: Path) -> tuple[float, float]:
    """(video_seconds, audio_seconds) — audio 0.0 when there's no track."""
    def probe(selector: str) -> float:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", selector,
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        out = r.stdout.strip().splitlines()
        try:
            return float(out[0])
        except (IndexError, ValueError):
            return 0.0
    return probe("v:0"), probe("a:0")


async def _normalize(src: Path, out: Path) -> None:
    await asyncio.to_thread(_crop_to_aspect_sync, src, out, 640, 360)


@pytest.mark.asyncio
async def test_silent_source_gains_a_full_length_silent_track(tmp_path):
    src = tmp_path / "silent.mp4"
    _make_clip(src, 3.0, with_audio=False)
    out = tmp_path / "norm.mp4"

    await _normalize(src, out)

    assert sorted(_stream_types(out)) == ["audio", "video"], \
        "a silent source must still normalize to video+audio"
    vdur, adur = _durations(out)
    # The injected silence must cover the whole video, not a token stub — a
    # shorter track recreates the mid-file audio cutoff in a later concat.
    assert adur == pytest.approx(vdur, abs=0.35)


@pytest.mark.asyncio
async def test_audio_source_keeps_its_audio(tmp_path):
    src = tmp_path / "talking.mp4"
    _make_clip(src, 3.0, with_audio=True)
    out = tmp_path / "norm.mp4"

    await _normalize(src, out)

    assert sorted(_stream_types(out)) == ["audio", "video"]
    vdur, adur = _durations(out)
    assert adur == pytest.approx(vdur, abs=0.35)


@pytest.mark.asyncio
async def test_normalized_layouts_match_across_silent_and_talking(tmp_path):
    """The property the concat demuxer actually depends on: every clip that
    passes through the normalizer has the SAME stream layout, whatever the
    source had. This is the merge-goes-mute bug stated as an invariant."""
    silent, talking = tmp_path / "s.mp4", tmp_path / "t.mp4"
    _make_clip(silent, 2.0, with_audio=False)
    _make_clip(talking, 2.0, with_audio=True)

    layouts = []
    for i, src in enumerate((silent, talking)):
        out = tmp_path / f"n{i}.mp4"
        await _normalize(src, out)
        layouts.append(sorted(_stream_types(out)))

    assert layouts[0] == layouts[1] == ["audio", "video"]
