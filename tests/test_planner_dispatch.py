# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The planner's action layer — turning a model's `<action>` block into work.

The planner is this project's chat brain, and its whole contract is that
whatever the model emits, the app either does something sensible or does
nothing at all. A malformed action must never crash the turn, and an action
must never fire on incomplete arguments — a `start_download` with no ids that
silently created an empty job would show the user a job that does nothing.

Three areas are covered:

  * `_parse_quick_replies` — quick replies are pure UX sugar, so every parse
    failure has to degrade to [] rather than break the reply that carries them;
  * `_infer_missing_action` — the safety net for when the model SAYS it is
    searching and forgets the action block. It must fire when intent is
    unambiguous and stay silent when it isn't, because a wrong guess starts a
    scout the user never asked for;
  * `_dispatch_action` — every action type routes to its handler, unknown
    types are inert, and incomplete payloads create nothing.

No AI calls, no network: the WS layer and the job dispatcher are stubbed.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.agents.planner import PlannerAgent, _parse_quick_replies
from backend.database import init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


@pytest.fixture()
def bus(monkeypatch):
    """Capture WS traffic and job dispatches instead of performing them."""
    sent: list[dict] = []
    jobs: list[tuple] = []

    async def fake_send(msg, user_id="local"):
        sent.append(msg)

    async def fake_progress(*a, **k):
        return None

    async def fake_warn(**k):
        sent.append({"type": "constraint_warning", **k})

    def fake_dispatch(coro, *a, **k):
        coro.close()
        jobs.append(("dispatch", coro))
        return None

    from backend.core.ws_manager import ws_manager
    monkeypatch.setattr(ws_manager, "send", fake_send)
    monkeypatch.setattr(ws_manager, "send_progress", fake_progress)
    monkeypatch.setattr(ws_manager, "send_constraint_warning", fake_warn)
    monkeypatch.setattr("backend.core.task_runner.dispatch", fake_dispatch)
    return {"sent": sent, "jobs": jobs}


def _types(bus):
    return [m.get("type") for m in bus["sent"]]


async def _job_count(job_type: str) -> int:
    from sqlalchemy import func, select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(func.count(Job.id)).where(Job.job_type == job_type))).scalar_one()


# ── quick replies ───────────────────────────────────────────────────────────

class TestQuickReplies:
    def test_a_well_formed_block_is_parsed(self):
        out = _parse_quick_replies(
            'Sure!\n<quick_replies>["Yes", "No, thanks"]</quick_replies>')
        assert out == ["Yes", "No, thanks"]

    @pytest.mark.parametrize("text", [
        "no block at all",
        "<quick_replies>not json</quick_replies>",
        '<quick_replies>{"a": 1}</quick_replies>',      # object, not a list
        "<quick_replies></quick_replies>",
        '<quick_replies>"just a string"</quick_replies>',
    ])
    def test_every_malformed_shape_degrades_to_empty(self, text):
        """Never break a good reply over its decorative chips."""
        assert _parse_quick_replies(text) == []

    def test_non_string_items_are_dropped(self):
        assert _parse_quick_replies(
            '<quick_replies>["ok", 42, null, "fine"]</quick_replies>') == ["ok", "fine"]

    def test_blank_items_are_dropped(self):
        assert _parse_quick_replies(
            '<quick_replies>["  ", "real"]</quick_replies>') == ["real"]

    def test_the_count_is_capped(self):
        out = _parse_quick_replies(
            "<quick_replies>%s</quick_replies>" % json.dumps([f"r{i}" for i in range(50)]))
        assert 0 < len(out) <= 8

    def test_each_reply_is_length_capped(self):
        out = _parse_quick_replies(
            '<quick_replies>["%s"]</quick_replies>' % ("x" * 500))
        assert len(out[0]) < 500


# ── the missing-action safety net ───────────────────────────────────────────

class TestInferMissingAction:
    def _infer(self, msg, resp=""):
        return [json.loads(a) for a in PlannerAgent._infer_missing_action(msg, resp)]

    def test_an_article_url_is_always_analyzed(self):
        out = self._infer("what do you think of https://example.com/post")
        assert out == [{"type": "analyze_url", "url": "https://example.com/post"}]

    def test_trailing_punctuation_is_stripped_from_the_url(self):
        out = self._infer("read https://example.com/post.")
        assert out[0]["url"] == "https://example.com/post"

    @pytest.mark.parametrize("url", [
        "https://youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.tiktok.com/@x/video/1",
        "https://www.douyin.com/video/1",
    ])
    def test_a_video_url_is_not_treated_as_an_article(self, url):
        """Those have their own download path; analyzing them as articles
        would scrape a player page."""
        assert self._infer(f"check {url}") == []

    def test_it_fires_when_intent_and_claim_agree(self):
        out = self._infer("find me cooking videos", "Searching for those now...")
        assert out[0]["type"] == "start_scout"
        assert out[0]["niche"] == "me cooking videos" or "cooking" in out[0]["niche"]
        assert out[0]["platforms"] == ["youtube", "tiktok"]

    def test_the_command_prefix_is_stripped_from_the_niche(self):
        out = self._infer("scout home workouts", "I'll search for that")
        assert out[0]["niche"] == "home workouts"

    def test_it_stays_silent_when_the_ai_did_not_claim_to_act(self):
        """The model may be asking a clarifying question — starting a scout
        under it would be an action the user never asked for."""
        assert self._infer("find me cooking videos",
                           "What kind of cooking are you into?") == []

    def test_it_stays_silent_without_scout_intent(self):
        assert self._infer("hello there", "Searching now...") == []

    def test_news_intent_routes_to_the_news_scout(self):
        out = self._infer("find AI news", "Searching for those now")
        assert out[0]["type"] == "start_news_scout"
        assert "news" not in out[0]["query"].lower()

    @pytest.mark.parametrize("msg", [
        "find trending news", "search latest news", "find headlines"])
    def test_a_vague_news_query_is_left_for_the_ai_to_clarify(self, msg):
        """"trending news" is not a topic — auto-scouting it wastes a job."""
        assert self._infer(msg, "Searching now...") == []

    def test_chinese_intent_is_recognised(self):
        out = self._infer("帮我找 健身视频", "正在搜索")
        assert out and out[0]["type"] == "start_scout"

    def test_a_bare_command_word_scouts_for_itself(self):
        """Documented quirk, not an assertion that it's ideal: the prefixes
        carry a trailing space ("search "), so a message that is ONLY the
        command word isn't stripped and becomes the niche. Harmless in
        practice — it needs the model to also claim it's searching — but worth
        pinning so a future prefix change is a deliberate one."""
        out = self._infer("search", "Searching now...")
        assert out and out[0]["niche"] == "search"


class TestIsValidJson:
    @pytest.mark.parametrize("s,ok", [
        ('{"a": 1}', True), ("[1,2]", True), ("null", True),
        ("{not json", False), ("", False), ("{'a': 1}", False),
    ])
    def test_it_reports_parseability(self, s, ok):
        assert PlannerAgent._is_valid_json(s) is ok


# ── action dispatch ─────────────────────────────────────────────────────────

class TestDispatch:
    def _run(self, action, user_settings=None):
        asyncio.run(PlannerAgent()._dispatch_action(action, user_settings, "local"))

    def test_an_unknown_action_type_is_inert(self, bus):
        """A model that invents an action must not crash the turn."""
        self._run({"type": "teleport_video"})
        assert bus["sent"] == [] and bus["jobs"] == []

    def test_an_action_with_no_type_is_inert(self, bus):
        self._run({})
        assert bus["sent"] == [] and bus["jobs"] == []

    def test_start_download_creates_a_job_and_announces_it(self, bus):
        before = asyncio.run(_job_count("download"))
        self._run({"type": "start_download", "scout_result_ids": ["a", "b"]})
        assert asyncio.run(_job_count("download")) == before + 1
        started = [m for m in bus["sent"] if m.get("type") == "job_started"]
        assert started and "2 videos" in started[0]["message"]

    def test_start_download_with_no_ids_creates_nothing(self, bus):
        """An empty job would show the user a job that does nothing."""
        before = asyncio.run(_job_count("download"))
        self._run({"type": "start_download", "scout_result_ids": []})
        assert asyncio.run(_job_count("download")) == before
        assert bus["sent"] == []

    def test_start_generate_creates_a_job(self, bus):
        before = asyncio.run(_job_count("generate"))
        self._run({"type": "start_generate", "downloaded_video_id": "dv1"})
        assert asyncio.run(_job_count("generate")) == before + 1

    def test_start_generate_without_a_video_creates_nothing(self, bus):
        before = asyncio.run(_job_count("generate"))
        self._run({"type": "start_generate"})
        assert asyncio.run(_job_count("generate")) == before

    def test_start_upload_creates_a_job(self, bus):
        """The uploader is this project's own feature — its planner action
        has to keep working."""
        before = asyncio.run(_job_count("upload"))
        self._run({"type": "start_upload", "generated_video_id": "gv1",
                   "platforms": ["youtube", "tiktok"]})
        assert asyncio.run(_job_count("upload")) == before + 1

    def test_start_upload_without_a_video_creates_nothing(self, bus):
        before = asyncio.run(_job_count("upload"))
        self._run({"type": "start_upload"})
        assert asyncio.run(_job_count("upload")) == before

    def test_a_known_wizard_is_launched(self, bus):
        from backend.core.setup_wizard import WIZARDS
        wid = next(iter(WIZARDS))
        self._run({"type": "start_wizard", "wizard_id": wid})
        assert "wizard_start" in _types(bus)

    def test_an_unknown_wizard_is_ignored(self, bus):
        self._run({"type": "start_wizard", "wizard_id": "not_a_wizard"})
        assert bus["sent"] == []

    def test_a_news_scout_with_no_query_creates_nothing(self, bus):
        before = asyncio.run(_job_count("news_scout"))
        self._run({"type": "start_news_scout", "query": ""})
        assert asyncio.run(_job_count("news_scout")) == before

    def test_a_news_scout_with_a_query_creates_a_job(self, bus):
        before = asyncio.run(_job_count("news_scout"))
        self._run({"type": "start_news_scout", "query": "AI regulation"})
        assert asyncio.run(_job_count("news_scout")) == before + 1

    def test_show_videos_emits_a_list(self, bus):
        self._run({"type": "show_videos"})
        assert bus["sent"], "the user asked to see something — say something"

    def test_show_downloaded_emits_a_list(self, bus):
        self._run({"type": "show_downloaded"})
        assert bus["sent"]

    def test_show_scout_results_is_survivable_with_no_results(self, bus):
        self._run({"type": "show_scout_results"})
        assert isinstance(bus["sent"], list)

    def test_a_content_calendar_with_no_data_explains_itself(self, bus, monkeypatch):
        async def empty(*a, **k):
            return None
        monkeypatch.setattr(
            "backend.core.user_intelligence.UserIntelligence.generate_content_calendar",
            empty)
        self._run({"type": "content_calendar", "days": 7})
        text = json.dumps(bus["sent"])
        assert "more data" in text or "assistant_message" in text

    def test_a_content_calendar_with_data_is_sent(self, bus, monkeypatch):
        async def full(*a, **k):
            return [{"day": 1, "idea": "post a short"}]
        monkeypatch.setattr(
            "backend.core.user_intelligence.UserIntelligence.generate_content_calendar",
            full)
        self._run({"type": "content_calendar", "days": 7})
        assert "content_calendar" in _types(bus)

    def test_analyze_url_with_no_url_creates_nothing(self, bus):
        self._run({"type": "analyze_url", "url": ""})
        assert bus["jobs"] == []

    def test_download_url_with_no_url_creates_nothing(self, bus):
        before = asyncio.run(_job_count("download_url"))
        self._run({"type": "download_url", "url": ""})
        assert asyncio.run(_job_count("download_url")) == before

    def test_analyze_channel_with_no_url_creates_nothing(self, bus):
        self._run({"type": "analyze_channel", "url": ""})
        assert bus["jobs"] == []

    def test_save_news_with_nothing_to_save_is_inert(self, bus):
        self._run({"type": "save_news_to_library", "articles": []})
        assert bus["jobs"] == []
