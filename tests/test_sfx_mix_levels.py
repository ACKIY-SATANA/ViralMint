# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The SFX mix must not make the voice ramp up in volume.

`amix` defaults to `normalize=1`, which divides the output by the number of
*currently-active* inputs. Sound effects are short clips `adelay`'d across the
timeline, and an adelay'd input counts as active from t=0 — its pre-roll is
silence, not EOF. So with N effects the voice starts scaled by ~1/N and gets
progressively LOUDER as each effect ends: a monotonic ramp, not a mix.

Measured here with real ffmpeg on a 20 s tone plus 8 effects:

    normalize=1 (old):  -41.0 dB at the start → -24.4 dB at the end  (16.6 dB ramp)
    normalize=0 (new):  -21.5 dB              → -21.6 dB             (flat)

`alimiter` is the other half: summing instead of averaging can peak above full
scale, and the limiter catches that so the sum can't clip.

Real ffmpeg by necessity — this is a filtergraph behaviour no mock can witness.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services.sfx_service import MAX_MIXED_SFX

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH",
)

N_EFFECTS = 8
DURATION = 20


def _build_graph(tmp_path: Path, *, normalize_off: bool) -> Path:
    voice = tmp_path / "voice.mp3"
    ding = tmp_path / "ding.mp3"
    for path, freq, dur in ((voice, 220, DURATION), (ding, 880, 0.4)):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
             "-c:a", "libmp3lame", str(path)],
            capture_output=True, check=True, timeout=60,
        )

    inputs, parts, labels = [], [], []
    for i in range(N_EFFECTS):
        ms = (i + 1) * 2000
        inputs += ["-i", str(ding)]
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms},volume=-10dB[sfx{i}]")
        labels.append(f"[sfx{i}]")

    n_inputs = N_EFFECTS + 1
    mix = (f"[0:a]{''.join(labels)}amix=inputs={n_inputs}:duration=first:"
           f"dropout_transition=2")
    tail = (f":normalize=0[mixraw];[mixraw]alimiter=limit=0.95[out]"
            if normalize_off else "[out]")
    out = tmp_path / ("new.mp3" if normalize_off else "old.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(voice), *inputs,
         "-filter_complex", ";".join(parts) + ";" + mix + tail,
         "-map", "[out]", "-c:a", "libmp3lame", str(out)],
        capture_output=True, check=True, timeout=120,
    )
    return out


def _mean_db(path: Path, start: int, length: int = 3) -> float:
    r = subprocess.run(
        ["ffmpeg", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    assert m, f"no mean_volume in ffmpeg output for {path}"
    return float(m.group(1))


def test_normalize_off_keeps_the_voice_at_a_steady_level(tmp_path):
    out = _build_graph(tmp_path, normalize_off=True)
    start, end = _mean_db(out, 0), _mean_db(out, DURATION - 4)
    assert abs(end - start) < 3.0, (
        f"voice level drifted {end - start:+.1f} dB across the clip "
        f"({start:.1f} → {end:.1f})"
    )


def test_the_default_amix_really_does_ramp(tmp_path):
    """Guards the REASON for the fix. If a future ffmpeg changes amix's
    normalize default, this fails and tells the next reader the comment above
    is now describing history."""
    out = _build_graph(tmp_path, normalize_off=False)
    start, end = _mean_db(out, 0), _mean_db(out, DURATION - 4)
    assert end - start > 6.0, (
        "expected the un-normalized default to swell; measured "
        f"{start:.1f} → {end:.1f} dB"
    )


def test_the_mixer_ceiling_covers_the_densest_style():
    """A mixer cap below the planner's densest style silently discards work the
    user was told they'd get."""
    from backend.services.sfx_service import STYLE_MAX_SFX
    assert max(STYLE_MAX_SFX.values()) <= MAX_MIXED_SFX
