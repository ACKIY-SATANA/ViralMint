# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The `mute` transform operation — strip a video's audio track.

Distinct from the `volume=0` it replaces in the UI. Both were run against
ffmpeg 7.1 rather than assumed:

  * `volume=0` leaves a SILENT AAC stream in the output (probe: video+audio).
    `-an` removes the track (probe: video only). For "I don't want sound on
    this", quiet is not the same as gone.
  * `-an` skips the audio re-encode entirely, so it's near-instant on a long
    clip. (Both paths stream-copy the video, so neither costs picture quality.)

The no-audio-source case still produces a file: the user asked for a video
with no sound and that's already true, so failing would be wrong. It warns
instead, because handing back an unchanged file with no explanation looks
broken.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.core import tool_runners as tr


@pytest.fixture
def terminal_spies():
    with patch.object(tr, "_tool_progress", new=AsyncMock()) as prog, \
         patch.object(tr, "_tool_success", new=AsyncMock()) as ok, \
         patch.object(tr, "_tool_fail", new=AsyncMock()) as fail:
        yield {"progress": prog, "success": ok, "fail": fail}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 1000)
    monkeypatch.setattr(
        "backend.api.tools.tool_out_path",
        lambda job_id, ext=".mp4": tmp_path / f"{job_id}{ext}",
    )
    return {"src": src, "dir": tmp_path}


def _capture_cmd(store: list):
    """Patch subprocess.run inside the runner and record the argv."""
    class _Res:
        returncode = 0
        stderr = ""

    def _run(cmd, **kwargs):
        store.append(cmd)
        return _Res()

    return _run


@pytest.mark.asyncio
class TestMuteOperation:
    async def _run_mute(self, paths, job, *, has_audio=True):
        cmds: list = []
        (paths["dir"] / f"{job}.mp4").write_bytes(b"y" * 500)
        with patch("subprocess.run", new=_capture_cmd(cmds)), \
             patch("backend.services.ffmpeg_service.has_audio_stream",
                   new=AsyncMock(return_value=has_audio)), \
             patch("backend.core.ws_manager.ws_manager.send_constraint_warning",
                   new=AsyncMock()) as warn:
            await tr.run_tool_transform(job, paths["src"], "mute")
        return cmds, warn

    async def test_strips_the_track_and_copies_the_video(self, terminal_spies, paths):
        cmds, _ = await self._run_mute(paths, "m1")

        terminal_spies["success"].assert_awaited()
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "-an" in cmd, "mute must REMOVE the audio track"
        # Video stream-copied — no re-encode, so no quality loss and no wait.
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        # Not the volume-filter path, and no audio encoder at all: re-encoding
        # to silence is exactly what this operation exists to avoid.
        assert "-af" not in cmd
        assert "-c:a" not in cmd
        assert "libx264" not in cmd

    async def test_no_audio_track_warns_but_still_succeeds(self, terminal_spies, paths):
        """The goal ("a video with no sound") is already true — so deliver the
        file, but say why nothing appears to have changed."""
        _, warn = await self._run_mute(paths, "m3", has_audio=False)

        terminal_spies["success"].assert_awaited()
        terminal_spies["fail"].assert_not_awaited()
        warn.assert_awaited()
        assert "no audio track" in warn.call_args.kwargs["message"]

    async def test_audio_present_does_not_warn(self, terminal_spies, paths):
        _, warn = await self._run_mute(paths, "m4", has_audio=True)
        warn.assert_not_awaited()

    async def test_other_operations_skip_the_audio_probe(self, terminal_spies, paths):
        """Only `mute` should pay for an ffprobe call."""
        (paths["dir"] / "m5.mp4").write_bytes(b"y" * 500)
        with patch("subprocess.run", new=_capture_cmd([])), \
             patch("backend.services.ffmpeg_service.has_audio_stream") as probe:
            await tr.run_tool_transform("m5", paths["src"], "flip_h")
        probe.assert_not_called()


class TestMuteIsWiredEverywhere:
    def test_api_accepts_the_operation(self) -> None:
        """A runner branch nothing can reach is dead code."""
        import inspect
        from backend.api.tools import transform_tool
        sig = inspect.signature(transform_tool)
        literal = sig.parameters["operation"].annotation
        assert "mute" in getattr(literal, "__args__", ())
