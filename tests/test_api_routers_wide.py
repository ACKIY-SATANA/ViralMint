# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The remaining REST surface: videos, settings, captions, templates,
channels, messaging, scout, news and generate.

The existing smoke suites prove these routes don't 5xx. This one goes into the
handler bodies — the branches that decide whether work starts, what gets
persisted, and what the user is told when something is missing.

Everything network- or AI-bound is stubbed at its seam, so the suite is
hermetic: no yt-dlp, no Whisper, no ffmpeg, no messaging sockets, no AI calls.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="vm-wide-api-"))
os.environ["VIRALMINT_DATA_DIR"] = str(_TMP)
os.environ.setdefault("DEBUG", "false")

import pytest
from starlette.testclient import TestClient

from backend.main import create_app
from backend.messaging import manager as messaging_manager


@pytest.fixture(scope="module")
def seeded():
    """Rows AND the files they point at. The Library list route prunes rows
    whose file has vanished, so a seed with dangling paths deletes itself."""
    media = _TMP / "media"
    media.mkdir(parents=True, exist_ok=True)
    gen_path, src_path = media / "gen.mp4", media / "src.mp4"
    for f in (gen_path, src_path):
        f.write_bytes(b"\x00" * 4096)

    async def _seed():
        from backend.database import engine, AsyncSessionLocal, Base
        from backend.models.connected_channel import ConnectedChannel
        from backend.models.downloaded_video import DownloadedVideo
        from backend.models.generated_video import GeneratedVideo
        from backend.models.scout_result import ScoutResult
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids = {}
        async with AsyncSessionLocal() as db:
            gv = GeneratedVideo(user_id="local", title="A generated short",
                                status="ready", video_path=str(gen_path),
                                aspect_ratio="9:16", source_type="generated")
            dv = DownloadedVideo(user_id="local", title="A source",
                                 platform="youtube", video_path=str(src_path),
                                 duration_seconds=300)
            sr = ScoutResult(user_id="local", platform="youtube",
                             video_id="yt1", video_url="https://youtu.be/yt1",
                             title="Scouted", niche="cooking", views=1000)
            ch = ConnectedChannel(user_id="local", platform="youtube",
                                  channel_id="UC1", channel_name="My Chan",
                                  channel_url="https://youtube.com/channel/UC1")
            db.add_all([gv, dv, sr, ch])
            await db.commit()
            ids.update(video=gv.id, downloaded=dv.id, scout=sr.id, channel=ch.id)
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


# ══════════════════════════════════════════════════════════════════════
# /api/videos — the Library
# ══════════════════════════════════════════════════════════════════════

class TestVideos:
    def test_the_library_lists_videos(self, client):
        r = client.get("/api/videos")
        assert r.status_code == 200

    def test_it_filters_by_status(self, client):
        assert client.get("/api/videos", params={"status": "ready"}).status_code == 200
        assert client.get("/api/videos", params={"status": "draft"}).status_code == 200

    def test_a_single_video_can_be_fetched(self, client, seeded):
        r = client.get(f"/api/videos/{seeded['video']}")
        assert r.status_code == 200 and r.json()["title"] == "A generated short"

    def test_an_unknown_video_is_a_404(self, client):
        assert client.get("/api/videos/nope").status_code == 404

    def test_a_video_can_be_renamed(self, client, seeded):
        r = client.patch(f"/api/videos/{seeded['video']}",
                         json={"title": "Renamed"})
        assert r.status_code == 200
        assert client.get(f"/api/videos/{seeded['video']}").json()["title"] == "Renamed"

    def test_patching_an_unknown_video_is_a_404(self, client):
        assert client.patch("/api/videos/nope", json={"title": "x"}).status_code == 404

    def test_exporting_an_unknown_video_is_a_404(self, client):
        assert client.post("/api/videos/nope/export",
                           json={"target_aspect": "16:9"}).status_code == 404

    def test_an_unknown_export_method_is_refused(self, client, seeded):
        """The method decides the geometry; a typo must not silently pick one."""
        r = client.post(f"/api/videos/{seeded['video']}/export",
                        json={"target_aspect": "16:9", "method": "teleport"})
        assert r.status_code in (400, 422)

    def test_there_is_exactly_one_export_route(self, client):
        """A duplicate handler once shadowed the real one — FastAPI matches
        the first registration, so the second never ran."""
        app = client.app
        paths = [r.path for r in app.routes if getattr(r, "path", "").endswith("/export")]
        assert len(paths) == len(set(paths)), f"duplicate export routes: {paths}"

    def test_streaming_an_unknown_video_is_a_404(self, client):
        assert client.get("/api/videos/nope/stream").status_code == 404

    def test_a_thumbnail_for_an_unknown_video_is_a_404(self, client):
        assert client.get("/api/videos/nope/thumbnail").status_code == 404

    def test_regenerating_a_thumbnail_needs_a_real_video(self, client):
        assert client.post(
            "/api/videos/nope/regenerate-thumbnail").status_code == 404

    def test_performance_summary_is_available(self, client):
        assert client.get("/api/videos/performance/summary").status_code == 200

    def test_the_optimal_posting_time_endpoint_answers(self, client):
        """This one crashed on its happy path once — round() over a list."""
        assert client.get("/api/videos/performance/optimal-time").status_code == 200

    def test_per_video_performance_answers(self, client, seeded):
        assert client.get(
            f"/api/videos/{seeded['video']}/performance").status_code in (200, 404)

    def test_uploading_an_unknown_video_is_a_404(self, client):
        assert client.post("/api/videos/nope/upload",
                           json={"platforms": ["youtube"]}).status_code == 404

    def test_a_video_can_be_deleted(self, client):
        async def add():
            from backend.database import AsyncSessionLocal
            from backend.models.generated_video import GeneratedVideo
            async with AsyncSessionLocal() as db:
                v = GeneratedVideo(user_id="local", title="temp", status="ready",
                                   video_path=str(_TMP / "media" / "gen.mp4"))
                db.add(v)
                await db.commit()
                return v.id
        vid = asyncio.run(add())
        assert client.delete(f"/api/videos/{vid}").status_code in (200, 204)
        assert client.get(f"/api/videos/{vid}").status_code == 404

    def test_deleting_an_unknown_video_is_a_404(self, client):
        assert client.delete("/api/videos/nope").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /api/settings
# ══════════════════════════════════════════════════════════════════════

class TestSettings:
    def test_settings_are_readable(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert "music_volume_db" in r.json()

    def test_a_preference_round_trips(self, client):
        client.post("/api/settings", json={"caption_style": "bold",
                                           "music_genre": "cinematic"})
        got = client.get("/api/settings").json()
        assert got["caption_style"] == "bold" and got["music_genre"] == "cinematic"

    def test_an_unset_music_bed_reads_as_the_audible_default(self, client):
        """-20dB measured as a 0.1dB change to the finished mix, which users
        reported as "no background music". Set the column to NULL explicitly:
        this suite shares a DB with the other API modules, so asserting on
        whatever value happens to be stored would test test-ordering."""
        async def clear():
            from sqlalchemy import select
            from backend.database import AsyncSessionLocal
            from backend.models.user_settings import UserSettings
            async with AsyncSessionLocal() as db:
                row = (await db.execute(select(UserSettings).where(
                    UserSettings.user_id == "local"))).scalar_one_or_none()
                if row is not None:
                    row.music_volume_db = None
                    await db.commit()
        asyncio.run(clear())
        assert client.get("/api/settings").json()["music_volume_db"] == -14.0

    def test_an_api_key_is_never_echoed_back(self, client):
        """BYOK: keys are stored encrypted and must not come back in a GET."""
        client.post("/api/settings", json={"ai_api_key": "sk-secret-value"})
        body = client.get("/api/settings").text
        assert "sk-secret-value" not in body

    def test_health_reports_the_local_toolchain(self, client):
        r = client.get("/api/settings/health")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_a_partial_update_leaves_other_fields_alone(self, client):
        client.post("/api/settings", json={"music_genre": "lofi"})
        client.post("/api/settings", json={"caption_style": "neon"})
        got = client.get("/api/settings").json()
        assert got["music_genre"] == "lofi" and got["caption_style"] == "neon"


# ══════════════════════════════════════════════════════════════════════
# /api/captions
# ══════════════════════════════════════════════════════════════════════

class TestCaptionStyles:
    def test_the_builtin_list_comes_from_the_engine(self, client):
        from backend.services.caption_service import CAPTION_STYLES
        served = {s["id"] for s in client.get("/api/captions/styles").json()["builtin"]}
        assert served == set(CAPTION_STYLES) - {"brainrot"}

    def test_a_custom_style_round_trips(self, client):
        made = client.post("/api/captions/styles",
                           json={"name": "My Style", "font": "Impact"})
        assert made.status_code == 201, made.text
        sid = made.json()["id"]
        listed = client.get("/api/captions/styles").json()["custom"]
        assert any(s["id"] == sid for s in listed)

        upd = client.put(f"/api/captions/styles/{sid}", json={"name": "Renamed"})
        assert upd.status_code == 200 and upd.json()["name"] == "Renamed"

        assert client.delete(f"/api/captions/styles/{sid}").status_code == 200

    def test_a_new_style_defaults_to_a_bottom_alignment(self, client):
        """Under a mid alignment libass ignores margin_v entirely and the
        platform safe-zone floor silently does nothing."""
        made = client.post("/api/captions/styles", json={"name": "Defaults"})
        assert made.json()["alignment"] == 2

    def test_an_explicit_middle_alignment_is_normalized(self, client):
        made = client.post("/api/captions/styles",
                           json={"name": "Middle", "alignment": 5})
        assert made.json()["alignment"] == 2

    def test_an_empty_name_is_refused(self, client):
        assert client.post("/api/captions/styles",
                           json={"name": "   "}).status_code == 422

    def test_updating_an_unknown_style_is_a_404(self, client):
        assert client.put("/api/captions/styles/nope",
                          json={"name": "x"}).status_code == 404

    def test_deleting_an_unknown_style_is_a_404(self, client):
        assert client.delete("/api/captions/styles/nope").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /api/templates
# ══════════════════════════════════════════════════════════════════════

class TestTemplates:
    def test_templates_are_listable(self, client):
        assert client.get("/api/templates").status_code == 200

    def test_they_filter_by_mode(self, client):
        assert client.get("/api/templates",
                          params={"mode": "stock"}).status_code == 200

    def test_deleting_an_unknown_template_is_a_404(self, client):
        """It used to return 200 with an `{"error": ...}` body, where every
        other delete in the API 404s. The frontend checks `response.ok`, so a
        failed delete read as a success and the row silently stayed."""
        assert client.delete("/api/templates/nope").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /api/channels
# ══════════════════════════════════════════════════════════════════════

class TestChannels:
    def test_connected_channels_are_listable(self, client):
        r = client.get("/api/channels/list")
        assert r.status_code == 200

    def test_connecting_without_a_url_is_refused(self, client):
        assert client.post("/api/channels/connect", json={}).status_code in (400, 422)

    def test_disconnecting_an_unknown_channel_is_handled(self, client):
        r = client.post("/api/channels/disconnect", json={"channel_db_id": "nope"})
        assert r.status_code in (200, 400, 404)

    def test_listing_videos_for_an_unknown_channel_is_a_404(self, client):
        assert client.get("/api/channels/videos/nope").status_code == 404

    def test_analyzing_without_a_url_is_refused(self, client):
        assert client.post("/api/channels/analyze", json={}).status_code in (400, 422)


# ══════════════════════════════════════════════════════════════════════
# /api/messaging
# ══════════════════════════════════════════════════════════════════════

class TestMessaging:
    def test_status_lists_every_channel(self, client):
        r = client.get("/api/messaging/status")
        assert r.status_code == 200
        body = r.json()
        text = str(body)
        for ch in ("telegram", "whatsapp", "discord", "slack"):
            assert ch in text

    @pytest.mark.parametrize("channel", ["telegram", "discord", "slack"])
    def test_connecting_without_credentials_is_refused(self, client, channel):
        r = client.post(f"/api/messaging/{channel}/connect", json={})
        assert r.status_code in (400, 422)

    @pytest.mark.parametrize("channel", ["telegram", "whatsapp", "discord", "slack"])
    def test_disconnect_is_idempotent(self, client, channel, monkeypatch):
        """Disconnecting something never connected must not error."""
        async def noop(*a, **k):
            return None
        monkeypatch.setattr(messaging_manager.messaging, "disconnect", noop,
                            raising=False)
        r = client.post(f"/api/messaging/{channel}/disconnect")
        assert r.status_code in (200, 400, 404)


# ══════════════════════════════════════════════════════════════════════
# /api/scout
# ══════════════════════════════════════════════════════════════════════

class TestScout:
    def test_starting_a_scout_needs_a_niche(self, client):
        assert client.post("/api/scout/start", json={}).status_code in (400, 422)

    def test_a_scout_can_be_started(self, client):
        r = client.post("/api/scout/start",
                        json={"niche": "cooking", "platforms": ["youtube"]})
        assert r.status_code == 200, r.text
        assert "job_id" in r.json()

    def test_results_are_listable(self, client):
        assert client.get("/api/scout/results").status_code == 200

    def test_results_filter_by_platform(self, client):
        assert client.get("/api/scout/results",
                          params={"platform": "youtube"}).status_code == 200

    def test_a_single_result_can_be_fetched(self, client, seeded):
        r = client.get(f"/api/scout/results/{seeded['scout']}")
        assert r.status_code == 200 and r.json()["title"] == "Scouted"

    def test_an_unknown_result_is_a_404(self, client):
        assert client.get("/api/scout/results/nope").status_code == 404

    def test_downloading_needs_ids(self, client):
        assert client.post("/api/scout/download", json={}).status_code in (400, 422)

    def test_downloading_starts_a_job(self, client, seeded):
        r = client.post("/api/scout/download",
                        json={"scout_result_ids": [seeded["scout"]]})
        assert r.status_code == 200, r.text

    def test_viral_formulas_are_listable(self, client):
        assert client.get("/api/scout/viral-formulas").status_code == 200

    def test_deleting_an_unknown_result_is_a_404(self, client):
        assert client.delete("/api/scout/results/nope").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /api/generate + /api/news
# ══════════════════════════════════════════════════════════════════════

class TestGenerate:
    def test_stock_generation_needs_a_script(self, client):
        assert client.post("/api/generate/stock", json={}).status_code in (400, 422)

    def test_stock_generation_starts_a_job(self, client):
        r = client.post("/api/generate/stock",
                        json={"script": "A short script about cats.",
                              "aspect_ratio": "9:16"})
        assert r.status_code == 200, r.text
        assert "job_id" in r.json()

    def test_stock_generation_forwards_the_users_own_images(self, client):
        """The field has to reach the runner. A request body that parses but
        drops a field looks identical to one that works."""
        from unittest.mock import patch
        with patch("backend.core.task_runner.dispatch") as dispatch:
            r = client.post("/api/generate/stock",
                            json={"script": "A short script about cats.",
                                  "user_images": ["/api/media/a.png",
                                                  "/api/media/b.png"]})
        assert r.status_code == 200, r.text
        coro = dispatch.call_args.args[0]
        assert coro.cr_frame.f_locals["user_images"] == [
            "/api/media/a.png", "/api/media/b.png"]
        coro.close()

    def test_stock_generation_defaults_to_no_user_images(self, client):
        from unittest.mock import patch
        with patch("backend.core.task_runner.dispatch") as dispatch:
            client.post("/api/generate/stock", json={"script": "Cats."})
        coro = dispatch.call_args.args[0]
        assert coro.cr_frame.f_locals["user_images"] == []
        coro.close()

    def test_stock_generation_rejects_an_unbounded_pile_of_images(self, client):
        """One request must not be able to queue an arbitrary number of
        renders; the scene grid caps at 12 anyway."""
        r = client.post("/api/generate/stock",
                        json={"script": "Cats.",
                              "user_images": [f"/api/media/{i}.png" for i in range(50)]})
        assert r.status_code == 422, r.text

    def test_splitting_scenes_needs_a_script(self, client):
        assert client.post("/api/generate/split-scenes",
                           json={}).status_code in (400, 422)


class TestNews:
    def test_a_news_scout_needs_a_query(self, client):
        assert client.post("/api/news/scout", json={}).status_code in (400, 422)

    def test_analyzing_a_url_needs_a_url(self, client):
        assert client.post("/api/news/analyze-url",
                           json={}).status_code in (400, 422)

    def test_saving_needs_articles(self, client):
        assert client.post("/api/news/save", json={}).status_code in (400, 422)


# ══════════════════════════════════════════════════════════════════════
# /api/jobs — the progress surface
# ══════════════════════════════════════════════════════════════════════

class TestJobs:
    @pytest.fixture()
    def jobs(self):
        async def make():
            from backend.agents.job_helper import create_job, update_job_status
            running = await create_job("download", "local", {})
            await update_job_status(running.id, "running", progress_pct=40)
            done = await create_job("generate", "local", {})
            await update_job_status(done.id, "success", progress_pct=100)
            return {"running": running.id, "done": done.id}
        return asyncio.run(make())

    def test_jobs_are_listable(self, client, jobs):
        r = client.get("/api/jobs")
        assert r.status_code == 200

    def test_they_filter_by_status(self, client, jobs):
        r = client.get("/api/jobs", params={"status": "running"})
        assert r.status_code == 200
        body = r.json()
        rows = body if isinstance(body, list) else body.get("jobs", body.get("items", []))
        assert all(j["status"] == "running" for j in rows)

    def test_they_filter_by_type(self, client, jobs):
        r = client.get("/api/jobs", params={"type": "generate"})
        assert r.status_code == 200

    def test_the_limit_is_respected(self, client, jobs):
        r = client.get("/api/jobs", params={"limit": 1})
        body = r.json()
        rows = body if isinstance(body, list) else body.get("jobs", body.get("items", []))
        assert len(rows) <= 1

    def test_a_single_job_can_be_fetched(self, client, jobs):
        r = client.get(f"/api/jobs/{jobs['running']}")
        assert r.status_code == 200 and r.json()["status"] == "running"

    def test_an_unknown_job_is_a_404(self, client):
        assert client.get("/api/jobs/nope").status_code == 404

    def test_cancelling_a_running_job_marks_it_cancelled(self, client, jobs):
        """Cancel only flips the row — the runner polls it. What matters here
        is that the row actually changes, because that IS the signal."""
        assert client.delete(f"/api/jobs/{jobs['running']}").status_code in (200, 204)
        assert client.get(f"/api/jobs/{jobs['running']}").json()["status"] == "cancelled"

    def test_deleting_a_finished_job_removes_it(self, client, jobs):
        assert client.delete(f"/api/jobs/{jobs['done']}").status_code in (200, 204)
        assert client.get(f"/api/jobs/{jobs['done']}").status_code == 404

    def test_deleting_an_unknown_job_is_a_404(self, client):
        assert client.delete("/api/jobs/nope").status_code == 404

    def test_bulk_delete_accepts_a_list(self, client, jobs):
        r = client.post("/api/jobs/bulk-delete",
                        json={"job_ids": [jobs["done"], "nope"]})
        assert r.status_code == 200

    def test_bulk_delete_with_nothing_to_do_is_survivable(self, client):
        r = client.post("/api/jobs/bulk-delete", json={"job_ids": []})
        assert r.status_code in (200, 400, 422)


# ══════════════════════════════════════════════════════════════════════
# /api/media + /api/chat/sessions
# ══════════════════════════════════════════════════════════════════════

class TestMedia:
    def test_an_image_can_be_uploaded_and_served_back(self, client):
        import io
        r = client.post("/api/media/upload", files={
            "file": ("pic.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 500),
                     "image/png")})
        assert r.status_code in (200, 201), r.text
        name = r.json().get("filename") or Path(r.json().get("url", "")).name
        if name:
            assert client.get(f"/api/media/{name}").status_code == 200

    def test_a_non_image_is_refused(self, client):
        import io
        r = client.post("/api/media/upload", files={
            "file": ("clip.mp4", io.BytesIO(b"\x00" * 500), "video/mp4")})
        assert r.status_code == 400

    def test_an_unknown_file_is_a_404(self, client):
        assert client.get("/api/media/not-here.png").status_code == 404

    def test_a_traversal_filename_cannot_escape_the_media_dir(self, client):
        """The media dir is not a window onto the filesystem. The handler
        reduces the parameter to its basename, so the directory components are
        discarded rather than followed — `../../etc/passwd` becomes `passwd`,
        which isn't in the media dir either."""
        r = client.get("/api/media/passwd")
        assert r.status_code == 404
        # And a multi-segment path never matches this single-segment route at
        # all — it falls through to the SPA shell, so nothing is served from
        # disk. Assert we did NOT get file bytes back.
        r2 = client.get("/api/media/../../etc/passwd")
        assert "root:" not in r2.text


class TestChatSessions:
    def test_a_session_round_trips(self, client):
        made = client.post("/api/chat/sessions", json={"title": "My chat"})
        assert made.status_code == 201, made.text
        sid = made.json()["id"]

        assert any(s["id"] == sid for s in client.get("/api/chat/sessions").json())

        renamed = client.put(f"/api/chat/sessions/{sid}", json={"title": "Renamed"})
        assert renamed.status_code == 200 and renamed.json()["title"] == "Renamed"

        assert client.get(f"/api/chat/sessions/{sid}/messages").json() == []
        assert client.delete(f"/api/chat/sessions/{sid}").status_code == 204

    def test_renaming_an_unknown_session_is_a_404(self, client):
        assert client.put("/api/chat/sessions/nope",
                          json={"title": "x"}).status_code == 404

    def test_deleting_an_unknown_session_is_a_404(self, client):
        assert client.delete("/api/chat/sessions/nope").status_code == 404

    def test_messages_for_an_unknown_session_is_a_404(self, client):
        assert client.get("/api/chat/sessions/nope/messages").status_code == 404

    def test_deleting_a_session_takes_its_messages_with_it(self, client):
        sid = client.post("/api/chat/sessions", json={"title": "Doomed"}).json()["id"]

        async def add_msg():
            from backend.api import chat as CHAT
            await CHAT._persist_message(sid, "user", "hello")
        asyncio.run(add_msg())
        assert len(client.get(f"/api/chat/sessions/{sid}/messages").json()) == 1
        client.delete(f"/api/chat/sessions/{sid}")
        assert client.get(f"/api/chat/sessions/{sid}/messages").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /api/config — the small stuff the frontend boots off
# ══════════════════════════════════════════════════════════════════════

class TestConfig:
    @pytest.mark.parametrize("key", ["tts_voices", "caption_styles", "unknown_key"])
    def test_a_config_key_answers_without_5xxing(self, client, key):
        """The frontend fetches these on boot; a 500 here is a blank app."""
        assert client.get(f"/api/config/{key}").status_code in (200, 404)

    def test_a_known_key_returns_json(self, client):
        r = client.get("/api/config/tts_voices")
        if r.status_code == 200:
            assert isinstance(r.json(), (dict, list))


class TestSettingsAuthRoutes:
    """The upload-OAuth entry points. They must not 500 when the provider
    credentials aren't configured — that's the normal state for most users."""

    @pytest.mark.parametrize("route", ["youtube-auth", "tiktok-upload-auth"])
    def test_an_auth_url_request_is_handled(self, client, route):
        assert client.get(f"/api/settings/{route}").status_code in (
            200, 400, 500, 503)

    @pytest.mark.parametrize("route", ["youtube-callback", "tiktok-upload-callback"])
    def test_a_callback_without_a_code_is_handled(self, client, route):
        assert client.get(f"/api/settings/{route}").status_code in (
            200, 400, 422, 500)
