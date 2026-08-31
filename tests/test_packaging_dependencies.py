# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Dependencies the PACKAGED app needs, which the source tree never misses.

The class of bug this guards: a dependency that only the frozen build relies
on, so removing it passes every test and every local run, and the failure
appears once — in a shipped binary, on a user's machine.

`imageio_ffmpeg` is exactly that. Nothing imports it; the PyInstaller spec
`collect_all`s it to bundle a static ffmpeg per platform, and the source tree
finds ffmpeg on PATH instead. It used to arrive only as a transitive
dependency of moviepy, which nothing imported either — so "drop the unused
moviepy" would have quietly shipped an app with no ffmpeg.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQS = ROOT / "requirements.txt"
SPEC = ROOT / "desktop" / "scripts" / "viralmint.spec"


def _requirement_names() -> set[str]:
    names = set()
    for line in REQS.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)", line)
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def test_every_collect_all_in_the_spec_is_a_real_requirement():
    """A `collect_all("x")` for a package nothing requires silently collects
    NOTHING — PyInstaller warns and carries on, and the binary ships without
    whatever x provided."""
    spec = SPEC.read_text()
    collected = set(re.findall(r'collect_all\(\s*["\']([A-Za-z0-9._-]+)["\']', spec))
    # Names the spec collects from a loop variable rather than a literal are
    # listed in that loop; this checks the literal ones.
    reqs = _requirement_names()
    # stdlib / PyInstaller-provided names that are not pip requirements
    exempt = {"tkinter"}
    missing = {c for c in collected
               if c.lower().replace("_", "-") not in reqs and c not in exempt}
    assert not missing, (
        f"the packaging spec collects {sorted(missing)}, which requirements.txt "
        f"does not require — the packaged build would ship without it"
    )


def test_imageio_ffmpeg_is_required_directly():
    """It must not go back to being someone else's transitive dependency: the
    package that used to supply it (moviepy) was removed as unused, and the
    next person to prune an 'unused' dep should hit this test, not a user."""
    assert "imageio-ffmpeg" in _requirement_names()


def test_imageio_ffmpeg_actually_provides_a_working_ffmpeg():
    """The point of the dependency. A wheel that installs but ships no usable
    binary would satisfy every other check here."""
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
    assert exe.exists(), f"imageio_ffmpeg reports {exe}, which does not exist"
    out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[-300:]
    assert "ffmpeg version" in out.stdout


def test_the_moviepy_shim_went_with_moviepy():
    """backend/main.py monkeypatched Pillow's removed ANTIALIAS constants back
    on, solely for moviepy 1.0.3. Leaving a monkeypatch behind after its one
    consumer is gone is how a codebase accumulates rules nobody can explain."""
    main = (ROOT / "backend" / "main.py").read_text()
    assert "ANTIALIAS" not in main
    assert "moviepy" not in main


def test_no_requirement_pins_a_version_below_its_documented_floor():
    """The two floors in this file that are safety decisions, not freshness:
    curl-cffi <0.15 SIGABRTs the whole process on concurrent TikTok probes,
    and an unbounded yt-dlp resolves to an ancient build on an old Python."""
    text = REQS.read_text()
    m = re.search(r"^curl-cffi>=(\d+)\.(\d+)", text, re.M)
    assert m, "the curl-cffi floor is gone"
    assert (int(m.group(1)), int(m.group(2))) >= (0, 15), (
        "curl-cffi floor dropped below 0.15 — see the comment above it"
    )
    assert re.search(r"^yt-dlp>=\d{4}\.\d+", text, re.M), "the yt-dlp floor is gone"
