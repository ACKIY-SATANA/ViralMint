# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Shared capped-subprocess runner for the Motion Graphics (HyperFrames) plugin.

The npm-install step (hyperframes_service) and the render step
(motion_render_service) both spawn a Node subprocess and need the SAME loop:
stream stdout (stderr merged) line-by-line with a per-read idle timeout, enforce
an overall wall-clock cap (kill + drain on breach), keep a short tail for error
detail, and report the exit code. That loop was duplicated; centralizing it here
keeps the kill/drain edge cases written once.

NOT used by the studio preview server (studio_service), which spawns with DEVNULL
and polls an HTTP endpoint instead of streaming stdout — a different pattern.
"""
import asyncio
import platform
import time
from typing import Callable, Optional

__all__ = ["run_capped"]


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill a capped subprocess AND its descendants on a timeout breach.

    On Windows, ``proc.kill()`` is TerminateProcess — parent only. The
    HyperFrames CLI is a Node process that spawns Chromium + ffmpeg
    grandchildren; killing only node on a cap breach orphans a headless
    Chromium that keeps burning CPU (2026-07-10 Windows stability audit).
    ``taskkill /T /F`` takes the whole tree. POSIX keeps plain kill() —
    the node process group dies with it in practice, and changing
    process-group semantics there risks the working macOS path.
    """
    if platform.system() == "Windows":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=10.0)
        except Exception:
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass


async def run_capped(
    cmd: list[str],
    *,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    overall_timeout: float,
    line_timeout: float = 15.0,
    silence_timeout: Optional[float] = None,
    tail_lines: int = 12,
    on_line: Optional[Callable[[str], None]] = None,
    on_idle: Optional[Callable[[], None]] = None,
) -> tuple[int, list[str], bool]:
    """Run ``cmd`` to completion (or until the cap), streaming stdout.

    Returns ``(returncode, tail, timed_out)``:
      - ``tail``     — last ``tail_lines`` non-empty stdout lines (for error detail).
      - ``timed_out``— True iff ``overall_timeout`` was hit (the process was then
                       killed); callers should treat this as a failure regardless
                       of ``returncode``.

    ``on_line(line)`` fires for each non-empty line; ``on_idle()`` fires when a
    read times out while the process is still running (e.g. to heartbeat a
    progress bar). Spawn failures propagate to the caller (wrap in try/except).

    ``silence_timeout`` (optional) kills the process after that many seconds
    with NO output at all. It separates "this job is long" from "this job is
    stuck", which a wall-clock cap alone cannot do: a 30s motion piece with a
    video background legitimately renders for minutes, so a cap tight enough to
    catch a hang would kill healthy work, and a cap loose enough for the long
    job lets a hang sit there instead. Progress output resets the clock.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    start = time.monotonic()
    last_output = start
    timed_out = False
    while True:
        now = time.monotonic()
        if now - start > overall_timeout:
            await _kill_tree(proc)
            timed_out = True
            break
        if silence_timeout and now - last_output > silence_timeout:
            await _kill_tree(proc)
            timed_out = True
            break
        try:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=line_timeout)
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break
            if on_idle:
                on_idle()
            continue
        if not line_bytes:
            break
        last_output = time.monotonic()
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        tail.append(line)
        if len(tail) > tail_lines:
            tail.pop(0)
        if on_line:
            on_line(line)

    rc = proc.returncode if proc.returncode is not None else await proc.wait()
    return rc, tail, timed_out
