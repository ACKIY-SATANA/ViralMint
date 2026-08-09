# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Probe a produced artifact before we call a job successful.

Every audit found the same shape of bug: a runner finishes without raising,
reports success, and hands the user back a file that is broken. Audio Enhance
"succeeded" with a 0-byte mp3. Blur-fill export shrank the picture to a third
of the frame. Reframe returned landscape. Each got its own one-off fix, which
means the NEXT one waits for the next audit — the rule ("verify the artifact
before you call it done") was enforced by whoever remembered, per runner.

This is that rule as code, in one place. `_tool_success` in core/tool_runners
is the single point every file-producing tool passes through, so a check here
covers all of them, including the next tool someone adds.

DESIGN — conservative on purpose. A validator that false-positives is worse
than no validator: it fails work the user actually wanted and erodes trust in
the whole gate. So only *unambiguous* breakage is fatal:

    fatal    the file is missing, empty, unparseable, has no stream of the kind
             its own extension promises, or has no duration
    warn     everything softer (all-black frames, digital silence) — recorded
             and logged, never blocks. A legitimately black cold-open or a
             deliberately silent clip must still ship.

Callers that know more can assert more via `expect` (e.g. reframe knows the
output must be portrait), and those extra assertions ARE fatal, because the
runner declared them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Below this a media file cannot be anything real — an empty ffmpeg output, a
# truncated write, an error page saved to disk. Deliberately generous: the
# smallest legitimate media artifact we ship is a short mp3 (a few KB) or a
# tiny GIF.
MIN_MEDIA_BYTES = 512

# Text artifacts get a far lower floor. A subtitle export of a 2-second clip
# is one cue (~40 bytes) and a chapters list can be under 100 — both are real,
# paid-for output. Only a truly empty file is unambiguous garbage.
MIN_TEXT_BYTES = 8

# Extension → the stream kind the file's own name promises. A ".mp4" with no
# video stream is a mislabelled audio file, which is exactly what a half-failed
# ffmpeg run produces.
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Text artifacts (.srt/.vtt/.txt/.json) get the existence + size check only —
# ffprobe has nothing to say about them.
_TEXT_EXTS = {".srt", ".vtt", ".txt", ".json", ".md", ".zip"}


@dataclass
class Issue:
    check: str      # stable machine id, e.g. "empty_file"
    detail: str     # human sentence, safe to show a user
    fatal: bool


@dataclass
class ValidationResult:
    ok: bool
    issues: list[Issue] = field(default_factory=list)
    width: int = 0
    height: int = 0
    duration: float = 0.0
    has_video: bool = False
    has_audio: bool = False

    @property
    def fatal_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.fatal]

    def message(self) -> str:
        """One sentence naming what's wrong, for the job's error_message."""
        fatal = self.fatal_issues
        return fatal[0].detail if fatal else ""


def _ffprobe(path: Path) -> dict | None:
    """Full stream + format probe. None when ffprobe can't read the file."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except FileNotFoundError:
        # ffprobe itself is missing — a broken dev box, not a broken artifact.
        # Signalled distinctly so the caller can skip rather than fail the job.
        raise
    except subprocess.TimeoutExpired:
        # We could not JUDGE the file — that is not evidence the file is bad.
        # Mapping this onto the same None as "unreadable" failed finished jobs,
        # and it fires precisely when the box is loaded (right after a full
        # re-encode). Signalled distinctly so the caller skips, exactly like a
        # missing ffprobe.
        raise
    except Exception:
        return None


def _validate_sync(path: Path, expect: dict | None) -> ValidationResult:
    expect = expect or {}
    res = ValidationResult(ok=True)

    def fail(check: str, detail: str) -> ValidationResult:
        res.issues.append(Issue(check, detail, fatal=True))
        res.ok = False
        return res

    if not path.exists():
        return fail("missing_file", "The tool reported success but wrote no output file.")

    suffix = path.suffix.lower()
    size = path.stat().st_size
    # Per-class floor: a 40-byte .srt is a real one-cue subtitle file; a
    # 40-byte .mp4 is nothing. The first cut of this check applied the media
    # floor to every suffix and would have failed legitimate short-clip
    # subtitle/chapters/metadata exports.
    floor = MIN_TEXT_BYTES if suffix in _TEXT_EXTS else MIN_MEDIA_BYTES
    if size < floor:
        return fail(
            "empty_file",
            f"The output file is empty ({size} bytes) — the render produced nothing usable.",
        )

    if suffix == ".vtt":
        # A cue-less WebVTT is exactly its 8-byte header ("WEBVTT\n\n"), which
        # is NOT below an 8-byte floor — so `size < floor` waved it through and
        # a subtitle file with no subtitles passed as a success. The SRT of the
        # same empty export is 1 byte and correctly failed, so the two formats
        # disagreed. Judge content, not just length: a real cue file has a
        # timing arrow.
        try:
            if "-->" not in path.read_text(encoding="utf-8", errors="replace"):
                return fail(
                    "empty_file",
                    "The subtitle file has no cues — the export produced "
                    "nothing usable.",
                )
        except OSError:
            pass  # unreadable here → let the existence/size checks stand

    if suffix in _TEXT_EXTS:
        return res  # existence + a non-empty body is the whole contract for text artifacts

    try:
        probe = _ffprobe(path)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # We cannot judge (no ffprobe, or it timed out on a loaded box), so we
        # do not block — failing here would kill a finished job on an
        # ambiguity rather than on evidence.
        logger.warning("output_validator: cannot probe (missing/timeout), skipping media checks")
        return res

    if probe is None:
        return fail(
            "unreadable",
            "The output file couldn't be read back as media — it's corrupt or truncated.",
        )

    streams = probe.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    res.has_video, res.has_audio = bool(video), bool(audio)

    if video:
        try:
            res.width = int(video[0].get("width") or 0)
            res.height = int(video[0].get("height") or 0)
        except (TypeError, ValueError):
            pass
    try:
        res.duration = float((probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        res.duration = 0.0

    # An image is a single video stream with no meaningful duration — checking
    # duration on a .png would fail every thumbnail we produce.
    is_image = suffix in _IMAGE_EXTS

    if suffix in _VIDEO_EXTS and not video:
        return fail(
            "no_video_stream",
            "The output is named as a video but contains no video track.",
        )
    if suffix in _AUDIO_EXTS and not audio:
        return fail(
            "no_audio_stream",
            "The output is named as audio but contains no audio track.",
        )
    if is_image and not video:
        return fail("no_image_data", "The output image contains no decodable image data.")

    if not is_image and res.duration <= 0:
        return fail(
            "zero_duration",
            "The output file has no duration — nothing was actually rendered.",
        )

    # ── caller-declared expectations (fatal: the runner asserted these) ──
    want_min_dur = expect.get("min_duration")
    if want_min_dur and res.duration and res.duration + 0.25 < want_min_dur:
        return fail(
            "too_short",
            f"The output is {res.duration:.1f}s but at least {want_min_dur:.1f}s was expected.",
        )

    want_orientation = expect.get("orientation")  # "portrait" | "landscape" | "square"
    if want_orientation and res.width and res.height:
        actual = ("square" if res.width == res.height
                  else "portrait" if res.height > res.width else "landscape")
        if actual != want_orientation:
            return fail(
                "wrong_orientation",
                f"The output came out {actual} ({res.width}x{res.height}) but "
                f"{want_orientation} was requested.",
            )

    if expect.get("audio") and not res.has_audio:
        return fail("missing_audio", "The output has no audio track, but audio was expected.")

    return res


async def validate_output(
    path: Path | str, expect: dict | None = None
) -> ValidationResult:
    """Validate a produced artifact. Never raises — a validator that throws
    would turn a cosmetic problem into a lost job.

    `expect` (all optional): {"min_duration": float, "orientation": str,
    "audio": bool}. Anything asserted here is fatal on mismatch, because the
    runner claimed it.
    """
    try:
        return await asyncio.to_thread(_validate_sync, Path(path), expect)
    except Exception as e:  # noqa: BLE001 — last-resort guard, see docstring
        logger.warning("output_validator crashed on %s: %s", path, e)
        return ValidationResult(ok=True)
