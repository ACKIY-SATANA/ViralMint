# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""AI clip selection — deciding the duration window, and sanitising what the
model hands back.

Two halves, both consequential:

**The duration range.** The user's typed bounds are non-negotiable. A regression
here once meant a request for 10-20s clips could ship a 53s clip, because the
retry cascade hardcoded its own bounds. Single-bound input is the subtle case:
typing only a minimum used to be ignored entirely unless a maximum was given
too.

**Window validation.** Everything the model returns is untrusted: times can be
strings, negative, past the end of the video, inverted after clamping, or
missing entirely; scores can be out of range or absent; hook types can be
free-form strings the UI has no colour for. None of it may reach the ffmpeg
fan-out or the database unchecked — and none of it may raise, because a raise
here fails the whole extraction.

The AI client is stubbed; nothing here calls out.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import clip_extractor as CE

SEGMENTS = [{"start": i * 10.0, "end": i * 10.0 + 9.0,
             "text": f"This is segment {i} of the transcript."}
            for i in range(30)]


class _AI:
    """Stands in for the BYOK AI client."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.token_limits: list[int] = []

    async def chat(self, messages, max_tokens=None, **kw):
        self.prompts.append(messages[0]["content"])
        self.token_limits.append(max_tokens)
        r = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def ai(monkeypatch):
    holder = {}

    def install(*responses):
        client = _AI(*responses)
        holder["client"] = client
        monkeypatch.setattr("backend.core.ai_provider.get_ai_client",
                            lambda *a, **k: client)
        return client
    return install


def _select(**kw):
    defaults = dict(segments=SEGMENTS, title="A Podcast", duration=300,
                    max_clips=5, user_settings=None)
    defaults.update(kw)
    return asyncio.run(CE._select_clip_windows(**defaults))


def _clips(*windows):
    return json.dumps(list(windows))


# ── the duration window handed to the model ─────────────────────────────────

class TestDurationRange:
    def _range_from_prompt(self, client):
        """The prompt carries the min/max the model is told to respect."""
        import re
        p = client.prompts[0]
        m = re.search(r"(\d+)[–\-—](\d+) seconds", p)
        if m:
            return int(m.group(1)), int(m.group(2))
        raise AssertionError(f"no duration range in prompt: {p[:400]}")

    def test_both_bounds_are_used_verbatim(self, ai):
        c = ai(_clips({"start": 0, "end": 15}))
        _select(min_duration=10, max_duration=20)
        assert self._range_from_prompt(c) == (10, 20)

    def test_a_minimum_alone_is_honoured(self, ai):
        """This used to be ignored unless a maximum came with it."""
        c = ai(_clips({"start": 0, "end": 45}))
        _select(min_duration=40, duration=600)
        lo, hi = self._range_from_prompt(c)
        assert lo == 40 and hi > 40

    def test_a_maximum_alone_derives_a_sensible_minimum(self, ai):
        c = ai(_clips({"start": 0, "end": 20}))
        _select(max_duration=30)
        lo, hi = self._range_from_prompt(c)
        assert hi == 30 and 10 <= lo <= 15

    def test_a_short_source_narrows_the_window(self, ai):
        c = ai(_clips({"start": 0, "end": 20}))
        _select(duration=90)
        lo, hi = self._range_from_prompt(c)
        assert hi <= 90

    def test_a_long_source_uses_the_default_window(self, ai):
        c = ai(_clips({"start": 0, "end": 45}))
        _select(duration=3600)
        assert self._range_from_prompt(c) == (30, 60)

    def test_a_minimum_on_a_short_source_still_leaves_headroom(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(min_duration=25, duration=100)
        lo, hi = self._range_from_prompt(c)
        assert hi >= lo + 5, "the model needs room to move"


# ── the optional prompt biases ──────────────────────────────────────────────

class TestPromptBiases:
    def test_a_user_query_is_injected_and_ranked_above_virality(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(user_query="only the funny bits")
        assert "only the funny bits" in c.prompts[0]
        assert "USER REQUEST" in c.prompts[0]

    def test_a_blank_query_adds_nothing(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(user_query="   ")
        assert "USER REQUEST" not in c.prompts[0]

    def test_a_very_long_query_is_capped(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(user_query="x" * 2000)
        assert "x" * 600 not in c.prompts[0], "the prompt budget must stay predictable"

    def test_an_unknown_platform_adds_no_bias(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(target_platform="myspace")
        assert isinstance(c.prompts[0], str)

    def test_the_token_limit_scales_with_the_clip_count(self, ai):
        c = ai(_clips({"start": 0, "end": 30}))
        _select(max_clips=40)
        assert c.token_limits[0] > 2000
        c2 = ai(_clips({"start": 0, "end": 30}))
        _select(max_clips=1)
        assert c2.token_limits[0] >= 2000


# ── everything the model returns is untrusted ───────────────────────────────

class TestWindowValidation:
    def test_a_well_formed_clip_survives(self, ai):
        ai(_clips({"start": 10, "end": 40, "virality_score": 8,
                   "hook_type": "curiosity_gap", "title": "T"}))
        out = _select()
        assert len(out) == 1 and out[0]["start"] == 10 and out[0]["end"] == 40

    def test_string_times_are_coerced(self, ai):
        ai(_clips({"start": "10.5", "end": "40.2"}))
        out = _select()
        assert out[0]["start"] == 10.5

    def test_a_clip_with_unparseable_times_is_dropped_not_raised(self, ai):
        ai(_clips({"start": "banana", "end": 40}, {"start": 0, "end": 30}))
        out = _select()
        assert len(out) == 1

    def test_a_negative_start_is_clamped_to_zero(self, ai):
        ai(_clips({"start": -20, "end": 40}))
        assert _select()[0]["start"] == 0

    def test_an_end_past_the_video_is_clamped(self, ai):
        """Clamped to the source length first; the oversize trim then applies
        on top, so the result is bounded by BOTH."""
        ai(_clips({"start": 10, "end": 9999}))
        out = _select(duration=300)[0]
        assert out["end"] <= 300
        assert out["end"] - out["start"] <= 90

    def test_an_inverted_clip_is_dropped(self, ai):
        ai(_clips({"start": 100, "end": 50}))
        assert _select() == []

    def test_a_sub_five_second_clip_is_dropped(self, ai):
        """Below this it isn't a clip, whatever the model called it."""
        ai(_clips({"start": 10, "end": 13}))
        assert _select() == []

    def test_a_wildly_oversized_clip_is_trimmed_not_discarded(self, ai):
        """The model found something; keep the front of it rather than
        throwing the whole find away."""
        ai(_clips({"start": 0, "end": 280}))
        out = _select(duration=300, max_duration=60)
        assert len(out) == 1 and out[0]["end"] - out[0]["start"] <= 61

    def test_a_slightly_short_clip_is_accepted(self, ai):
        """Between 5s and the requested minimum is a judgement call the model
        is allowed to make."""
        ai(_clips({"start": 0, "end": 12}))
        assert len(_select(min_duration=20, max_duration=40)) == 1

    def test_scores_are_clamped_into_range(self, ai):
        ai(_clips({"start": 0, "end": 30, "virality_score": 99, "hook_score": -5}))
        out = _select()
        assert out[0]["virality_score"] == 10 and out[0]["hook_score"] == 1

    def test_missing_or_junk_scores_get_a_neutral_default(self, ai):
        ai(_clips({"start": 0, "end": 30},
                  {"start": 40, "end": 70, "virality_score": "high"}))
        out = _select()
        assert all(1 <= w["virality_score"] <= 10 for w in out)

    def test_an_invented_hook_type_collapses_to_general(self, ai):
        """The UI colours by hook type; free-form strings have no colour."""
        ai(_clips({"start": 0, "end": 30, "hook_type": "vibes_based_intrigue"}))
        assert _select()[0]["hook_type"] == "general"

    def test_a_known_hook_type_survives_normalisation(self, ai):
        ai(_clips({"start": 0, "end": 30, "hook_type": "Curiosity Gap"}))
        assert _select()[0]["hook_type"] == "curiosity_gap"

    def test_a_missing_hook_type_becomes_general(self, ai):
        ai(_clips({"start": 0, "end": 30}))
        assert _select()[0]["hook_type"] == "general"

    def test_a_partial_score_breakdown_is_filled_from_the_overall_score(self, ai):
        """Missing sub-scores default to the clip's own virality score so the
        UI bars don't collapse to zero on a partial response."""
        ai(_clips({"start": 0, "end": 30, "virality_score": 7,
                   "score_breakdown": {"flow": 9}}))
        bd = _select()[0]["score_breakdown"]
        assert bd["flow"] == 9
        assert set(bd) == set(CE._SCORE_BREAKDOWN_KEYS)
        assert bd["value"] == 7, "an omitted sub-score inherits the overall one"
        assert all(1 <= v <= 10 for v in bd.values())

    def test_an_absent_score_breakdown_yields_an_empty_dict(self, ai):
        """Not a default-filled one — the UI hides the panel for that clip
        rather than drawing four identical invented bars."""
        ai(_clips({"start": 0, "end": 30, "virality_score": 7}))
        assert _select()[0]["score_breakdown"] == {}

    def test_junk_sub_scores_fall_back_to_the_overall_score(self, ai):
        ai(_clips({"start": 0, "end": 30, "virality_score": 6,
                   "score_breakdown": {"flow": "very high"}}))
        assert _select()[0]["score_breakdown"]["flow"] == 6

    def test_results_come_back_best_first(self, ai):
        ai(_clips({"start": 0, "end": 30, "virality_score": 4},
                  {"start": 40, "end": 70, "virality_score": 9},
                  {"start": 80, "end": 110, "virality_score": 6}))
        scores = [w["virality_score"] for w in _select()]
        assert scores == sorted(scores, reverse=True)

    def test_the_clip_cap_is_enforced(self, ai):
        ai(_clips(*[{"start": i * 40, "end": i * 40 + 30} for i in range(10)]))
        assert len(_select(max_clips=3, duration=1000)) == 3


class TestModelFailureModes:
    def test_a_single_object_instead_of_an_array_is_accepted(self, ai):
        """Models do this constantly when asked for exactly one clip."""
        ai(json.dumps({"start": 0, "end": 30}))
        assert len(_select()) == 1

    def test_a_fenced_response_is_parsed(self, ai):
        ai("```json\n" + _clips({"start": 0, "end": 30}) + "\n```")
        assert len(_select()) == 1

    def test_garbage_is_retried_then_gives_up_cleanly(self, ai):
        c = ai("not json at all")
        assert _select() == []
        assert len(c.prompts) == 2, "it should retry once before giving up"

    def test_a_retry_recovers_a_transient_failure(self, ai):
        ai(RuntimeError("model overloaded"), _clips({"start": 0, "end": 30}))
        assert len(_select()) == 1

    def test_a_persistent_ai_failure_returns_empty_rather_than_raising(self, ai):
        """A raise here fails the whole extraction job."""
        ai(RuntimeError("model down"))
        assert _select() == []

    def test_an_empty_array_is_respected(self, ai):
        """"Nothing here matches" is a legitimate answer, especially with a
        user query — padding it with weak clips would be worse."""
        ai("[]")
        assert _select(user_query="something absent") == []


class TestRetryCascade:
    """The cascade widens only the bounds the user did NOT pin."""

    def _run(self, **kw):
        defaults = dict(segments=SEGMENTS, title="T", duration=600,
                        max_clips=5, user_settings=None)
        defaults.update(kw)
        return asyncio.run(CE._select_clip_windows_with_retries(**defaults))

    def test_a_first_attempt_hit_needs_no_retry(self, ai):
        c = ai(_clips({"start": 0, "end": 45}))
        assert len(self._run()) == 1
        assert len(c.prompts) == 1

    def test_both_bounds_pinned_means_no_widening(self, ai):
        """Retrying with the same bounds can't help, and widening them would
        ship a clip length the user explicitly ruled out."""
        c = ai("[]")
        assert self._run(min_duration=10, max_duration=20) == []
        assert len(c.prompts) <= 2, "no third call with identical bounds"

    def test_an_unpinned_search_widens_and_retries(self, ai):
        c = ai("[]", "[]", _clips({"start": 0, "end": 45}))
        out = self._run()
        assert len(c.prompts) > 2, "it should relax and try again"
        assert len(out) == 1
