# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend.services.sfx_service — SFX auto-placement + FFmpeg mixing.

Placement is pure logic over word timestamps (keyword triggers, transition
whooshes, pause bass-drops, spacing + count caps). Mixing/generation shell out
to FFmpeg — those are mocked so nothing spawns a process, and we assert the
argv is a real list (never shell=True string-concat, per rule #27c).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import sfx_service as sfx
from backend.services.sfx_service import SFXType, SFX_TRIGGER_KEYWORDS


@pytest.fixture
def fake_sfx_dir(tmp_path, monkeypatch):
    """Point SFX_DIR at an existing tmp dir so auto_place_sfx never triggers
    ensure_sfx_dir() (which would shell out to FFmpeg)."""
    monkeypatch.setattr(sfx, "SFX_DIR", tmp_path)
    return tmp_path


def _words(*specs):
    return [{"text": t, "start": s, "end": e} for (t, s, e) in specs]


# ── static mappings ────────────────────────────────────────────────────────

def test_trigger_keywords_map_to_valid_sfx_types():
    for kw, t in SFX_TRIGGER_KEYWORDS.items():
        assert isinstance(t, SFXType)
    assert SFX_TRIGGER_KEYWORDS["amazing"] == SFXType.POP
    assert SFX_TRIGGER_KEYWORDS["million"] == SFXType.DING
    assert SFX_TRIGGER_KEYWORDS["but"] == SFXType.WHOOSH


# ── auto_place_sfx ─────────────────────────────────────────────────────────

async def test_style_none_returns_empty():
    assert await sfx.auto_place_sfx(_words(("amazing", 1.0, 1.5)), style="none") == []


async def test_empty_timestamps_returns_empty():
    assert await sfx.auto_place_sfx([], style="moderate") == []


async def test_keyword_triggers_placement(fake_sfx_dir):
    fake = Path("/fake/pop.mp3")
    with patch.object(sfx, "_get_sfx_path", return_value=fake):
        out = await sfx.auto_place_sfx(_words(("amazing", 2.0, 2.5)), style="moderate")
    assert len(out) == 1
    assert out[0]["sfx_type"] == SFXType.POP.value
    assert out[0]["timestamp"] == 2.0


async def test_clip_boundary_places_whoosh(fake_sfx_dir):
    fake = Path("/fake/whoosh.mp3")
    with patch.object(sfx, "_get_sfx_path", return_value=fake):
        out = await sfx.auto_place_sfx(
            _words(("hello", 0.1, 0.4)), clip_boundaries=[3.0], style="minimal")
    assert any(p["sfx_type"] == SFXType.WHOOSH.value and p["timestamp"] == 3.0 for p in out)


async def test_min_interval_spacing_enforced(fake_sfx_dir):
    # Two triggers 1s apart with moderate min_interval=5.0 → only the first.
    with patch.object(sfx, "_get_sfx_path", return_value=Path("/fake/pop.mp3")):
        out = await sfx.auto_place_sfx(
            _words(("amazing", 2.0, 2.5), ("incredible", 3.0, 3.5)), style="moderate")
    assert len(out) == 1


async def test_no_sfx_file_yields_no_placement(fake_sfx_dir):
    with patch.object(sfx, "_get_sfx_path", return_value=None):
        out = await sfx.auto_place_sfx(_words(("amazing", 2.0, 2.5)), style="moderate")
    assert out == []


async def test_invalid_timestamp_is_skipped(fake_sfx_dir):
    words = [{"text": "amazing", "start": "oops", "end": 2.5}]
    with patch.object(sfx, "_get_sfx_path", return_value=Path("/fake/pop.mp3")):
        out = await sfx.auto_place_sfx(words, style="moderate")
    assert out == []


async def test_pause_detection_places_bass_drop(fake_sfx_dir):
    # Gap of ~1s between word 1 end (1.0) and word 2 start (2.0) → bass_drop.
    words = _words(("hello", 0.0, 1.0), ("world", 2.0, 2.5))

    def _path(t):
        return Path(f"/fake/{t.value}.mp3")

    with patch.object(sfx, "_get_sfx_path", side_effect=_path):
        out = await sfx.auto_place_sfx(words, style="heavy")
    assert any(p["sfx_type"] == SFXType.BASS_DROP.value for p in out)


async def test_output_sorted_by_timestamp(fake_sfx_dir):
    with patch.object(sfx, "_get_sfx_path", side_effect=lambda t: Path(f"/f/{t.value}.mp3")):
        out = await sfx.auto_place_sfx(
            _words(("first", 0.0, 0.5), ("but", 12.0, 12.5)),
            clip_boundaries=[6.0], style="heavy")
    ts = [p["timestamp"] for p in out]
    assert ts == sorted(ts)


# ── mix_sfx_into_audio ─────────────────────────────────────────────────────

async def test_mix_no_placements_returns_original(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"x")
    assert await sfx.mix_sfx_into_audio(audio, []) == audio


async def test_mix_success_returns_output_and_uses_argv_list(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"x")
    sfx_file = tmp_path / "ding.mp3"
    sfx_file.write_bytes(b"y")
    placements = [{"timestamp": 1.0, "sfx_type": "ding", "sfx_path": str(sfx_file), "volume_db": -10}]

    with patch.object(sfx.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stderr="")
        out = await sfx.mix_sfx_into_audio(audio, placements)

    assert out == audio.parent / "voice_sfx.mp3"
    cmd = run.call_args.args[0]
    assert isinstance(cmd, list) and cmd[0] == "ffmpeg"
    assert run.call_args.kwargs.get("shell") in (None, False)


async def test_mix_missing_sfx_file_returns_original(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"x")
    placements = [{"timestamp": 1.0, "sfx_path": str(tmp_path / "nope.mp3"), "volume_db": -10}]
    # No filter parts built → returns original without invoking ffmpeg.
    with patch.object(sfx.subprocess, "run") as run:
        out = await sfx.mix_sfx_into_audio(audio, placements)
    assert out == audio and run.call_count == 0


async def test_mix_ffmpeg_failure_returns_original(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"x")
    sfx_file = tmp_path / "ding.mp3"
    sfx_file.write_bytes(b"y")
    placements = [{"timestamp": 1.0, "sfx_path": str(sfx_file), "volume_db": -10}]
    with patch.object(sfx.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1, stderr="boom")
        out = await sfx.mix_sfx_into_audio(audio, placements)
    assert out == audio


# ── ensure_sfx_dir ─────────────────────────────────────────────────────────

def test_ensure_sfx_dir_builds_argv_per_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx, "SFX_DIR", tmp_path)
    with patch.object(sfx.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stderr="")
        sfx.ensure_sfx_dir()
    # One ffmpeg invocation per spec (6 SFX), all argv lists, none shell.
    assert run.call_count == 6
    for call in run.call_args_list:
        cmd = call.args[0]
        assert isinstance(cmd, list) and cmd[0] == "ffmpeg"


# ── The mix's two measured bugs ─────────────────────────────────────────────
#
# Both lived in flag interactions, so the first is proven with real ffmpeg and
# a loudness measurement rather than a mock.


@pytest.mark.asyncio
async def test_heavy_style_is_not_truncated_below_its_own_cap(fake_sfx_dir):
    """The planner offered "heavy · up to 25" while the mixer stopped at 15, so
    a heavy run silently dropped a third of its effects and still reported the
    planned count. The two caps now come from one place."""
    assert sfx.STYLE_MAX_SFX["heavy"] <= sfx.MAX_MIXED_SFX


@pytest.mark.asyncio
async def test_mix_sums_instead_of_averaging(tmp_path):
    """amix defaults to normalize=1, which divides by the number of ACTIVE
    inputs. Every adelay'd SFX counts as active from t=0 (its pre-roll silence
    isn't EOF), so the voice was scaled by ~1/n at the start and swelled louder
    as each effect ended — a monotonic ramp, not a mix."""
    captured = {}

    class _Res:
        returncode = 0
        stderr = ""

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x" * 2048)
        return _Res()

    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x" * 2048)
    effect = tmp_path / "ding.mp3"
    effect.write_bytes(b"x" * 512)

    with patch("subprocess.run", new=_run):
        await sfx.mix_sfx_into_audio(
            voice,
            [{"sfx_path": str(effect), "timestamp": 1.0, "volume_db": -10}],
            output_path=tmp_path / "out.mp3",
        )

    fc = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "normalize=0" in fc, "amix must sum, not average"
    # Summing can peak above full scale, so the limiter is part of the fix.
    assert "alimiter" in fc
