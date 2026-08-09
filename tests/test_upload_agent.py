# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The upload orchestrator — the agent between "user pressed Upload" and the
three provider clients.

Its job is entirely about partial failure. Uploading to three platforms is
three independent network operations and any subset can fail, so the rules it
has to keep are:

  - one platform failing NEVER stops the others;
  - an auth failure emits a constraint warning naming the setup wizard, so the
    user gets a re-connect prompt instead of a dead error;
  - the job is "success" if ANYTHING landed (with the errors named), and
    "failed" only when nothing did — a video that reached YouTube but not
    TikTok has not failed;
  - each platform's id is persisted so the Library can link to the live post,
    and a re-upload never duplicates the platform in the list.

Provider clients are stubbed — the real ones have their own suite
(test_uploaders.py). Nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from backend.agents.uploader import UploadAgent
from backend.core.crypto import encrypt
from backend.core.exceptions import UploadAuthError
from backend.database import AsyncSessionLocal, init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


async def _make_video(**kw):
    from backend.models.generated_video import GeneratedVideo
    async with AsyncSessionLocal() as db:
        v = GeneratedVideo(
            user_id="local", title="A video", status="ready",
            video_path=kw.pop("video_path", "/tmp/v.mp4"), **kw)
        db.add(v)
        await db.commit()
        return v.id


async def _make_settings(**kw):
    from backend.models.user_settings import UserSettings
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local"))).scalar_one_or_none()
        if row is None:
            row = UserSettings(user_id="local")
            db.add(row)
        for k, v in kw.items():
            setattr(row, k, v)
        await db.commit()
    return row


async def _job() -> str:
    from backend.agents.job_helper import create_job
    return (await create_job("upload", "local", {})).id


async def _job_row(job_id):
    from backend.models.job import Job
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _video_row(vid):
    from backend.models.generated_video import GeneratedVideo
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(GeneratedVideo).where(GeneratedVideo.id == vid))).scalar_one()


@pytest.fixture()
def ws(monkeypatch):
    """Capture what the user is actually told."""
    sent: list[dict] = []
    warnings: list[dict] = []

    async def fake_send(msg, user_id="local"):
        sent.append(msg)

    async def fake_progress(*a, **k):
        return None

    async def fake_warn(constraint, message, severity="warning",
                        wizard_id=None, user_id="local"):
        warnings.append({"constraint": constraint, "message": message,
                         "severity": severity, "wizard_id": wizard_id})

    from backend.core.ws_manager import ws_manager
    monkeypatch.setattr(ws_manager, "send", fake_send)
    monkeypatch.setattr(ws_manager, "send_progress", fake_progress)
    monkeypatch.setattr(ws_manager, "send_constraint_warning", fake_warn)
    return {"sent": sent, "warnings": warnings}


@pytest.fixture()
def providers(monkeypatch):
    """Stub the three provider clients; each test sets outcomes."""
    calls: dict = {}
    outcomes: dict = {
        "youtube": {"video_id": "yt1", "url": "https://youtu.be/yt1"},
        "tiktok": {"publish_id": "tt1"},
        "instagram": {"media_id": "ig1", "permalink": "https://instagram.com/p/ig1"},
    }

    async def yt(**kw):
        calls["youtube"] = kw
        out = outcomes["youtube"]
        if isinstance(out, Exception):
            raise out
        return out

    async def tt(**kw):
        calls["tiktok"] = kw
        out = outcomes["tiktok"]
        if isinstance(out, Exception):
            raise out
        return out

    async def ig(**kw):
        calls["instagram"] = kw
        out = outcomes["instagram"]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr("backend.services.youtube_uploader.upload_to_youtube", yt)
    monkeypatch.setattr("backend.services.tiktok_uploader.upload_to_tiktok", tt)
    monkeypatch.setattr("backend.services.instagram_uploader.upload_to_instagram", ig)
    return {"calls": calls, "outcomes": outcomes}


# ── preconditions ───────────────────────────────────────────────────────────

class TestPreconditions:
    def test_a_missing_video_fails_the_job_with_a_reason(self, ws, providers):
        async def run():
            jid = await _job()
            await UploadAgent().run(jid, "no-such-video", ["youtube"])
            return await _job_row(jid)
        row = asyncio.run(run())
        assert row.status == "failed" and "not found" in row.error_message

    def test_a_video_row_with_no_file_path_fails_before_any_provider(
            self, ws, providers):
        async def run():
            vid = await _make_video(video_path=None)
            jid = await _job()
            await UploadAgent().run(jid, vid, ["youtube"])
            return await _job_row(jid)
        row = asyncio.run(run())
        assert row.status == "failed" and "path is missing" in row.error_message
        assert "youtube" not in providers["calls"]


# ── per-platform success ────────────────────────────────────────────────────

class TestSuccessfulUploads:
    def test_youtube_uses_the_drafted_metadata_and_persists_the_id(self, ws, providers):
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            vid = await _make_video(
                youtube_title="Catchy title", youtube_description="Desc here",
                youtube_tags_json=json.dumps(["a", "b"]))
            jid = await _job()
            await UploadAgent().run(jid, vid, ["youtube"])
            return await _job_row(jid), await _video_row(vid)

        job, video = asyncio.run(run())
        assert job.status == "success"
        call = providers["calls"]["youtube"]
        assert call["title"] == "Catchy title"
        assert call["description"] == "Desc here"
        assert call["tags"] == ["a", "b"]
        assert video.youtube_video_id == "yt1"
        assert json.loads(video.uploaded_platforms_json) == ["youtube"]
        assert video.status == "uploaded"
        assert any(m.get("type") == "upload_complete" and m.get("platform") == "youtube"
                   for m in ws["sent"])

    def test_a_video_without_drafted_metadata_falls_back_to_its_title(
            self, ws, providers):
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            vid = await _make_video()
            await UploadAgent().run(await _job(), vid, ["youtube"])
        asyncio.run(run())
        assert providers["calls"]["youtube"]["title"] == "A video"
        assert providers["calls"]["youtube"]["tags"] == []

    def test_tiktok_uses_the_oauth_token_and_the_saved_privacy(self, ws, providers):
        async def run():
            await _make_settings(tiktok_upload_token_encrypted=encrypt("TOK"),
                                 tiktok_default_privacy="SELF_ONLY")
            vid = await _make_video(tiktok_title="tok title")
            jid = await _job()
            await UploadAgent().run(jid, vid, ["tiktok"])
            return await _video_row(vid)
        video = asyncio.run(run())
        call = providers["calls"]["tiktok"]
        assert call["access_token"] == "TOK" and call["cookie_sessionid"] == ""
        assert call["privacy"] == "SELF_ONLY"
        assert call["title"] == "tok title"
        assert video.tiktok_publish_id == "tt1"

    def test_tiktok_falls_back_to_the_session_cookie(self, ws, providers):
        async def run():
            await _make_settings(tiktok_upload_token_encrypted=None,
                                 tiktok_cookie_encrypted=encrypt("SESSION"),
                                 tiktok_default_privacy=None)
            await UploadAgent().run(await _job(), await _make_video(), ["tiktok"])
        asyncio.run(run())
        call = providers["calls"]["tiktok"]
        assert call["cookie_sessionid"] == "SESSION" and call["access_token"] == ""
        assert call["privacy"] == "PUBLIC_TO_EVERYONE", "the default privacy"

    def test_instagram_reuses_the_tiktok_caption(self, ws, providers):
        async def run():
            await _make_settings(
                instagram_access_token_encrypted=encrypt("IGT"),
                instagram_user_id="ig_user_1")
            vid = await _make_video(tiktok_title="short caption")
            jid = await _job()
            await UploadAgent().run(jid, vid, ["instagram"])
            return await _video_row(vid)
        video = asyncio.run(run())
        call = providers["calls"]["instagram"]
        assert call["caption"] == "short caption"
        assert call["access_token"] == "IGT" and call["ig_user_id"] == "ig_user_1"
        assert video.instagram_media_id == "ig1"

    def test_uploading_to_every_platform_at_once(self, ws, providers):
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'),
                tiktok_upload_token_encrypted=encrypt("TOK"),
                instagram_access_token_encrypted=encrypt("IGT"))
            vid = await _make_video()
            jid = await _job()
            await UploadAgent().run(jid, vid, ["youtube", "tiktok", "instagram"])
            return await _job_row(jid), await _video_row(vid)
        job, video = asyncio.run(run())
        assert job.status == "success"
        assert set(json.loads(video.uploaded_platforms_json)) == {
            "youtube", "tiktok", "instagram"}

    def test_re_uploading_does_not_duplicate_the_platform(self, ws, providers):
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            vid = await _make_video(uploaded_platforms_json=json.dumps(["youtube"]))
            await UploadAgent().run(await _job(), vid, ["youtube"])
            return await _video_row(vid)
        video = asyncio.run(run())
        assert json.loads(video.uploaded_platforms_json) == ["youtube"]


# ── partial failure, the reason this agent exists ───────────────────────────

class TestPartialFailure:
    def test_one_platform_failing_never_stops_the_others(self, ws, providers):
        """The whole point: a TikTok outage must not cost the YouTube upload."""
        providers["outcomes"]["tiktok"] = RuntimeError("tiktok is down")

        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'),
                tiktok_upload_token_encrypted=encrypt("TOK"))
            vid = await _make_video()
            jid = await _job()
            await UploadAgent().run(jid, vid, ["tiktok", "youtube"])
            return await _job_row(jid), await _video_row(vid)

        job, video = asyncio.run(run())
        assert job.status == "success", "one failure must not sink a real upload"
        assert "youtube" in job.current_step and "tiktok" in job.current_step
        out = json.loads(job.output_json)
        assert out["uploaded_platforms"] == ["youtube"]
        assert any("tiktok" in e for e in out["errors"])
        assert json.loads(video.uploaded_platforms_json) == ["youtube"]

    def test_everything_failing_marks_the_job_failed(self, ws, providers):
        providers["outcomes"]["youtube"] = RuntimeError("boom")

        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            jid = await _job()
            await UploadAgent().run(jid, await _make_video(), ["youtube"])
            return await _job_row(jid)
        job = asyncio.run(run())
        assert job.status == "failed" and "boom" in job.error_message

    def test_an_unknown_platform_is_reported_not_ignored(self, ws, providers):
        async def run():
            jid = await _job()
            await UploadAgent().run(jid, await _make_video(), ["myspace"])
            return await _job_row(jid)
        job = asyncio.run(run())
        assert job.status == "failed" and "Unknown platform" in job.error_message


# ── auth failures route to the setup wizard ─────────────────────────────────

class TestAuthFailures:
    @pytest.mark.parametrize("platform,wizard", [
        ("youtube", "youtube_auth"),
        ("tiktok", "tiktok_upload_auth"),
        ("instagram", "instagram_upload_auth"),
    ])
    def test_a_disconnected_account_names_its_setup_wizard(
            self, ws, providers, platform, wizard):
        """An auth error the user can fix must arrive as a re-connect prompt,
        not a generic red error."""
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=None,
                tiktok_upload_token_encrypted=None, tiktok_cookie_encrypted=None,
                instagram_access_token_encrypted=None)
            jid = await _job()
            await UploadAgent().run(jid, await _make_video(), [platform])
            return await _job_row(jid)

        job = asyncio.run(run())
        assert job.status == "failed"
        warn = ws["warnings"][0]
        assert warn["constraint"] == f"{platform}_upload_auth"
        assert warn["severity"] == "error"
        assert warn["wizard_id"] == wizard
        assert "Settings" in warn["message"]

    @pytest.mark.parametrize("field,platform", [
        ("youtube_credentials_json_encrypted", "youtube"),
        ("tiktok_upload_token_encrypted", "tiktok"),
        ("instagram_access_token_encrypted", "instagram"),
    ])
    def test_corrupted_credentials_ask_for_a_reconnect(
            self, ws, providers, field, platform):
        """A rotated ENCRYPTION_KEY makes every stored token undecryptable.
        That must read as "reconnect", not as a crash."""
        async def run():
            fields = {"youtube_credentials_json_encrypted": None,
                      "tiktok_upload_token_encrypted": None,
                      "tiktok_cookie_encrypted": None,
                      "instagram_access_token_encrypted": None}
            fields[field] = "not-a-valid-fernet-token"
            await _make_settings(**fields)
            jid = await _job()
            await UploadAgent().run(jid, await _make_video(), [platform])
            return await _job_row(jid)

        job = asyncio.run(run())
        assert job.status == "failed"
        assert "corrupted" in ws["warnings"][0]["message"]
        assert "reconnect" in ws["warnings"][0]["message"].lower()

    def test_a_provider_raising_an_auth_error_also_warns(self, ws, providers):
        providers["outcomes"]["youtube"] = UploadAuthError("token expired")

        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            await UploadAgent().run(await _job(), await _make_video(), ["youtube"])
        asyncio.run(run())
        assert ws["warnings"][0]["constraint"] == "youtube_upload_auth"

    def test_no_settings_row_at_all_is_an_auth_error_not_a_crash(
            self, ws, providers, monkeypatch):
        async def run():
            from backend.agents import uploader as U
            agent = U.UploadAgent()
            with pytest.raises(UploadAuthError):
                await agent._upload_youtube(object(), None)
            with pytest.raises(UploadAuthError):
                await agent._upload_tiktok(object(), None)
            with pytest.raises(UploadAuthError):
                await agent._upload_instagram(object(), None)
        asyncio.run(run())


class TestPersistence:
    def test_marking_a_vanished_video_is_survivable(self, ws, providers):
        """The row can be deleted while an upload is in flight."""
        async def run():
            await UploadAgent()._mark_platform_uploaded(
                "gone", "youtube", "youtube_video_id", "x")
        asyncio.run(run())  # must not raise

    def test_a_corrupt_platforms_json_is_replaced_not_propagated(
            self, ws, providers):
        async def run():
            vid = await _make_video(uploaded_platforms_json="{not json")
            await UploadAgent()._mark_platform_uploaded(
                vid, "tiktok", "tiktok_publish_id", "t9")
            return await _video_row(vid)
        video = asyncio.run(run())
        assert json.loads(video.uploaded_platforms_json) == ["tiktok"]


class TestPlatformIdPersistence:
    """The stored per-platform id is what lets the Library link to the live
    post — losing it means the user can't find what they published."""

    def test_a_platform_with_no_returned_id_still_records_the_upload(
            self, ws, providers):
        providers["outcomes"]["youtube"] = {"url": "https://youtu.be/x"}

        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            vid = await _make_video()
            await UploadAgent().run(await _job(), vid, ["youtube"])
            return await _video_row(vid)
        video = asyncio.run(run())
        assert json.loads(video.uploaded_platforms_json) == ["youtube"]

    def test_an_already_uploaded_platform_list_is_extended_not_replaced(
            self, ws, providers):
        async def run():
            await _make_settings(
                tiktok_upload_token_encrypted=encrypt("TOK"),
                tiktok_default_privacy=None)
            vid = await _make_video(
                uploaded_platforms_json=json.dumps(["youtube"]))
            await UploadAgent().run(await _job(), vid, ["tiktok"])
            return await _video_row(vid)
        video = asyncio.run(run())
        assert set(json.loads(video.uploaded_platforms_json)) == {"youtube", "tiktok"}

    def test_the_row_is_marked_uploaded(self, ws, providers):
        async def run():
            await _make_settings(
                youtube_credentials_json_encrypted=encrypt('{"token":"t"}'))
            vid = await _make_video()
            await UploadAgent().run(await _job(), vid, ["youtube"])
            return await _video_row(vid)
        assert asyncio.run(run()).status == "uploaded"
