# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""TikHub client — the paid TikTok/Douyin search fallback.

TikHub's app-v3 endpoint returns videos under one of FIVE different keys
depending on which upstream endpoint served the request, and the shapes drift
without notice. That is the whole reason this module exists as a pile of
format branches plus an AI fallback, and it's why every branch is pinned here:
a parser that silently returns `[]` looks exactly like "no results for that
niche", so a format change would degrade the scout invisibly.

No network: `httpx.AsyncClient` is stubbed, so these run offline and fast.
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from backend.services import tikhub_client as TH


# ── fixtures ────────────────────────────────────────────────────────────────

def _aweme(video_id="7300000000000000001", desc="a viral clip",
           unique_id="creator", uid="u123", duration=15,
           plays=1000, likes=100, comments=10, shares=5,
           create_time=1735689600, share_url=None, cover=True):
    a = {
        "aweme_id": video_id,
        "desc": desc,
        "author": {"unique_id": unique_id, "nickname": "Creator Name", "uid": uid},
        "statistics": {"play_count": plays, "digg_count": likes,
                       "comment_count": comments, "share_count": shares},
        "video": {"duration": duration},
        "create_time": create_time,
    }
    if cover:
        a["video"]["cover"] = {"url_list": ["https://cdn.example/thumb.jpg"]}
    if share_url:
        a["share_url"] = share_url
    return a


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text or "{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


class _Client:
    """Stand-in for httpx.AsyncClient used as an async context manager."""

    def __init__(self, resp=None, raises=None):
        self._resp = resp
        self._raises = raises
        self.calls: list[dict] = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self._raises:
            raise self._raises
        return self._resp


@pytest.fixture()
def patch_httpx(monkeypatch):
    def install(resp=None, raises=None):
        c = _Client(resp, raises)
        monkeypatch.setattr(TH.httpx, "AsyncClient", c)
        return c
    return install


# ── the five response formats ───────────────────────────────────────────────

class TestResponseFormats:
    def test_aweme_list(self):
        out = TH._parse_tiktok_app_v3_as({"data": {"aweme_list": [_aweme()]}})
        assert len(out) == 1 and out[0]["video_id"] == "7300000000000000001"

    def test_search_item_list_with_nested_aweme_info(self):
        data = {"data": {"search_item_list": [{"aweme_info": _aweme()}]}}
        assert len(TH._parse_tiktok_app_v3_as(data)) == 1

    def test_search_item_list_falling_back_to_a_mix(self):
        """Some search rows carry a mix instead of a single aweme."""
        data = {"data": {"search_item_list": [
            {"aweme_mix_info": {"mix_items": [_aweme(video_id="777")]}},
        ]}}
        out = TH._parse_tiktok_app_v3_as(data)
        assert len(out) == 1 and out[0]["video_id"] == "777"

    def test_business_data(self):
        data = {"data": {"business_data": [{"data": {"aweme_info": _aweme()}}]}}
        assert len(TH._parse_tiktok_app_v3_as(data)) == 1

    def test_business_data_row_without_an_aweme_is_skipped(self):
        data = {"data": {"business_data": [{"data": {}}, {"data": {"aweme_info": _aweme()}}]}}
        assert len(TH._parse_tiktok_app_v3_as(data)) == 1

    def test_aweme_detail(self):
        data = {"data": {"aweme_detail": [_aweme()]}}
        assert len(TH._parse_tiktok_app_v3_as(data)) == 1

    def test_an_unrecognized_shape_yields_nothing_rather_than_raising(self):
        assert TH._parse_tiktok_app_v3_as({"data": {"mystery_key": [{"x": 1}]}}) == []

    def test_a_non_dict_data_block_is_survivable(self):
        """TikHub has returned a bare list here before."""
        assert TH._parse_tiktok_app_v3_as({"data": []}) == []
        assert TH._parse_tiktok_app_v3_as({}) == []

    def test_entries_without_an_id_are_dropped_not_emitted_blank(self):
        data = {"data": {"aweme_list": [{"desc": "no id here"}, _aweme()]}}
        assert len(TH._parse_tiktok_app_v3_as(data)) == 1


# ── field mapping ───────────────────────────────────────────────────────────

class TestAwemeMapping:
    def test_tiktok_urls_are_built_from_the_handle(self):
        r = TH._aweme_to_result(_aweme(unique_id="someone"), "tiktok")
        assert r["video_url"] == "https://www.tiktok.com/@someone/video/7300000000000000001"
        assert r["author_url"] == "https://www.tiktok.com/@someone"
        assert r["author"] == "@someone"

    def test_tiktok_without_a_handle_still_produces_a_usable_url(self):
        r = TH._aweme_to_result(_aweme(unique_id="", uid=""), "tiktok")
        assert r["video_url"].endswith("/video/7300000000000000001")
        assert r["author_url"] == ""
        assert r["author"] == "@Creator Name", "falls back to the nickname"

    def test_douyin_prefers_the_share_url_and_the_uid_profile(self):
        r = TH._aweme_to_result(
            _aweme(share_url="https://v.douyin.com/abc/"), "douyin")
        assert r["video_url"] == "https://v.douyin.com/abc/"
        assert r["author_url"] == "https://www.douyin.com/user/u123"

    def test_douyin_without_a_share_url_falls_back(self):
        r = TH._aweme_to_result(_aweme(), "douyin")
        assert r["video_url"] == "https://www.douyin.com/video/7300000000000000001"

    def test_millisecond_durations_are_normalized_to_seconds(self):
        """Some endpoints report ms. 15000 must not become a 4-hour video."""
        assert TH._aweme_to_result(_aweme(duration=15000), "tiktok")["duration_seconds"] == 15

    def test_second_durations_are_left_alone(self):
        assert TH._aweme_to_result(_aweme(duration=42), "tiktok")["duration_seconds"] == 42

    def test_a_string_cover_is_accepted(self):
        a = _aweme(cover=False)
        a["video"]["cover"] = "https://cdn.example/direct.jpg"
        assert TH._aweme_to_result(a, "tiktok")["thumbnail_url"] == "https://cdn.example/direct.jpg"

    def test_a_missing_or_odd_cover_degrades_to_empty(self):
        a = _aweme(cover=False)
        assert TH._aweme_to_result(a, "tiktok")["thumbnail_url"] == ""
        a["video"]["cover"] = 12345
        assert TH._aweme_to_result(a, "tiktok")["thumbnail_url"] == ""

    def test_camelcase_statistics_are_read_too(self):
        a = _aweme()
        a["statistics"] = {"playCount": 9, "diggCount": 8,
                           "commentCount": 7, "shareCount": 6}
        r = TH._aweme_to_result(a, "tiktok")
        assert (r["views"], r["likes"], r["comments"], r["shares"]) == (9, 8, 7, 6)

    def test_the_title_is_capped_but_the_description_is_not(self):
        a = _aweme(desc="x" * 500)
        r = TH._aweme_to_result(a, "tiktok")
        assert len(r["title"]) == 200 and len(r["description"]) == 500

    def test_missing_everything_optional_still_yields_a_row(self):
        r = TH._aweme_to_result({"aweme_id": "1"}, "tiktok")
        assert r["video_id"] == "1" and r["views"] == 0 and r["upload_date"] is None


class TestTimestamps:
    def test_a_unix_timestamp_becomes_a_datetime(self):
        assert TH._ts_to_datetime(1735689600) == datetime(2025, 1, 1, 0, 0)

    def test_a_string_timestamp_is_accepted(self):
        assert isinstance(TH._ts_to_datetime("1735689600"), datetime)

    @pytest.mark.parametrize("bad", [None, 0, "", "not-a-number", {}, []])
    def test_junk_never_raises(self, bad):
        assert TH._ts_to_datetime(bad) is None


# ── the network layer ───────────────────────────────────────────────────────

class TestSearch:
    async def test_no_api_key_short_circuits_without_calling_out(self, patch_httpx):
        c = patch_httpx(_Resp({"data": {"aweme_list": [_aweme()]}}))
        assert await TH.search_tiktok("cats", "") == []
        assert await TH.search_douyin("cats", "") == []
        assert c.calls == [], "must not spend a request without a key"

    async def test_tiktok_hits_the_tiktok_endpoint_with_bearer_auth(self, patch_httpx):
        c = patch_httpx(_Resp({"data": {"aweme_list": [_aweme()]}}))
        out = await TH.search_tiktok("cats", "KEY", max_results=10)
        assert len(out) == 1 and out[0]["platform"] == "tiktok"
        call = c.calls[0]
        assert "/tiktok/" in call["url"]
        assert call["params"] == {"keyword": "cats", "count": 10}
        assert call["headers"]["Authorization"] == "Bearer KEY"

    async def test_douyin_hits_the_douyin_endpoint(self, patch_httpx):
        c = patch_httpx(_Resp({"data": {"aweme_list": [_aweme()]}}))
        out = await TH.search_douyin("猫", "KEY")
        assert len(out) == 1 and out[0]["platform"] == "douyin"
        assert "/douyin/" in c.calls[0]["url"]

    async def test_the_count_is_capped_at_thirty(self, patch_httpx):
        """TikHub bills per result; the endpoint caps at 30 anyway."""
        c = patch_httpx(_Resp({"data": {"aweme_list": []}}))
        await TH.search_tiktok("cats", "KEY", max_results=500)
        assert c.calls[0]["params"]["count"] == 30

    @pytest.mark.parametrize("search", ["search_tiktok", "search_douyin"])
    async def test_an_http_error_returns_empty_instead_of_crashing_the_scout(
            self, patch_httpx, search):
        """Rule: one platform failing must never kill the whole scout."""
        patch_httpx(_Resp({}, status=402, text="payment required"))
        assert await getattr(TH, search)("cats", "KEY") == []

    @pytest.mark.parametrize("search", ["search_tiktok", "search_douyin"])
    async def test_a_transport_error_returns_empty(self, patch_httpx, search):
        patch_httpx(raises=httpx.ConnectError("no route to host"))
        assert await getattr(TH, search)("cats", "KEY") == []

    async def test_malformed_json_is_survivable(self, patch_httpx):
        class Bad(_Resp):
            def json(self):
                raise ValueError("not json")
        patch_httpx(Bad({}))
        assert await TH.search_tiktok("cats", "KEY") == []


# ── the AI fallback (what saves us from a silent format change) ─────────────

class TestAiFallback:
    async def test_it_runs_when_parsing_finds_nothing_but_data_exists(
            self, patch_httpx, monkeypatch):
        called = {}

        async def fake_ai(raw, platform, keyword):
            called["args"] = (platform, keyword)
            return [_aweme(video_id="999")]

        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", fake_ai)
        patch_httpx(_Resp({"data": {"brand_new_key": [{"whatever": 1}]}}))
        out = await TH.search_tiktok("cats", "KEY")
        assert called["args"] == ("tiktok", "cats")
        assert len(out) == 1 and out[0]["video_id"] == "999"

    async def test_it_does_not_run_when_the_parser_succeeded(
            self, patch_httpx, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("AI must not be called when parsing worked")
        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", boom)
        patch_httpx(_Resp({"data": {"aweme_list": [_aweme()]}}))
        assert len(await TH.search_tiktok("cats", "KEY")) == 1

    async def test_no_lists_in_the_payload_means_no_ai_call(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("nothing worth parsing — don't spend an AI call")
        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", boom)
        assert await TH._ai_parse_fallback({"data": {"total": 0}}, "tiktok", "cats") == []

    async def test_a_non_dict_data_block_is_refused(self):
        assert await TH._ai_parse_fallback({"data": "nope"}, "tiktok", "cats") == []

    async def test_an_ai_failure_is_non_critical(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("model down")
        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", boom)
        assert await TH._ai_parse_fallback(
            {"data": {"k": [1]}}, "tiktok", "cats") == []

    async def test_ai_output_that_maps_to_nothing_yields_nothing(self, monkeypatch):
        async def fake(*a, **k):
            return [{"no_aweme_id": True}]
        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", fake)
        assert await TH._ai_parse_fallback({"data": {"k": [1]}}, "tiktok", "cats") == []

    async def test_ai_returning_nothing_yields_nothing(self, monkeypatch):
        async def fake(*a, **k):
            return []
        monkeypatch.setattr("backend.core.ai_retry.ai_parse_api_response", fake)
        assert await TH._ai_parse_fallback({"data": {"k": [1]}}, "tiktok", "cats") == []
