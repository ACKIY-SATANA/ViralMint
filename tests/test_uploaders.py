# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Uploader surface — YouTube, TikTok and Instagram.

Auto-upload is one of the two things this project ships that the hosted
variant does not (the other is BYOK), so it has to keep working, and it is the
worst place for a silent failure: a user who thinks a video was posted and
finds nothing on their channel has lost the whole point of the pipeline.

Every path here is exercised offline. `httpx` is stubbed, the Google client
libraries are injected as fakes (they're optional dependencies and may not be
installed), and every `asyncio.sleep` in a poll loop is neutralised so the
timeout branches run instantly instead of taking a real minute.

The contract these tests pin, on every provider:
  - a missing/expired credential raises UploadAuthError (the UI routes that to
    a re-connect prompt), NOT the generic UploadError;
  - a missing file is caught before any network call;
  - a provider that reports failure raises rather than returning a fake id.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from backend.core.exceptions import UploadAuthError, UploadError


@pytest.fixture()
def video(tmp_path):
    p = tmp_path / "out.mp4"
    p.write_bytes(b"\x00" * 2048)
    return p


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Poll loops here sleep 2-5s per turn; without this the timeout tests
    would take minutes."""
    async def instant(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", instant)


# ══════════════════════════════════════════════════════════════════════
# YouTube
# ══════════════════════════════════════════════════════════════════════

class _FakeRequest:
    """Mimics googleapiclient's resumable upload request."""

    def __init__(self, chunks=2, response=None, raises=None):
        self._left = chunks
        self._response = response if response is not None else {"id": "yt_abc123"}
        self._raises = raises

    def next_chunk(self):
        if self._raises:
            raise self._raises
        self._left -= 1
        if self._left > 0:
            status = types.SimpleNamespace(progress=lambda: 0.5)
            return status, None
        return None, self._response


def _install_google(monkeypatch, request=None, channels=None, fetch_raises=None):
    """Inject fake google client libs — they're optional deps."""
    req = request or _FakeRequest()
    captured: dict = {}

    class _Videos:
        def insert(self, part, body, media_body):
            captured["part"] = part
            captured["body"] = body
            captured["media"] = media_body
            return req

    class _Channels:
        def list(self, part, mine):
            return types.SimpleNamespace(
                execute=lambda: channels if channels is not None else {"items": []})

    class _YouTube:
        def videos(self):
            return _Videos()

        def channels(self):
            return _Channels()

    disc = types.ModuleType("googleapiclient.discovery")
    disc.build = lambda *a, **k: _YouTube()
    http = types.ModuleType("googleapiclient.http")

    class _Media:
        def __init__(self, path, mimetype=None, resumable=None, chunksize=None):
            captured["media_path"] = path
            captured["chunksize"] = chunksize
    http.MediaFileUpload = _Media

    creds_mod = types.ModuleType("google.oauth2.credentials")

    class _Credentials:
        def __init__(self, **kw):
            captured["creds"] = kw
            self.token = kw.get("token")
            self.refresh_token = kw.get("refresh_token")
            self.token_uri = kw.get("token_uri")
            self.client_id = kw.get("client_id")
            self.client_secret = kw.get("client_secret")
    creds_mod.Credentials = _Credentials

    flow_mod = types.ModuleType("google_auth_oauthlib.flow")

    class _Flow:
        redirect_uri = ""
        credentials = _Credentials(token="t", refresh_token="r",
                                   token_uri="u", client_id="ci",
                                   client_secret="cs")

        @classmethod
        def from_client_config(cls, config, scopes):
            captured["oauth_config"] = config
            captured["scopes"] = scopes
            return cls()

        def authorization_url(self, **kw):
            captured["auth_kw"] = kw
            return ("https://accounts.google.com/o/oauth2/auth?fake=1", "state")

        def fetch_token(self, code):
            captured["code"] = code
            if fetch_raises:
                raise fetch_raises
    flow_mod.Flow = _Flow

    for name, mod in (
        ("googleapiclient", types.ModuleType("googleapiclient")),
        ("googleapiclient.discovery", disc),
        ("googleapiclient.http", http),
        ("google", types.ModuleType("google")),
        ("google.oauth2", types.ModuleType("google.oauth2")),
        ("google.oauth2.credentials", creds_mod),
        ("google_auth_oauthlib", types.ModuleType("google_auth_oauthlib")),
        ("google_auth_oauthlib.flow", flow_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return captured


CREDS = json.dumps({"token": "tok", "refresh_token": "ref",
                    "client_id": "cid", "client_secret": "sec"})


class TestYouTubeUpload:
    async def test_a_successful_upload_returns_id_and_watch_url(self, video, monkeypatch):
        from backend.services.youtube_uploader import upload_to_youtube
        _install_google(monkeypatch)
        out = await upload_to_youtube(str(video), "My title", credentials_json=CREDS)
        assert out == {"video_id": "yt_abc123",
                       "url": "https://youtube.com/watch?v=yt_abc123"}

    async def test_no_credentials_is_an_AUTH_error_not_a_generic_failure(self, video):
        """The UI routes UploadAuthError to a re-connect prompt."""
        from backend.services.youtube_uploader import upload_to_youtube
        with pytest.raises(UploadAuthError):
            await upload_to_youtube(str(video), "t", credentials_json="")

    async def test_a_missing_file_is_caught_before_any_api_call(self, monkeypatch):
        from backend.services.youtube_uploader import upload_to_youtube
        _install_google(monkeypatch)
        with pytest.raises(UploadError, match="not found"):
            await upload_to_youtube("/no/such/file.mp4", "t", credentials_json=CREDS)

    async def test_the_api_limits_are_respected(self, video, monkeypatch):
        """YouTube rejects >100 char titles, >5000 char descriptions and
        >15 tags outright — truncating beats a 400."""
        from backend.services.youtube_uploader import upload_to_youtube
        cap = _install_google(monkeypatch)
        await upload_to_youtube(
            str(video), "T" * 300, description="D" * 9000,
            tags=[f"tag{i}" for i in range(40)], credentials_json=CREDS)
        sn = cap["body"]["snippet"]
        assert len(sn["title"]) == 100
        assert len(sn["description"]) == 5000
        assert len(sn["tags"]) == 15

    async def test_the_upload_is_resumable_and_chunked(self, video, monkeypatch):
        """A phone-shot 4K clip over a flaky line needs resumability."""
        from backend.services.youtube_uploader import upload_to_youtube
        cap = _install_google(monkeypatch)
        await upload_to_youtube(str(video), "t", credentials_json=CREDS)
        assert cap["chunksize"] == 10 * 1024 * 1024

    async def test_privacy_and_category_reach_the_request(self, video, monkeypatch):
        from backend.services.youtube_uploader import upload_to_youtube
        cap = _install_google(monkeypatch)
        await upload_to_youtube(str(video), "t", category_id="27",
                                privacy="unlisted", credentials_json=CREDS)
        assert cap["body"]["status"]["privacyStatus"] == "unlisted"
        assert cap["body"]["snippet"]["categoryId"] == "27"
        assert cap["body"]["status"]["selfDeclaredMadeForKids"] is False

    async def test_a_completed_upload_with_no_id_is_a_failure(self, video, monkeypatch):
        """Reporting success without a video id would leave the user hunting
        for a video that doesn't exist."""
        from backend.services.youtube_uploader import upload_to_youtube
        _install_google(monkeypatch, request=_FakeRequest(chunks=1, response={}))
        with pytest.raises(UploadError, match="no video ID"):
            await upload_to_youtube(str(video), "t", credentials_json=CREDS)

    @pytest.mark.parametrize("msg", ["invalid_grant: bad", "Token has been revoked"])
    async def test_an_expired_token_is_reclassified_as_an_AUTH_error(
            self, video, monkeypatch, msg):
        """Google raises a generic exception; the user needs "re-connect",
        not "upload failed"."""
        from backend.services.youtube_uploader import upload_to_youtube
        _install_google(monkeypatch, request=_FakeRequest(raises=RuntimeError(msg)))
        with pytest.raises(UploadAuthError):
            await upload_to_youtube(str(video), "t", credentials_json=CREDS)

    async def test_an_unrelated_failure_stays_a_generic_upload_error(
            self, video, monkeypatch):
        from backend.services.youtube_uploader import upload_to_youtube
        _install_google(monkeypatch,
                        request=_FakeRequest(raises=RuntimeError("quotaExceeded")))
        with pytest.raises(UploadError) as ei:
            await upload_to_youtube(str(video), "t", credentials_json=CREDS)
        assert not isinstance(ei.value, UploadAuthError)


class TestYouTubeOAuth:
    def test_the_auth_url_asks_for_offline_access_and_consent(self, monkeypatch):
        """Without offline+consent there is no refresh token, and the
        connection silently dies after an hour."""
        from backend.services.youtube_uploader import build_youtube_auth_url
        cap = _install_google(monkeypatch)
        url = build_youtube_auth_url()
        assert url.startswith("https://accounts.google.com/")
        assert cap["auth_kw"]["access_type"] == "offline"
        assert cap["auth_kw"]["prompt"] == "consent"
        assert any("youtube.upload" in s for s in cap["scopes"])

    async def test_exchanging_a_code_returns_credentials_and_the_channel_name(
            self, monkeypatch):
        from backend.services.youtube_uploader import exchange_youtube_code
        cap = _install_google(
            monkeypatch, channels={"items": [{"snippet": {"title": "My Channel"}}]})
        out = await exchange_youtube_code("auth-code-123")
        assert cap["code"] == "auth-code-123"
        assert out["channel_title"] == "My Channel"
        assert out["credentials"]["refresh_token"] == "r"

    async def test_an_account_with_no_channel_still_connects(self, monkeypatch):
        from backend.services.youtube_uploader import exchange_youtube_code
        _install_google(monkeypatch, channels={"items": []})
        assert (await exchange_youtube_code("c"))["channel_title"] == ""


# ══════════════════════════════════════════════════════════════════════
# TikTok
# ══════════════════════════════════════════════════════════════════════

class _R:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or "{}"
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._payload


class _TTClient:
    """Scripted httpx.AsyncClient for the TikTok 3-step publish flow."""

    def __init__(self, init=None, chunk=None, statuses=None):
        self.init = init or _R(payload={"data": {"publish_id": "pub_1",
                                                 "upload_url": "https://up.tiktok/x"}})
        self.chunk = chunk or _R(status=201)
        self.statuses = list(statuses or [_R(payload={"data": {"status": "PUBLISH_COMPLETE"}})])
        self.posts: list[tuple] = []
        self.puts: list[dict] = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, data=None):
        self.posts.append((url, json or data))
        if "init" in url:
            return self.init
        if "status/fetch" in url:
            return self.statuses.pop(0) if self.statuses else _R(status=500)
        return _R()

    async def put(self, url, headers=None, content=None):
        self.puts.append({"url": url, "headers": headers, "len": len(content or b"")})
        return self.chunk


class TestTikTokUpload:
    async def test_neither_token_nor_cookie_is_an_AUTH_error(self, video):
        from backend.services.tiktok_uploader import upload_to_tiktok
        with pytest.raises(UploadAuthError, match="OAuth access token or session cookie"):
            await upload_to_tiktok(str(video), "t")

    async def test_a_missing_file_is_caught_first(self):
        from backend.services.tiktok_uploader import upload_to_tiktok
        with pytest.raises(UploadError, match="not found"):
            await upload_to_tiktok("/no/such.mp4", "t", access_token="TOK")

    async def test_the_happy_path_returns_the_publish_id(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient()
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        assert await TT.upload_to_tiktok(str(video), "t", access_token="TOK") == {
            "publish_id": "pub_1"}

    async def test_the_whole_file_is_uploaded_in_content_range_chunks(
            self, tmp_path, monkeypatch):
        """A short-form clip is one chunk, but the ranges must still be right —
        an off-by-one here truncates the video server-side."""
        from backend.services import tiktok_uploader as TT
        big = tmp_path / "big.mp4"
        big.write_bytes(b"\x01" * 1500)
        c = _TTClient()
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        await TT.upload_to_tiktok(str(big), "t", access_token="TOK")
        assert len(c.puts) == 1
        assert c.puts[0]["headers"]["Content-Range"] == "bytes 0-1499/1500"
        assert c.puts[0]["len"] == 1500

    async def test_the_title_is_capped_at_the_api_limit(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient()
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        await TT.upload_to_tiktok(str(video), "T" * 400, access_token="TOK")
        assert len(c.posts[0][1]["post_info"]["title"]) == 150

    async def test_a_401_on_init_is_an_AUTH_error(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(init=_R(status=401, payload={"error": {"message": "bad token"}}))
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadAuthError):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")

    async def test_a_non_auth_init_failure_is_a_generic_error(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(init=_R(status=400, payload={"error": {"message": "video too long"}}))
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadError, match="video too long"):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")

    async def test_init_without_an_upload_url_fails_loudly(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(init=_R(payload={"data": {"publish_id": "p"}}))
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadError, match="no publish_id or upload_url"):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")

    async def test_a_rejected_chunk_fails_the_upload(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(chunk=_R(status=500))
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadError, match="chunk upload failed"):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")

    async def test_a_publish_failure_surfaces_tiktoks_reason(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(statuses=[_R(payload={"data": {"status": "FAILED",
                                                     "fail_reason": "copyright"}})])
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadError, match="copyright"):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")

    async def test_it_keeps_polling_through_a_pending_status(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        c = _TTClient(statuses=[
            _R(payload={"data": {"status": "PROCESSING_UPLOAD"}}),
            _R(status=500),
            _R(payload={"data": {"status": "PUBLISH_COMPLETE"}}),
        ])
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        assert (await TT.upload_to_tiktok(str(video), "t", access_token="TOK"))[
            "publish_id"] == "pub_1"

    async def test_a_publish_that_never_confirms_times_out(self, video, monkeypatch):
        """Never return success on an unconfirmed publish."""
        from backend.services import tiktok_uploader as TT
        c = _TTClient(statuses=[_R(payload={"data": {"status": "PROCESSING_UPLOAD"}})] * 40)
        monkeypatch.setattr(TT.httpx, "AsyncClient", c)
        with pytest.raises(UploadError, match="timed out"):
            await TT.upload_to_tiktok(str(video), "t", access_token="TOK")


class TestTikTokCookieFallback:
    async def test_it_routes_to_the_cookie_path_when_only_a_cookie_is_given(
            self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        calls = {}
        mod = types.ModuleType("tiktok_uploader.upload")

        def fake_upload(filename, description, cookies):
            calls["file"] = filename
            calls["desc"] = description
            calls["cookie_file"] = cookies
            assert Path(cookies).exists(), "the cookie file must exist during upload"
            return True
        mod.upload_video = fake_upload
        monkeypatch.setitem(sys.modules, "tiktok_uploader", types.ModuleType("tiktok_uploader"))
        monkeypatch.setitem(sys.modules, "tiktok_uploader.upload", mod)

        out = await TT.upload_to_tiktok(str(video), "hello", cookie_sessionid="SESSION")
        assert out["publish_id"].startswith("cookie_upload_")
        assert calls["desc"] == "hello"
        assert not Path(calls["cookie_file"]).exists(), (
            "the session cookie must be deleted after the upload, not left on disk"
        )

    async def test_a_library_failure_becomes_an_upload_error(self, video, monkeypatch):
        from backend.services import tiktok_uploader as TT
        mod = types.ModuleType("tiktok_uploader.upload")

        def boom(**kw):
            raise RuntimeError("selenium exploded")
        mod.upload_video = boom
        monkeypatch.setitem(sys.modules, "tiktok_uploader", types.ModuleType("tiktok_uploader"))
        monkeypatch.setitem(sys.modules, "tiktok_uploader.upload", mod)
        with pytest.raises(UploadError, match="cookie upload failed"):
            await TT.upload_to_tiktok(str(video), "t", cookie_sessionid="S")


class TestTikTokOAuth:
    def test_the_auth_url_requests_the_publishing_scopes(self):
        from backend.services.tiktok_uploader import build_tiktok_auth_url
        url = build_tiktok_auth_url()
        assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
        assert "video.upload" in url and "video.publish" in url
        assert "response_type=code" in url

    async def test_a_successful_exchange_returns_both_tokens(self, monkeypatch):
        from backend.services import tiktok_uploader as TT

        class C(_TTClient):
            async def post(self, url, headers=None, json=None, data=None):
                return _R(payload={"access_token": "AT", "refresh_token": "RT",
                                   "expires_in": 100})
        monkeypatch.setattr(TT.httpx, "AsyncClient", C())
        out = await TT.exchange_tiktok_code("code")
        assert out == {"access_token": "AT", "refresh_token": "RT", "expires_in": 100}

    async def test_a_rejected_exchange_is_an_AUTH_error(self, monkeypatch):
        from backend.services import tiktok_uploader as TT

        class C(_TTClient):
            async def post(self, url, headers=None, json=None, data=None):
                return _R(status=400, text="bad code")
        monkeypatch.setattr(TT.httpx, "AsyncClient", C())
        with pytest.raises(UploadAuthError):
            await TT.exchange_tiktok_code("code")

    async def test_a_200_without_a_token_is_still_an_AUTH_error(self, monkeypatch):
        from backend.services import tiktok_uploader as TT

        class C(_TTClient):
            async def post(self, url, headers=None, json=None, data=None):
                return _R(payload={"error": "invalid_client"})
        monkeypatch.setattr(TT.httpx, "AsyncClient", C())
        with pytest.raises(UploadAuthError, match="no access_token"):
            await TT.exchange_tiktok_code("code")


# ══════════════════════════════════════════════════════════════════════
# Instagram
# ══════════════════════════════════════════════════════════════════════

class _IGHttp:
    """Stand-in for the module-level `httpx` functions Instagram uses."""

    def __init__(self, **routes):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def _pick(self, method, url):
        self.calls.append((method, url))
        for key, resp in self.routes.items():
            if key in url:
                return resp() if callable(resp) else resp
        return _R(status=404, text="unrouted")

    def get(self, url, params=None, timeout=None):
        return self._pick("GET", url)

    def post(self, url, data=None, files=None, timeout=None):
        return self._pick("POST", url)

    def put(self, url, content=None, headers=None, timeout=None):
        return self._pick("PUT", url)


class TestInstagramUpload:
    async def test_no_token_is_an_AUTH_error(self):
        from backend.services.instagram_uploader import upload_to_instagram
        with pytest.raises(UploadAuthError, match="not connected"):
            await upload_to_instagram("/x.mp4", access_token="")

    async def test_the_full_reel_flow(self, video, monkeypatch):
        from backend.services import instagram_uploader as IG
        # Routes match by substring in declaration order, so ids must not be
        # substrings of the endpoint paths (a media id of "media_9" would be
        # caught by the "/media" route).
        http = _IGHttp(**{
            "file.io": _R(payload={"link": "https://file.io/tmp"}),
            "/media_publish": _R(payload={"id": "IGM9"}),
            "IGC1": _R(payload={"status_code": "FINISHED"}),
            "IGM9": _R(payload={"permalink": "https://instagram.com/reel/xyz"}),
            "/media": _R(payload={"id": "IGC1"}),
        })
        monkeypatch.setitem(sys.modules, "httpx", http)
        out = await IG.upload_to_instagram(
            str(video), caption="hi", access_token="AT", ig_user_id="ig1")
        assert out == {"media_id": "IGM9",
                       "permalink": "https://instagram.com/reel/xyz"}

    async def test_a_missing_file_is_caught_before_uploading_anywhere(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp())
        with pytest.raises(UploadError, match="not found"):
            await IG.upload_to_instagram("/no/such.mp4", access_token="AT", ig_user_id="x")

    async def test_an_account_with_no_linked_page_is_an_AUTH_error(
            self, video, monkeypatch):
        """The single most common Instagram setup mistake — the error has to
        name the fix, not just fail."""
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"/me/accounts": _R(payload={"data": []})}))
        with pytest.raises(UploadAuthError, match="Professional account"):
            await IG.upload_to_instagram(str(video), access_token="AT")

    async def test_the_ig_user_id_is_discovered_from_the_page(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{
            "/me/accounts": _R(payload={"data": [{"id": "page_5"}]}),
            "page_5": _R(payload={"instagram_business_account": {"id": "ig_77"}}),
        }))
        assert await IG._get_ig_user_id("AT") == "ig_77"

    async def test_a_page_without_a_linked_ig_account_returns_none(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{
            "/me/accounts": _R(payload={"data": [{"id": "page_5"}]}),
            "page_5": _R(payload={}),
        }))
        assert await IG._get_ig_user_id("AT") is None

    async def test_a_failed_page_lookup_returns_none(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"/me/accounts": _R(status=400, text="nope")}))
        assert await IG._get_ig_user_id("AT") is None


class TestInstagramContainer:
    async def test_a_rejected_container_raises(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"/media": _R(status=400, text="bad video")}))
        with pytest.raises(UploadError, match="container creation failed"):
            await IG._create_media_container("ig1", "http://v", "c", "AT")

    async def test_a_container_response_without_an_id_raises(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{"/media": _R(payload={})}))
        with pytest.raises(UploadError, match="did not return a container ID"):
            await IG._create_media_container("ig1", "http://v", "c", "AT")

    async def test_processing_errors_are_reported(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"c1": _R(payload={"status_code": "ERROR"})}))
        with pytest.raises(UploadError, match="processing failed"):
            await IG._wait_for_container("c1", "AT")

    async def test_an_expired_container_is_reported(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"c1": _R(payload={"status_code": "EXPIRED"})}))
        with pytest.raises(UploadError, match="expired or invalid"):
            await IG._wait_for_container("c1", "AT")

    async def test_an_unreadable_status_is_not_treated_as_ready(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"c1": _R(status=500)}))
        with pytest.raises(UploadError):
            await IG._wait_for_container("c1", "AT")

    async def test_a_finished_container_returns_quietly(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"c1": _R(payload={"status_code": "FINISHED"})}))
        assert await IG._wait_for_container("c1", "AT") is None

    async def test_a_failed_publish_raises(self, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"/media_publish": _R(status=400, text="denied")}))
        with pytest.raises(UploadError, match="publish failed"):
            await IG._publish_container("ig1", "c1", "AT")

    async def test_a_missing_permalink_degrades_to_empty(self, monkeypatch):
        """Cosmetic — never fail a published Reel over its permalink."""
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{"m1": _R(status=500)}))
        assert await IG._get_permalink("m1", "AT") == ""


class TestInstagramPublicUrl:
    async def test_the_primary_host_is_used_when_it_works(self, video, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx",
                            _IGHttp(**{"file.io": _R(payload={"link": "https://file.io/AAA"})}))
        assert await IG._get_public_video_url(video) == "https://file.io/AAA"

    async def test_it_falls_back_to_the_second_host(self, video, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{
            "file.io": _R(status=500),
            "transfer.sh": _R(status=200, text="https://transfer.sh/x/out.mp4"),
        }))
        assert await IG._get_public_video_url(video) == "https://transfer.sh/x/out.mp4"

    async def test_both_hosts_failing_raises_a_named_error(self, video, monkeypatch):
        """Instagram requires a public URL — say that, don't fail cryptically."""
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{
            "file.io": _R(status=500), "transfer.sh": _R(status=503)}))
        with pytest.raises(UploadError, match="publicly accessible"):
            await IG._get_public_video_url(video)

    async def test_a_200_without_a_link_falls_through(self, video, monkeypatch):
        from backend.services import instagram_uploader as IG
        monkeypatch.setitem(sys.modules, "httpx", _IGHttp(**{
            "file.io": _R(payload={}),
            "transfer.sh": _R(status=200, text="https://transfer.sh/y"),
        }))
        assert await IG._get_public_video_url(video) == "https://transfer.sh/y"
