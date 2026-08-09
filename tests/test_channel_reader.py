# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Channel reader — resolving a channel URL and listing its videos.

This module is a stack of fallbacks, and that's the point. YouTube's uploads
playlist genuinely returns 500s for some channels, so listing a channel walks:
playlistItems (1 quota unit) → Search API (100 units) → yt-dlp (0 units, no
key). Each rung exists because the one above it fails in the wild, and a rung
that swallows an error and returns `[]` looks identical to "this channel has
no videos" — the user sees an empty Channels page and no reason why.

Everything is offline: the Google client is a fake, yt-dlp is stubbed, and the
retry sleeps are neutralised so the 5xx-retry branch runs instantly.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from backend.services import channel_reader as CR


@pytest.fixture(autouse=True)
def clean_cache():
    CR.cache_clear()
    yield
    CR.cache_clear()


class _HttpError(Exception):
    """Stand-in for googleapiclient.errors.HttpError."""

    def __init__(self, status=500):
        super().__init__(f"HTTP {status}")
        self.resp = types.SimpleNamespace(status=status)


@pytest.fixture(autouse=True)
def google_stub(monkeypatch):
    """Inject googleapiclient — it's an optional dependency."""
    errors = types.ModuleType("googleapiclient.errors")
    errors.HttpError = _HttpError
    disc = types.ModuleType("googleapiclient.discovery")
    disc.build = lambda *a, **k: None      # replaced per-test
    monkeypatch.setitem(sys.modules, "googleapiclient",
                        types.ModuleType("googleapiclient"))
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", errors)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", disc)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    return disc


def _install_youtube(google_stub, obj):
    google_stub.build = lambda *a, **k: obj


class _Call:
    """A `.list(**kw).execute()` chain returning scripted responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.kwargs: list[dict] = []

    def list(self, **kw):
        self.kwargs.append(kw)
        nxt = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        def execute():
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return types.SimpleNamespace(execute=execute)


class _YouTube:
    def __init__(self, channels=None, playlist=None, search=None, videos=None):
        self._channels = channels or _Call({})
        self._playlist = playlist or _Call({"items": []})
        self._search = search or _Call({"items": []})
        self._videos = videos or _Call({"items": []})

    def channels(self):
        return self._channels

    def playlistItems(self):
        return self._playlist

    def search(self):
        return self._search

    def videos(self):
        return self._videos


# ── the in-process cache ────────────────────────────────────────────────────

class TestCache:
    def test_a_stored_value_reads_back(self):
        CR._cache_set("yt:abc", {"n": 1})
        assert CR._cache_get("yt:abc") == {"n": 1}

    def test_a_miss_is_none(self):
        assert CR._cache_get("nothing") is None

    def test_an_expired_entry_is_evicted_not_served(self, monkeypatch):
        CR._cache_set("yt:old", {"n": 1})
        real_now = time.time()      # capture BEFORE patching, or the lambda recurses
        monkeypatch.setattr(CR.time, "time",
                            lambda: real_now + CR.CACHE_TTL + 10)
        assert CR._cache_get("yt:old") is None
        assert "yt:old" not in CR._cache, "the expired entry must be dropped"

    def test_clearing_by_prefix_leaves_the_rest(self):
        CR._cache_set("yt:1", {}), CR._cache_set("yt:2", {}), CR._cache_set("tt:1", {})
        CR.cache_clear("yt:")
        assert "tt:1" in CR._cache and "yt:1" not in CR._cache

    def test_clearing_with_no_prefix_clears_everything(self):
        CR._cache_set("yt:1", {}), CR._cache_set("tt:1", {})
        CR.cache_clear()
        assert CR._cache == {}


# ── URL → channel id ────────────────────────────────────────────────────────

class TestResolveChannelId:
    def _resolve(self, url, key="KEY"):
        return asyncio.run(CR.resolve_youtube_channel_id(url, key))

    def test_a_canonical_channel_url_needs_no_api_call(self, google_stub):
        """/channel/UC… already contains the id — spending quota on it would
        be silly."""
        _install_youtube(google_stub, None)   # any call would explode
        assert self._resolve(
            "https://youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"
        ) == "UC_x5XG1OV2P6uZZ5FSM9Ttw"

    def test_a_handle_is_resolved_via_the_api(self, google_stub):
        _install_youtube(google_stub, _YouTube(
            channels=_Call({"items": [{"id": "UCHANDLE"}]})))
        assert self._resolve("https://youtube.com/@mkbhd") == "UCHANDLE"

    def test_a_handle_falls_back_to_search(self, google_stub):
        """forHandle only works for real @handles; legacy names need search."""
        _install_youtube(google_stub, _YouTube(
            channels=_Call({"items": []}),
            search=_Call({"items": [{"snippet": {"channelId": "UCSEARCH"}}]})))
        assert self._resolve("https://youtube.com/@legacyname") == "UCSEARCH"

    @pytest.mark.parametrize("url", [
        "https://youtube.com/c/SomeName",
        "https://youtube.com/user/SomeName",
    ])
    def test_legacy_url_forms_are_resolved(self, google_stub, url):
        _install_youtube(google_stub, _YouTube(
            channels=_Call({"items": [{"id": "UCLEGACY"}]})))
        assert self._resolve(url) == "UCLEGACY"

    def test_an_unrecognised_url_is_none(self, google_stub):
        assert self._resolve("https://example.com/not-a-channel") is None

    def test_an_api_failure_degrades_to_none(self, google_stub):
        _install_youtube(google_stub, _YouTube(channels=_Call(_HttpError(403))))
        assert self._resolve("https://youtube.com/@x") is None

    def test_nothing_found_is_none(self, google_stub):
        _install_youtube(google_stub, _YouTube(
            channels=_Call({"items": []}), search=_Call({"items": []})))
        assert self._resolve("https://youtube.com/@ghost") is None


class TestChannelInfo:
    def test_it_maps_the_fields_the_ui_shows(self, google_stub):
        _install_youtube(google_stub, _YouTube(channels=_Call({"items": [{
            "snippet": {"title": "Chan", "thumbnails": {"medium": {"url": "t.jpg"}}},
            "statistics": {"subscriberCount": "1234", "videoCount": "56"},
        }]})))
        info = asyncio.run(CR.get_youtube_channel_info("UC1", "KEY"))
        assert info == {"title": "Chan", "thumbnail_url": "t.jpg",
                        "subscriber_count": 1234, "video_count": 56}

    def test_a_channel_hiding_its_counts_still_renders(self, google_stub):
        """Subscriber counts can be hidden; that's not an error."""
        _install_youtube(google_stub, _YouTube(channels=_Call({"items": [{
            "snippet": {"title": "Chan", "thumbnails": {}},
            "statistics": {},
        }]})))
        info = asyncio.run(CR.get_youtube_channel_info("UC1", "KEY"))
        assert info["subscriber_count"] == 0 and info["thumbnail_url"] == ""

    def test_an_unknown_channel_is_none(self, google_stub):
        _install_youtube(google_stub, _YouTube(channels=_Call({"items": []})))
        assert asyncio.run(CR.get_youtube_channel_info("UC1", "KEY")) is None

    def test_an_api_failure_is_none(self, google_stub):
        _install_youtube(google_stub, _YouTube(channels=_Call(_HttpError(500))))
        assert asyncio.run(CR.get_youtube_channel_info("UC1", "KEY")) is None


class TestChannelSearch:
    def test_results_are_mapped(self, google_stub):
        _install_youtube(google_stub, _YouTube(search=_Call({"items": [{
            "snippet": {"channelId": "UC1", "title": "A Channel",
                        "description": "about things",
                        "thumbnails": {"medium": {"url": "t.jpg"}}},
        }]})))
        out = asyncio.run(CR.search_youtube_channels("cooking", "KEY"))
        assert len(out) == 1 and out[0]["channel_id"] == "UC1"

    def test_no_results_is_an_empty_list(self, google_stub):
        _install_youtube(google_stub, _YouTube(search=_Call({"items": []})))
        assert asyncio.run(CR.search_youtube_channels("x", "KEY")) == []

    def test_an_api_failure_PROPAGATES_here_unlike_its_siblings(self, google_stub):
        """Pinning real behaviour, and flagging an inconsistency:
        `_resolve_handle`, `get_youtube_channel_info` and both fetch layers
        all catch and degrade, but `search_youtube_channels` has no try/except
        — a quota-exhausted key raises out of it instead of returning []. The
        caller (channel search in the UI) therefore has to handle an exception
        that the neighbouring calls never throw."""
        _install_youtube(google_stub, _YouTube(search=_Call(_HttpError(403))))
        with pytest.raises(_HttpError):
            asyncio.run(CR.search_youtube_channels("x", "KEY"))


# ── the fallback ladder ─────────────────────────────────────────────────────

CH = {"contentDetails": {"relatedPlaylists": {"uploads": "UUabc123"}}}


class TestPlaylistLayer:
    def test_it_returns_items_from_the_uploads_playlist(self):
        pl = _Call({"items": [{"snippet": {"title": "v1"}}], "nextPageToken": None})
        items, token = CR._fetch_via_playlist_api(
            _YouTube(playlist=pl), CH, None, 10, "UCabc123")
        assert len(items) == 1 and token is None
        assert pl.kwargs[0]["playlistId"] == "UUabc123"

    def test_it_pages_until_the_cap(self):
        pl = _Call(
            {"items": [{"i": 1}] * 50, "nextPageToken": "p2"},
            {"items": [{"i": 2}] * 50, "nextPageToken": None},
        )
        items, _ = CR._fetch_via_playlist_api(
            _YouTube(playlist=pl), CH, None, 100, "UCabc123")
        assert len(items) == 100

    def test_a_transient_5xx_is_retried(self):
        """YouTube's uploads playlist 500s intermittently for some channels;
        giving up on the first one loses a channel that would have worked."""
        pl = _Call(_HttpError(500), _HttpError(500),
                   {"items": [{"ok": 1}], "nextPageToken": None})
        items, _ = CR._fetch_via_playlist_api(
            _YouTube(playlist=pl), CH, None, 10, "UCabc123")
        assert len(items) == 1

    def test_it_tries_the_alternate_playlist_prefixes(self):
        """UU can be permanently broken for a channel while UULF works."""
        pl = _Call(_HttpError(404))
        CR._fetch_via_playlist_api(_YouTube(playlist=pl), CH, None, 10, "UCabc123")
        tried = [k["playlistId"] for k in pl.kwargs]
        assert "UUabc123" in tried
        assert any(p.startswith("UULF") for p in tried), tried

    def test_every_prefix_failing_returns_empty(self):
        pl = _Call(_HttpError(404))
        items, token = CR._fetch_via_playlist_api(
            _YouTube(playlist=pl), CH, None, 10, "UCabc123")
        assert items == [] and token is None

    def test_a_non_uu_playlist_id_is_used_as_is(self):
        ch = {"contentDetails": {"relatedPlaylists": {"uploads": "PLcustom"}}}
        pl = _Call({"items": [{"x": 1}], "nextPageToken": None})
        CR._fetch_via_playlist_api(_YouTube(playlist=pl), ch, None, 5, "UC1")
        assert [k["playlistId"] for k in pl.kwargs] == ["PLcustom"]


class TestSearchApiLayer:
    def test_it_returns_videos_with_stats_folded_in(self):
        search = _Call({"items": [{"id": {"videoId": "v1"},
                                   "snippet": {"title": "fallback"}}]})
        videos = _Call({"items": [{
            "id": "v1",
            "snippet": {"title": "Real title", "description": "d",
                        "thumbnails": {"medium": {"url": "t.jpg"}},
                        "publishedAt": "2026-01-01T00:00:00Z"},
            "statistics": {"viewCount": "500", "likeCount": "20", "commentCount": "3"},
            "contentDetails": {"duration": "PT1M5S"},
        }]})
        out = CR._fetch_via_search_api(
            _YouTube(search=search, videos=videos), "UC1", 10)
        assert len(out) == 1
        v = out[0]
        assert v["title"] == "Real title" and v["view_count"] == 500
        assert v["duration"] == "PT1M5S" and v["_has_stats"] is True

    def test_it_still_returns_videos_when_the_stats_call_fails(self):
        """Missing view counts beat an empty Channels page."""
        search = _Call({"items": [{"id": {"videoId": "v1"},
                                   "snippet": {"title": "T"}}]})
        out = CR._fetch_via_search_api(
            _YouTube(search=search, videos=_Call(_HttpError(403))), "UC1", 10)
        assert len(out) == 1 and out[0]["view_count"] == 0

    def test_no_results_is_an_empty_list(self):
        out = CR._fetch_via_search_api(_YouTube(search=_Call({"items": []})), "UC1", 5)
        assert out == []

    def test_a_search_failure_is_an_empty_list(self):
        out = CR._fetch_via_search_api(_YouTube(search=_Call(_HttpError(403))), "UC1", 5)
        assert out == []

    def test_the_page_size_is_capped_at_the_api_maximum(self):
        search = _Call({"items": []})
        CR._fetch_via_search_api(_YouTube(search=search), "UC1", 500)
        assert search.kwargs[0]["maxResults"] == 50


class TestYtdlpLayer:
    """The last rung — no API key, no quota."""

    def test_it_maps_yt_dlp_entries(self, monkeypatch):
        mod = types.ModuleType("yt_dlp")

        class YDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return {"entries": [
                    {"id": "v1", "title": "One", "view_count": 10,
                     "duration": 61, "thumbnail": "t.jpg"},
                    {"id": "v2", "title": "Two", "view_count": 20, "duration": 30},
                ]}
        mod.YoutubeDL = YDL
        monkeypatch.setitem(sys.modules, "yt_dlp", mod)
        out = CR._fetch_via_ytdlp("UC1", 10)
        assert len(out) == 2 and out[0]["video_id"] == "v1"

    def test_a_yt_dlp_failure_is_an_empty_list(self, monkeypatch):
        mod = types.ModuleType("yt_dlp")

        class YDL:
            def __init__(self, opts):
                raise RuntimeError("yt-dlp exploded")
        mod.YoutubeDL = YDL
        monkeypatch.setitem(sys.modules, "yt_dlp", mod)
        assert CR._fetch_via_ytdlp("UC1", 10) == []

    def test_no_entries_is_an_empty_list(self, monkeypatch):
        mod = types.ModuleType("yt_dlp")

        class YDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return {}
        mod.YoutubeDL = YDL
        monkeypatch.setitem(sys.modules, "yt_dlp", mod)
        assert CR._fetch_via_ytdlp("UC1", 10) == []
