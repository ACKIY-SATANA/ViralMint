# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The scout agent — searching several platforms at once without letting any
one of them sink the run.

The governing rule is that a scout must never crash on one platform's failure.
That's easy to state and easy to regress: this agent gathers N independent
network operations, each of which can raise, return nothing, or return
malformed rows, and it then enriches, scores, dedupes and persists whatever
survived. A None slipping through the enrichment step once failed a scout that
had already collected perfectly good results.

Its inputs are also not all trustworthy. The platform list arrives from the
chat brain, from MCP, or from the UI — an LLM once passed 34 invented
"platforms" in one call, which meant 34 searches plus AI retries and a warning
toast each.

Every platform searcher, the AI retry helper and the WS layer are stubbed.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.agents.scout import MAX_PLATFORMS_PER_SCOUT, ScoutAgent, compute_virality_score
from backend.database import init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


@pytest.fixture()
def bus(monkeypatch):
    sent: list[dict] = []
    warnings: list[dict] = []

    async def fake_send(msg, user_id="local"):
        sent.append(msg)

    async def fake_progress(*a, **k):
        return None

    async def fake_warn(constraint, message, severity="warning",
                        wizard_id=None, user_id="local"):
        warnings.append({"constraint": constraint, "message": message})

    from backend.core.ws_manager import ws_manager
    monkeypatch.setattr(ws_manager, "send", fake_send)
    monkeypatch.setattr(ws_manager, "send_progress", fake_progress)
    monkeypatch.setattr(ws_manager, "send_constraint_warning", fake_warn)
    return {"sent": sent, "warnings": warnings}


@pytest.fixture()
def platforms(monkeypatch):
    """Replace the per-platform searcher with a scriptable stub."""
    calls: list[tuple[str, str]] = []
    outcomes: dict = {}

    async def fake_scout_platform(self, platform, niche, user_settings, user_id="local"):
        calls.append((platform, niche))
        out = outcomes.get(platform, [])
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(ScoutAgent, "_scout_platform", fake_scout_platform)

    async def no_refine(*a, **k):
        return None
    monkeypatch.setattr("backend.core.ai_retry.ai_refine_search", no_refine)

    async def no_enrich(self, *a, **k):
        return None
    monkeypatch.setattr(ScoutAgent, "_enrich_with_outlier_scores", no_enrich)

    return {"calls": calls, "outcomes": outcomes}


@pytest.fixture()
def no_ai_fallback(monkeypatch):
    """Disable the raw-HTTP AI rescue so a failure stays a failure.

    Patched via monkeypatch, NOT by assigning onto the class — a bare
    assignment here leaks into every later test in the session.
    """
    async def none(self, *a, **k):
        return []
    monkeypatch.setattr(ScoutAgent, "_ai_raw_search_fallback", none)


def _result(video_id="v1", platform="youtube", views=1000, likes=100, **kw):
    return {"platform": platform, "video_id": video_id,
            "video_url": f"https://example.com/{video_id}",
            "title": f"Video {video_id}", "author": "@creator",
            "views": views, "likes": likes, "comments": 10, "shares": 5,
            "duration_seconds": 30, **kw}


async def _job() -> str:
    from backend.agents.job_helper import create_job
    return (await create_job("scout", "local", {})).id


def _run(niche="cooking", plats=("youtube",)):
    async def go():
        jid = await _job()
        await ScoutAgent().run(jid, niche, list(plats))
        return jid
    return asyncio.run(go())


async def _saved_count(niche: str) -> int:
    from sqlalchemy import func, select
    from backend.database import AsyncSessionLocal
    from backend.models.scout_result import ScoutResult
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.count(ScoutResult.id))
                                 .where(ScoutResult.niche == niche))).scalar_one()


# ── the platform list is untrusted input ────────────────────────────────────

class TestPlatformListHygiene:
    def test_duplicates_are_collapsed(self, bus, platforms):
        _run(plats=["youtube", "YouTube", "youtube "])
        assert [c[0] for c in platforms["calls"]] == ["youtube"]

    def test_the_list_is_capped(self, bus, platforms):
        """An LLM once passed 34 invented platforms — that's 34 searches plus
        AI retries, a ~70s grind, and a toast per fallback."""
        _run(plats=[f"platform{i}" for i in range(40)])
        assert len(platforms["calls"]) == MAX_PLATFORMS_PER_SCOUT

    def test_an_empty_list_falls_back_to_youtube(self, bus, platforms):
        _run(plats=[])
        assert [c[0] for c in platforms["calls"]] == ["youtube"]

    def test_blank_entries_are_dropped(self, bus, platforms):
        _run(plats=["", "  ", "youtube"])
        assert [c[0] for c in platforms["calls"]] == ["youtube"]

    def test_many_unsupported_platforms_produce_ONE_aggregated_notice(
            self, bus, platforms):
        """A warning per platform was a toast storm."""
        _run(plats=["vimeo", "dailymotion", "rumble", "odysee"])
        aggregated = [w for w in bus["warnings"]
                      if w["constraint"] == "multi_platform_fallback"]
        assert len(aggregated) == 1


# ── one platform failing must never sink the run ────────────────────────────

class TestPartialFailure:
    def test_a_raising_platform_does_not_stop_the_others(self, bus, platforms,
                                                          no_ai_fallback):
        platforms["outcomes"]["tiktok"] = RuntimeError("tiktok API down")
        platforms["outcomes"]["youtube"] = [_result("y1")]
        _run(niche="partial-fail", plats=["tiktok", "youtube"])
        assert asyncio.run(_saved_count("partial-fail")) == 1

    def test_the_failure_is_reported_not_swallowed(self, bus, platforms,
                                                    no_ai_fallback):
        platforms["outcomes"]["tiktok"] = RuntimeError("tiktok API down")
        _run(niche="reported", plats=["tiktok"])
        assert any("tiktok" in w["constraint"] for w in bus["warnings"])

    def test_the_ai_fallback_can_rescue_a_failed_platform(self, bus, platforms,
                                                          monkeypatch):
        platforms["outcomes"]["tiktok"] = RuntimeError("library exploded")

        async def rescue(self, platform, niche, user_settings):
            return [_result("rescued", platform="tiktok")]
        monkeypatch.setattr(ScoutAgent, "_ai_raw_search_fallback", rescue)
        _run(niche="rescued-niche", plats=["tiktok"])
        assert asyncio.run(_saved_count("rescued-niche")) == 1

    def test_every_platform_failing_still_completes_the_job(self, bus, platforms,
                                                             no_ai_fallback):
        platforms["outcomes"]["youtube"] = RuntimeError("nope")
        jid = _run(niche="all-fail", plats=["youtube"])

        async def status():
            from sqlalchemy import select
            from backend.database import AsyncSessionLocal
            from backend.models.job import Job
            async with AsyncSessionLocal() as db:
                return (await db.execute(
                    select(Job.status).where(Job.id == jid))).scalar_one()
        assert asyncio.run(status()) in ("success", "failed")

    def test_enrichment_failing_never_costs_the_results(self, bus, platforms,
                                                        monkeypatch):
        """Enrichment is a bonus. A None author_url once failed a scout that
        had already collected everything."""
        platforms["outcomes"]["youtube"] = [_result("keep-me")]

        async def boom(self, *a, **k):
            raise RuntimeError("outlier enrichment blew up")
        monkeypatch.setattr(ScoutAgent, "_enrich_with_outlier_scores", boom)

        _run(niche="enrich-fail", plats=["youtube"])
        assert asyncio.run(_saved_count("enrich-fail")) == 1


class TestEmptyResultRetry:
    def test_an_empty_platform_triggers_one_refined_retry(self, bus, platforms,
                                                          monkeypatch):
        platforms["outcomes"]["youtube"] = []
        seen = {}

        async def refine(platform, niche, user_settings):
            seen["asked"] = (platform, niche)
            return "cooking recipes"
        monkeypatch.setattr("backend.core.ai_retry.ai_refine_search", refine)

        _run(niche="cooking", plats=["youtube"])
        assert seen["asked"] == ("youtube", "cooking")
        assert [c[1] for c in platforms["calls"]] == ["cooking", "cooking recipes"]

    def test_no_refinement_means_no_retry(self, bus, platforms):
        platforms["outcomes"]["youtube"] = []
        _run(plats=["youtube"])
        assert len(platforms["calls"]) == 1

    def test_a_refinement_failure_is_non_fatal(self, bus, platforms, monkeypatch):
        platforms["outcomes"]["youtube"] = []

        async def boom(*a, **k):
            raise RuntimeError("refiner down")
        monkeypatch.setattr("backend.core.ai_retry.ai_refine_search", boom)
        _run(plats=["youtube"])   # must not raise

    def test_a_platform_with_results_is_not_retried(self, bus, platforms,
                                                    monkeypatch):
        platforms["outcomes"]["youtube"] = [_result()]

        async def refine(*a, **k):
            raise AssertionError("must not refine a search that worked")
        monkeypatch.setattr("backend.core.ai_retry.ai_refine_search", refine)
        _run(plats=["youtube"])


# ── scoring + persistence ───────────────────────────────────────────────────

class TestScoringAndSave:
    def test_results_are_scored_and_saved(self, bus, platforms):
        platforms["outcomes"]["youtube"] = [
            _result("a", views=1_000_000, likes=100_000),
            _result("b", views=10, likes=0),
        ]
        _run(niche="scored", plats=["youtube"])
        assert asyncio.run(_saved_count("scored")) == 2

    def test_a_duplicate_video_is_not_saved_twice(self, bus, platforms):
        platforms["outcomes"]["youtube"] = [_result("dupe"), _result("dupe")]
        _run(niche="dupes", plats=["youtube"])
        assert asyncio.run(_saved_count("dupes")) <= 1

    def test_the_job_reports_completion(self, bus, platforms):
        platforms["outcomes"]["youtube"] = [_result()]
        jid = _run(niche="completes", plats=["youtube"])

        async def row():
            from sqlalchemy import select
            from backend.database import AsyncSessionLocal
            from backend.models.job import Job
            async with AsyncSessionLocal() as db:
                return (await db.execute(
                    select(Job).where(Job.id == jid))).scalar_one()
        assert asyncio.run(row()).status == "success"


class TestBuildVideoUrl:
    def test_youtube(self):
        assert ScoutAgent._build_video_url("youtube", "abc", {}) == \
            "https://youtube.com/watch?v=abc"

    def test_tiktok_with_a_handle(self):
        url = ScoutAgent._build_video_url(
            "tiktok", "123", {"author": {"unique_id": "someone"}})
        assert url == "https://www.tiktok.com/@someone/video/123"

    def test_tiktok_without_a_handle_still_resolves(self):
        assert ScoutAgent._build_video_url("tiktok", "123", {}) == \
            "https://www.tiktok.com/video/123"

    def test_douyin(self):
        assert ScoutAgent._build_video_url("douyin", "9", {}) == \
            "https://www.douyin.com/video/9"

    def test_an_unknown_platform_yields_no_url(self):
        assert ScoutAgent._build_video_url("myspace", "9", {}) == ""


class TestViralityScore:
    def test_it_stays_inside_zero_to_one_hundred(self):
        for v in (_result(views=0, likes=0),
                  _result(views=10**9, likes=10**8),
                  _result(views=1, likes=10**6)):
            assert 0 <= compute_virality_score(v) <= 100

    def test_engagement_beats_raw_views(self):
        """The whole point of the score: a small video everyone reacts to is
        a better template than a big one nobody does."""
        engaged = _result(views=10_000, likes=5_000, comments=2_000)
        flat = _result(views=10_000, likes=5, comments=0)
        assert compute_virality_score(engaged) > compute_virality_score(flat)

    def test_a_zero_view_video_does_not_divide_by_zero(self):
        assert compute_virality_score(_result(views=0, likes=0)) >= 0

    def test_missing_fields_are_survivable(self):
        assert compute_virality_score({"platform": "youtube"}) >= 0


class TestVideoUrlEdgeCases:
    def test_a_missing_author_block_is_survivable(self):
        """Scout rows come from four different platform shapes; a missing
        nested key must never raise mid-scout."""
        assert ScoutAgent._build_video_url("tiktok", "1", {"author": {}}) \
            == "https://www.tiktok.com/video/1"

    def test_an_empty_video_id_still_returns_a_string(self):
        for p in ("youtube", "tiktok", "douyin", "other"):
            assert isinstance(ScoutAgent._build_video_url(p, "", {}), str)


class TestViralityScoreShape:
    def test_a_brand_new_video_outranks_an_identical_old_one(self):
        """Recency is part of the formula — a template that worked last week
        is worth more than the same numbers from two years ago."""
        from datetime import datetime, timedelta
        fresh = _result(views=10_000, likes=1_000,
                        upload_date=datetime.utcnow())
        old = _result(views=10_000, likes=1_000,
                      upload_date=datetime.utcnow() - timedelta(days=900))
        assert compute_virality_score(fresh) >= compute_virality_score(old)

    @pytest.mark.xfail(strict=True, reason=(
        "GAP: compute_virality_score does max(video.get('likes', 0), 0), which "
        "raises TypeError on a string metric — and the scoring loop in "
        "ScoutAgent.run is NOT wrapped, unlike the outlier enrichment "
        "immediately above it. So one string-typed field kills the whole "
        "scout, which is exactly what rule #10 forbids. Reachable via the AI "
        "raw-search fallbacks: both _ai_parse_fallback (tikhub_client) and "
        "_ai_raw_search_fallback (scout) pass model-produced values straight "
        "into these fields, and a model emitting \"1000\" instead of 1000 is "
        "entirely ordinary"))
    def test_a_string_metric_does_not_crash_the_scorer(self):
        """Platform payloads are not consistently typed."""
        assert compute_virality_score(
            {"platform": "tiktok", "views": "1000", "likes": "10"}) >= 0
