# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The /api/tools router — upload validation, job creation, and downloads.

This is the layer between a browser file-picker and a background runner, and
almost everything it does is a guard. The guards matter more than they look:

  - the extension allowlist is per-tool (the Audio tools take .mp3, the Video
    tools don't), and getting it wrong rejects a legitimate file AFTER the user
    has already uploaded it;
  - the caption-style Literal is DERIVED from the render engine — it was
    hardcoded to three styles long after the engine grew to eleven, so posting
    any of the other seven came back 422 from the tool built to apply them;
  - `safe_tools_path` is a path-traversal boundary on a loopback server that a
    malicious page can still POST to;
  - the job type each route creates is the key the frontend re-attaches on, so
    a mismatch silently breaks "navigate away and come back mid-job".

Runners are never dispatched: `dispatch` is stubbed, so these tests exercise
the request path only. Nothing here shells out to ffmpeg.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

# Throwaway data dir BEFORE backend.config is imported.
_TMP = Path(tempfile.mkdtemp(prefix="vm-tools-api-"))
os.environ["VIRALMINT_DATA_DIR"] = str(_TMP)
os.environ.setdefault("DEBUG", "false")

import pytest
from starlette.testclient import TestClient

from backend.main import create_app
from backend.messaging import manager as messaging_manager


@pytest.fixture(scope="module")
def client():
    async def _noop(*a, **k):
        return None
    messaging_manager.messaging.start_all = _noop      # type: ignore[assignment]
    messaging_manager.messaging.stop_all = _noop       # type: ignore[assignment]
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    """Capture dispatched coroutines instead of running them."""
    spawned: list = []

    def fake_dispatch(coro, *a, **k):
        coro.close()          # never actually run the runner
        spawned.append(coro)
        return None

    monkeypatch.setattr("backend.core.task_runner.dispatch", fake_dispatch)
    return spawned


def _mp4(name="clip.mp4", size=4096):
    return {"file": (name, io.BytesIO(b"\x00" * size), "video/mp4")}


def _mp3(name="pod.mp3", size=4096):
    return {"file": (name, io.BytesIO(b"\x00" * size), "audio/mpeg")}


async def _job_type(job_id: str) -> str:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(Job.job_type).where(Job.id == job_id))).scalar_one()


def _job_type_sync(job_id: str) -> str:
    import asyncio
    return asyncio.run(_job_type(job_id))


# ── upload validation, shared by every tool ─────────────────────────────────

class TestUploadGuards:
    def test_no_file_is_a_400(self, client):
        r = client.post("/api/tools/captions", files={}, data={"style": "viral"})
        assert r.status_code in (400, 422)

    def test_an_unsupported_extension_is_rejected_with_the_allowed_list(self, client):
        r = client.post("/api/tools/captions",
                        files={"file": ("doc.pdf", io.BytesIO(b"x" * 100), "application/pdf")})
        assert r.status_code == 400
        assert ".mp4" in r.json()["detail"], "the message must say what IS allowed"

    def test_an_empty_upload_is_rejected(self, client):
        r = client.post("/api/tools/captions",
                        files={"file": ("clip.mp4", io.BytesIO(b""), "video/mp4")})
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_the_extension_check_is_case_insensitive(self, client):
        r = client.post("/api/tools/captions", files=_mp4("CLIP.MP4"))
        assert r.status_code == 200

    def test_an_oversized_upload_is_a_413(self, client, monkeypatch):
        import backend.api.tools as T
        monkeypatch.setattr(T, "VIDEO_MAX_BYTES", 100)
        r = client.post("/api/tools/captions", files=_mp4(size=5000))
        assert r.status_code == 413
        assert "too large" in r.json()["detail"].lower()

    def test_audio_tools_accept_audio(self, client):
        """These used to validate against the VIDEO list, so a podcast .mp3 was
        rejected by the tools whose whole job is the audio track."""
        for route in ("audio-enhance", "remove-silence"):
            r = client.post(f"/api/tools/{route}", files=_mp3())
            assert r.status_code == 200, f"{route} rejected an audio file: {r.text}"

    def test_video_only_tools_still_reject_audio(self, client):
        r = client.post("/api/tools/reframe", files=_mp3())
        assert r.status_code == 400


# ── every tool route creates a job ──────────────────────────────────────────

TOOL_ROUTES = [
    ("captions", {"style": "viral"}, _mp4),
    ("reframe", {}, _mp4),
    ("audio-enhance", {}, _mp4),
    ("remove-silence", {}, _mp4),
    ("transform", {"operation": "rotate_cw", "amount": ""}, _mp4),
    ("gif", {"start_seconds": "0", "duration_seconds": "2"}, _mp4),
    ("speed", {"speed": "1.5"}, _mp4),
    ("trim", {"start_seconds": "0", "end_seconds": "2"}, _mp4),
    ("auto-zoom", {}, _mp4),
    ("music-visualizer", {}, _mp3),
    ("hook-analysis", {}, _mp4),
    ("auto-chapters", {"target_count": "0"}, _mp4),
    ("subtitles", {"format": "srt"}, _mp4),
    ("translate", {"target_language": "Spanish", "mode": "captions_only"}, _mp4),
]


class TestToolRoutes:
    @pytest.mark.parametrize("route,data,fixture", TOOL_ROUTES)
    def test_it_accepts_an_upload_and_returns_a_job_id(
            self, client, route, data, fixture):
        r = client.post(f"/api/tools/{route}", files=fixture(), data=data)
        assert r.status_code == 200, r.text
        assert "job_id" in r.json()

    def test_captions_accepts_every_style_the_engine_offers(self, client):
        """The Literal is derived from CAPTION_STYLES; this is the drift guard
        at the HTTP boundary."""
        from backend.services.caption_service import CAPTION_STYLES
        for style in CAPTION_STYLES:
            if style == "brainrot":
                continue
            r = client.post("/api/tools/captions", files=_mp4(),
                            data={"style": style})
            assert r.status_code == 200, f"style {style!r} was rejected: {r.text}"

    def test_an_invented_caption_style_is_refused(self, client):
        r = client.post("/api/tools/captions", files=_mp4(),
                        data={"style": "definitely-not-a-style"})
        assert r.status_code == 422

    def test_brainrot_is_not_offered_as_a_general_style(self, client):
        """It's the Brainrot format's own look, applied by its own pipeline."""
        r = client.post("/api/tools/captions", files=_mp4(),
                        data={"style": "brainrot"})
        assert r.status_code == 422

    def test_watermark_takes_two_files(self, client):
        r = client.post("/api/tools/watermark", files={
            "file": ("clip.mp4", io.BytesIO(b"\x00" * 4096), "video/mp4"),
            "logo": ("logo.png", io.BytesIO(b"\x89PNG" + b"\x00" * 500), "image/png"),
        }, data={"position": "bottom-right"})
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("start,end,why", [
        ("-1", "5", "a negative start"),
        ("5", "5", "a zero-length range"),
        ("9", "3", "an inverted range"),
    ])
    def test_trim_refuses_an_impossible_range(self, client, start, end, why):
        r = client.post("/api/tools/trim", files=_mp4(),
                        data={"start_seconds": start, "end_seconds": end})
        assert r.status_code == 400, f"{why} should be refused"

    def test_transform_refuses_an_unknown_operation(self, client):
        r = client.post("/api/tools/transform", files=_mp4(),
                        data={"operation": "teleport"})
        assert r.status_code == 422

    def test_watermark_rejects_a_non_image_logo(self, client):
        r = client.post("/api/tools/watermark", files={
            "file": ("clip.mp4", io.BytesIO(b"\x00" * 4096), "video/mp4"),
            "logo": ("logo.mp4", io.BytesIO(b"\x00" * 500), "video/mp4"),
        })
        assert r.status_code == 400


# ── downloads + the path boundary ───────────────────────────────────────────

class TestDownload:
    def test_an_unknown_job_is_a_404(self, client):
        assert client.get("/api/tools/download/nope").status_code == 404
        assert client.get("/api/tools/download-meta/nope").status_code == 404

    def test_an_unfinished_job_is_not_downloadable(self, client):
        job_id = client.post("/api/tools/captions", files=_mp4(),
                             data={"style": "viral"}).json()["job_id"]
        r = client.get(f"/api/tools/download/{job_id}")
        assert r.status_code == 400
        assert client.get(f"/api/tools/download-meta/{job_id}").json()["ready"] is False

    def _finish(self, job_id, path):
        import asyncio
        from backend.agents.job_helper import update_job_status
        asyncio.run(update_job_status(job_id, "success",
                                      output_data={"file": str(path)}))

    def test_a_finished_job_serves_its_artifact(self, client):
        job_id = client.post("/api/tools/captions", files=_mp4(),
                             data={"style": "viral"}).json()["job_id"]
        from backend.api.tools import tool_out_path
        out = tool_out_path(job_id, ".mp4")
        out.write_bytes(b"\x00" * 900)
        self._finish(job_id, out)

        r = client.get(f"/api/tools/download/{job_id}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/mp4")
        assert "viralmint_tool_captions" in r.headers["content-disposition"]

        meta = client.get(f"/api/tools/download-meta/{job_id}").json()
        assert meta["ready"] is True and meta["size"] == 900 and meta["ext"] == ".mp4"

    def test_the_content_type_follows_the_produced_extension(self, client):
        """The frontend picks <video>/<audio>/<img> off this."""
        for ext, expect in ((".mp3", "audio/mpeg"), (".srt", "text/plain"),
                            (".png", "image/png"), (".vtt", "text/vtt")):
            job_id = client.post("/api/tools/audio-enhance",
                                 files=_mp3()).json()["job_id"]
            from backend.api.tools import tool_out_path
            out = tool_out_path(job_id, ext)
            out.write_bytes(b"x" * 50)
            self._finish(job_id, out)
            meta = client.get(f"/api/tools/download-meta/{job_id}").json()
            assert meta["content_type"] == expect, ext

    def test_a_vanished_artifact_reads_as_expired_not_ready(self, client):
        job_id = client.post("/api/tools/captions", files=_mp4(),
                             data={"style": "viral"}).json()["job_id"]
        from backend.api.tools import tool_out_path
        out = tool_out_path(job_id, ".mp4")
        self._finish(job_id, out)          # never written
        assert client.get(f"/api/tools/download-meta/{job_id}").json()["reason"] == "expired"
        assert client.get(f"/api/tools/download/{job_id}").status_code == 404

    def test_a_success_row_with_no_file_is_handled(self, client):
        job_id = client.post("/api/tools/captions", files=_mp4(),
                             data={"style": "viral"}).json()["job_id"]
        import asyncio
        from backend.agents.job_helper import update_job_status
        asyncio.run(update_job_status(job_id, "success", output_data={}))
        assert client.get(f"/api/tools/download-meta/{job_id}").json()["reason"] == "no_file"
        assert client.get(f"/api/tools/download/{job_id}").status_code == 404


class TestPathBoundary:
    """`safe_tools_path` is the traversal guard. The server is loopback-only,
    but any page the user visits can still POST to 127.0.0.1."""

    def test_a_path_inside_the_tools_dir_is_allowed(self):
        from backend.api.tools import safe_tools_path, tool_out_path
        p = tool_out_path("abc", ".mp4")
        assert safe_tools_path(str(p)) == p.resolve()

    @pytest.mark.parametrize("bad", [
        "/etc/passwd",
        "../../../../etc/passwd",
        "/tmp/somewhere-else.mp4",
    ])
    def test_a_path_outside_is_refused(self, bad):
        from fastapi import HTTPException
        from backend.api.tools import safe_tools_path
        with pytest.raises(HTTPException) as ei:
            safe_tools_path(bad)
        assert ei.value.status_code in (403, 404)

    def test_an_empty_path_is_refused(self):
        from fastapi import HTTPException
        from backend.api.tools import safe_tools_path
        with pytest.raises(HTTPException):
            safe_tools_path("")

    def test_a_traversal_path_in_a_job_row_is_not_served(self, client):
        """Defence in depth: even if a row somehow carried one."""
        job_id = client.post("/api/tools/captions", files=_mp4(),
                             data={"style": "viral"}).json()["job_id"]
        import asyncio
        from backend.agents.job_helper import update_job_status
        asyncio.run(update_job_status(job_id, "success",
                                      output_data={"file": "/etc/passwd"}))
        assert client.get(f"/api/tools/download/{job_id}").status_code == 403
        assert client.get(f"/api/tools/download-meta/{job_id}").json()["reason"] == "bad_path"


# ── the routes with their own shapes ────────────────────────────────────────

class TestMultiFileAndTextRoutes:
    def test_merge_clips_takes_several_videos(self, client):
        r = client.post("/api/tools/merge-clips", files=[
            ("files", ("a.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")),
            ("files", ("b.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")),
        ], data={"target_aspect": "9:16", "transition": "crossfade"})
        assert r.status_code == 200, r.text

    def test_merge_clips_refuses_a_single_clip(self, client):
        """Merging one video is a no-op the user didn't mean to ask for."""
        r = client.post("/api/tools/merge-clips", files=[
            ("files", ("a.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")),
        ])
        assert r.status_code == 400

    def test_merge_clips_rejects_an_unsupported_member(self, client):
        r = client.post("/api/tools/merge-clips", files=[
            ("files", ("a.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")),
            ("files", ("b.pdf", io.BytesIO(b"\x00" * 2048), "application/pdf")),
        ])
        assert r.status_code == 400

    def test_voiceover_works_from_text_alone(self, client):
        """No video attached — the tool produces a bare audio track."""
        r = client.post("/api/tools/voiceover",
                        data={"text": "hello world", "voice_id": ""})
        assert r.status_code == 200, r.text

    def test_voiceover_refuses_empty_text(self, client):
        r = client.post("/api/tools/voiceover", data={"text": "   "})
        assert r.status_code == 400

    def test_voiceover_can_take_a_video_to_dub(self, client):
        r = client.post("/api/tools/voiceover", data={"text": "hi"},
                        files={"video": ("v.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")})
        assert r.status_code == 200, r.text

    def test_metadata_works_from_a_topic_with_no_file(self, client):
        r = client.post("/api/tools/metadata", data={"topic": "how to bake bread"})
        assert r.status_code == 200, r.text

    def test_metadata_needs_something_to_work_from(self, client):
        r = client.post("/api/tools/metadata", data={})
        assert r.status_code == 400

    def test_translate_requires_a_target_language(self, client):
        r = client.post("/api/tools/translate", files=_mp4(), data={})
        assert r.status_code == 422

    def test_translate_refuses_an_unknown_mode(self, client):
        r = client.post("/api/tools/translate", files=_mp4(),
                        data={"target_language": "es", "mode": "telepathy"})
        assert r.status_code == 422

    def test_subtitles_offers_all_three_formats(self, client):
        for fmt in ("srt", "vtt", "txt"):
            r = client.post("/api/tools/subtitles", files=_mp4(),
                            data={"format": fmt})
            assert r.status_code == 200, f"{fmt}: {r.text}"

    def test_subtitles_refuses_an_unknown_format(self, client):
        r = client.post("/api/tools/subtitles", files=_mp4(), data={"format": "ass"})
        assert r.status_code == 422
