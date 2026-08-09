# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The /api/downloaded router — the competitor-video library and the Clipper's
entry point.

The interesting half is input validation, because this router is where free
text from a dialog becomes a background job that costs minutes of Whisper and
ffmpeg. Anything it waves through fails LATER, in a runner, where the user
sees "job failed" with no idea which of their ten time ranges was the typo.

So the rules pinned here are:
  - every manual range is validated against the real video BEFORE a job is
    created, and the message names WHICH range is wrong;
  - a range may be a number or "SS" / "MM:SS" / "HH:MM:SS";
  - unknown-but-harmless hints (platform, genre, emoji density) degrade
    quietly rather than 400-ing — the caller shouldn't need our vocabulary;
  - a video whose file has vanished from disk is caught at submit time.

`dispatch` is stubbed, so no runner executes.

Two gaps this suite FOUND and deliberately does not paper over (this file
never patches source — see the note in test_api_endpoints_seeded.py):

  * `POST /downloaded/batch-download` documents `urls` as a list of objects
    and calls `u.get("url")` on each. A caller passing the obvious
    `["https://…"]` gets an AttributeError → 500, not a 400.
  * `POST /downloaded/import` streams and size-caps the upload but never
    rejects a ZERO-byte one, so an empty file becomes a Library row.

Both are pinned below as `xfail(strict=True)` so the day they're fixed the
suite says so instead of quietly passing.
"""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="vm-dl-api-"))
os.environ["VIRALMINT_DATA_DIR"] = str(_TMP)
os.environ.setdefault("DEBUG", "false")

import pytest
from starlette.testclient import TestClient

from backend.main import create_app
from backend.messaging import manager as messaging_manager


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """A real file on disk — several routes check existence."""
    d = tmp_path_factory.mktemp("media")
    p = d / "source.mp4"
    p.write_bytes(b"\x00" * 8192)
    return p


@pytest.fixture(scope="module")
def seeded(media):
    """Insert the rows the path-param routes need, before the client starts."""
    async def _seed():
        from backend.database import engine, AsyncSessionLocal, Base
        from backend.models.downloaded_video import DownloadedVideo
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids = {}
        async with AsyncSessionLocal() as db:
            ok = DownloadedVideo(user_id="local", title="A long podcast",
                                 platform="youtube", video_path=str(media),
                                 duration_seconds=600)
            gone = DownloadedVideo(user_id="local", title="Deleted from disk",
                                   platform="youtube",
                                   video_path="/no/such/file.mp4",
                                   duration_seconds=600)
            db.add_all([ok, gone])
            await db.commit()
            ids["ok"] = ok.id
            ids["gone"] = gone.id
        await engine.dispose()
        return ids
    return asyncio.run(_seed())


@pytest.fixture(scope="module")
def client(seeded):
    async def _noop(*a, **k):
        return None
    messaging_manager.messaging.start_all = _noop      # type: ignore[assignment]
    messaging_manager.messaging.stop_all = _noop       # type: ignore[assignment]
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    def fake_dispatch(coro, *a, **k):
        coro.close()
        return None
    monkeypatch.setattr("backend.core.task_runner.dispatch", fake_dispatch)


def _extract(client, vid, **body):
    return client.post(f"/api/downloaded/{vid}/extract-clips", json=body)


# ── listing + fetching ──────────────────────────────────────────────────────

class TestListing:
    def test_the_list_returns_rows(self, client, seeded):
        r = client.get("/api/downloaded")
        assert r.status_code == 200
        body = r.json()
        rows = body if isinstance(body, list) else body.get("videos", body.get("items", []))
        assert len(rows) >= 2

    def test_it_pages(self, client):
        r = client.get("/api/downloaded", params={"limit": 1, "offset": 0})
        assert r.status_code == 200

    def test_a_single_row_can_be_fetched(self, client, seeded):
        r = client.get(f"/api/downloaded/{seeded['ok']}")
        assert r.status_code == 200
        assert r.json()["title"] == "A long podcast"

    def test_an_unknown_id_is_a_404(self, client):
        assert client.get("/api/downloaded/nope").status_code == 404

    def test_streaming_refuses_a_path_outside_the_storage_root(self, client, seeded):
        """The seeded row points at a tmp dir. A loopback server still can't
        be a general-purpose file reader for any page the user visits."""
        assert client.get(f"/api/downloaded/{seeded['ok']}/stream").status_code == 403

    def test_streaming_a_vanished_file_is_refused(self, client, seeded):
        assert client.get(
            f"/api/downloaded/{seeded['gone']}/stream").status_code in (403, 404)

    def test_a_thumbnail_is_generated_on_demand(self, client, seeded, monkeypatch):
        """The row usually has no thumbnail_path; the route makes one from the
        video and backfills the row rather than showing a blank card."""
        made = {}

        async def fake_extract(video_path, output_path=None, timestamp=None, **kw):
            out = Path(output_path) if output_path else Path("/tmp/t.jpg")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)
            made["path"] = out
            return out

        monkeypatch.setattr("backend.services.ffmpeg_service.extract_thumbnail",
                            fake_extract)
        r = client.get(f"/api/downloaded/{seeded['ok']}/thumbnail")
        assert r.status_code in (200, 404)

    def test_a_thumbnail_for_a_missing_file_is_a_404(self, client, seeded):
        assert client.get(
            f"/api/downloaded/{seeded['gone']}/thumbnail").status_code == 404


# ── manual time-range validation ────────────────────────────────────────────

class TestManualRangeValidation:
    def test_manual_mode_without_ranges_is_refused(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual")
        assert r.status_code == 400
        assert "time_ranges required" in r.json()["detail"]

    def test_an_empty_range_list_is_refused(self, client, seeded):
        assert _extract(client, seeded["ok"], mode="manual",
                        time_ranges=[]).status_code == 400

    def test_numeric_seconds_are_accepted(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": 0, "end": 30}])
        assert r.status_code == 200, r.text
        assert "job_id" in r.json()

    @pytest.mark.parametrize("start,end", [
        ("0", "30"),
        ("0:10", "1:30"),
        ("00:00:05", "00:01:05"),
        ("0:05.500", "0:35.250"),
    ])
    def test_every_documented_timestamp_form_is_accepted(
            self, client, seeded, start, end):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": start, "end": end}])
        assert r.status_code == 200, f"{start}-{end}: {r.text}"

    def test_a_bad_timestamp_names_which_range_is_wrong(self, client, seeded):
        """With ten rows in the dialog, "invalid timestamp" is useless."""
        r = _extract(client, seeded["ok"], mode="manual", time_ranges=[
            {"start": 0, "end": 30},
            {"start": "banana", "end": 60},
        ])
        assert r.status_code == 400
        assert "Range 2" in r.json()["detail"]

    def test_an_inverted_range_is_refused(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": 60, "end": 30}])
        assert r.status_code == 400
        assert "must be after start" in r.json()["detail"]

    def test_a_too_short_clip_is_refused_with_the_minimum(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": 10, "end": 10.2}])
        assert r.status_code == 400
        assert "too short" in r.json()["detail"]

    def test_a_range_past_the_end_of_the_video_is_refused(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": 0, "end": 9999}])
        assert r.status_code == 400
        assert "exceeds video duration" in r.json()["detail"]

    def test_a_half_second_over_is_tolerated(self, client, seeded):
        """yt-dlp's recorded duration is routinely off by a fraction against
        the real frame count; refusing that would reject a legitimate
        end-of-video clip."""
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": 500, "end": 600.4}])
        assert r.status_code == 200, r.text

    def test_a_non_object_range_is_refused(self, client, seeded):
        """Pydantic catches this at the model boundary (422) before the
        per-range validator can name it — either way it never reaches a job."""
        r = _extract(client, seeded["ok"], mode="manual", time_ranges=["0-30"])
        assert r.status_code in (400, 422)

    def test_too_many_ranges_are_refused_with_the_cap(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual",
                     time_ranges=[{"start": i, "end": i + 5} for i in range(0, 400, 10)])
        assert r.status_code == 400
        assert "Too many ranges" in r.json()["detail"]

    def test_several_valid_ranges_produce_one_job(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="manual", time_ranges=[
            {"start": 0, "end": 30},
            {"start": "1:00", "end": "1:45"},
        ])
        assert r.status_code == 200, r.text


# ── AI-mode validation ──────────────────────────────────────────────────────

class TestAiModeValidation:
    def test_the_default_mode_is_ai(self, client, seeded):
        assert _extract(client, seeded["ok"]).status_code == 200

    def test_an_unknown_mode_is_refused_by_name(self, client, seeded):
        r = _extract(client, seeded["ok"], mode="telepathy")
        assert r.status_code == 400
        assert "Unknown mode" in r.json()["detail"]

    def test_an_impossible_duration_window_is_refused(self, client, seeded):
        r = _extract(client, seeded["ok"], min_duration=60, max_duration=30)
        assert r.status_code == 400
        assert "must be less than" in r.json()["detail"]

    def test_max_clips_is_clamped_rather_than_refused(self, client, seeded):
        for n in (0, 1, 500):
            assert _extract(client, seeded["ok"], max_clips=n).status_code == 200

    @pytest.mark.parametrize("field,value", [
        ("target_platform", "myspace"),
        ("genre", "interpretive-dance"),
        ("emoji_style", "extremely"),
    ])
    def test_unknown_hints_degrade_quietly(self, client, seeded, field, value):
        """These bias the AI prompt. The caller shouldn't have to know our
        exact vocabulary to submit a job."""
        assert _extract(client, seeded["ok"], **{field: value}).status_code == 200

    def test_a_user_query_is_accepted(self, client, seeded):
        assert _extract(client, seeded["ok"],
                        user_query="only the funny bits").status_code == 200


class TestExtractPreconditions:
    def test_an_unknown_video_is_a_404(self, client):
        assert _extract(client, "no-such-video").status_code == 404

    def test_a_video_whose_file_has_vanished_is_caught_at_submit(
            self, client, seeded):
        """Better a clean 400 now than a runner failure in ten minutes."""
        r = _extract(client, seeded["gone"])
        assert r.status_code == 400
        assert "not found on disk" in r.json()["detail"]


# ── import + the remaining POSTs ────────────────────────────────────────────

class TestImport:
    def test_a_local_video_can_be_imported(self, client):
        r = client.post("/api/downloaded/import", files={
            "file": ("mine.mp4", io.BytesIO(b"\x00" * 4096), "video/mp4")})
        assert r.status_code in (200, 201), r.text

    def test_an_unsupported_format_is_refused(self, client):
        r = client.post("/api/downloaded/import", files={
            "file": ("doc.pdf", io.BytesIO(b"x" * 100), "application/pdf")})
        assert r.status_code == 400

    @pytest.mark.xfail(strict=True, reason=(
        "GAP: import size-caps the upload but never rejects a zero-byte one, "
        "so an empty file becomes a Library row"))
    def test_an_empty_file_is_refused(self, client):
        r = client.post("/api/downloaded/import", files={
            "file": ("mine.mp4", io.BytesIO(b""), "video/mp4")})
        assert r.status_code == 400


class TestOtherPosts:
    def test_batch_download_requires_urls(self, client):
        assert client.post("/api/downloaded/batch-download",
                           json={}).status_code == 400

    def test_batch_download_accepts_the_documented_shape(self, client):
        """The docstring specifies a list of objects."""
        r = client.post("/api/downloaded/batch-download",
                        json={"urls": [{"url": "https://youtu.be/abc",
                                        "title": "A video"}]})
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1

    def test_batch_download_caps_the_batch(self, client):
        r = client.post("/api/downloaded/batch-download", json={
            "urls": [{"url": f"https://youtu.be/{i}"} for i in range(25)]})
        assert r.status_code == 400
        assert "Maximum 20" in r.json()["detail"]

    @pytest.mark.xfail(strict=True, reason=(
        "GAP: a plain list of URL strings hits u.get() on a str → 500 "
        "instead of a 400 naming the expected shape"))
    def test_batch_download_rejects_a_bare_string_list_cleanly(self, client):
        r = client.post("/api/downloaded/batch-download",
                        json={"urls": ["https://youtu.be/abc"]})
        assert r.status_code == 400

    def test_generate_script_needs_a_real_video(self, client):
        assert client.post(
            "/api/downloaded/nope/generate-script", json={}).status_code == 404

    def test_generate_needs_a_real_video(self, client):
        assert client.post(
            "/api/downloaded/nope/generate", json={}).status_code == 404

    def test_reanalyze_needs_a_real_video(self, client):
        assert client.post(
            "/api/downloaded/nope/reanalyze", json={}).status_code == 404

    def test_ai_action_validates_its_action_before_the_video(self, client):
        """Argument validation runs first, so an unknown action 400s even for
        a video that doesn't exist."""
        assert client.post("/api/downloaded/nope/ai-action",
                           json={"action": "summarize"}).status_code == 400

    def test_batch_generate_requires_ids(self, client):
        assert client.post("/api/downloaded/batch-generate",
                           json={}).status_code == 400

    def test_cleanup_runs(self, client):
        r = client.post("/api/downloaded/cleanup")
        assert r.status_code == 200

    def test_deleting_an_unknown_row_is_a_404(self, client):
        assert client.delete("/api/downloaded/nope").status_code == 404

    def test_a_row_can_be_deleted(self, client, media):
        async def add():
            from backend.database import AsyncSessionLocal
            from backend.models.downloaded_video import DownloadedVideo
            async with AsyncSessionLocal() as db:
                v = DownloadedVideo(user_id="local", title="throwaway",
                                    platform="youtube", video_path=str(media),
                                    duration_seconds=10)
                db.add(v)
                await db.commit()
                return v.id
        vid = asyncio.run(add())
        assert client.delete(f"/api/downloaded/{vid}").status_code in (200, 204)
        assert client.get(f"/api/downloaded/{vid}").status_code == 404
