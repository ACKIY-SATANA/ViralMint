# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""One short batch must not kill a translation job.

Translation used to be a SINGLE AI call carrying every segment of the video,
parsed with a strict shape check. Two ways that lost the whole run after
Whisper had already paid for itself:

  * a long video overflowed the token budget and came back as truncated JSON;
  * any count mismatch ("input 20, output 19" — how a real Chinese-caption
    request died) raised and discarded everything already translated.

Now: batches of 20, and a batch that comes back the wrong shape is SPLIT and
retried in halves down to single lines, where the count can't be wrong. A line
that fails even alone keeps its source text and is marked `untranslated`,
because dropping or padding it would slide every later caption off its
timestamp.

The split replaces the retry (attempts=1 when recursing), so one pathological
batch can't fan out into dozens of calls — pinned below.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.core import tool_runners as trun


def _segments(n: int) -> list[dict]:
    return [{"start": float(i), "end": float(i + 1), "text": f"line {i}",
             "words": [{"word": "line", "start": float(i), "end": float(i) + 0.5}]}
            for i in range(n)]


class _AI:
    """Fake AI client. `responder(n)` (or `responder(n, prompt)`) builds each
    raw reply; `n` is the batch size recovered from the prompt's contract."""

    def __init__(self, responder):
        self._responder = responder
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        prompt = messages[0]["content"]
        n = int(prompt.split("MUST have exactly ")[1].split(" elements")[0])
        try:
            return self._responder(n, prompt)
        except TypeError:
            return self._responder(n)


def _run(ai, segments, monkeypatch, language="Chinese"):
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda s: ai)
    return asyncio.run(trun._translate_segments(segments, language, object()))


def test_happy_path_translates_every_line(monkeypatch):
    ai = _AI(lambda n: json.dumps([f"zh {i}" for i in range(n)]))
    out = _run(ai, _segments(5), monkeypatch)
    assert [s["text"] for s in out] == [f"zh {i}" for i in range(5)]
    assert all("untranslated" not in s for s in out)


def test_timings_are_preserved(monkeypatch):
    ai = _AI(lambda n: json.dumps(["x"] * n))
    out = _run(ai, _segments(3), monkeypatch)
    assert [(s["start"], s["end"]) for s in out] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]


def test_source_words_are_dropped(monkeypatch):
    """Whisper's `words` are the SOURCE language's words. Keeping them makes
    the renderer burn the original text — the "nothing was translated" bug."""
    ai = _AI(lambda n: json.dumps(["译文"] * n))
    out = _run(ai, _segments(2), monkeypatch)
    assert all("words" not in s for s in out)


def test_batches_of_twenty(monkeypatch):
    ai = _AI(lambda n: json.dumps(["x"] * n))
    out = _run(ai, _segments(45), monkeypatch)
    assert len(out) == 45
    assert ai.calls == 3          # 20 + 20 + 5, not one giant call


def test_a_short_batch_is_split_not_fatal(monkeypatch):
    """The reported failure: 20 in, 19 out. It must not lose the job."""
    state = {"first": True}

    def responder(n):
        if n == 20 and state["first"]:
            state["first"] = False
            return json.dumps(["x"] * 19)      # the mismatch
        return json.dumps([f"ok{n}"] * n)

    ai = _AI(responder)
    out = _run(ai, _segments(20), monkeypatch)
    assert len(out) == 20
    assert all("untranslated" not in s for s in out)


def test_a_hopeless_line_keeps_its_source_text(monkeypatch):
    """Degrade ONE caption rather than slide every later one off its
    timestamp. (All of them failing is an error — see the next test.)"""
    def responder(n, prompt):
        # "line 2" is poison: any batch containing it comes back malformed,
        # so the split walks down to it and only it degrades.
        if "line 2" in prompt:
            return "not json at all"
        return json.dumps([f"zh{i}" for i in range(n)])

    ai = _AI(responder)
    out = _run(ai, _segments(4), monkeypatch)
    assert len(out) == 4
    assert [s.get("untranslated", False) for s in out] == [False, False, True, False]
    assert out[2]["text"] == "line 2"          # source text kept, timing intact
    assert (out[2]["start"], out[2]["end"]) == (2.0, 3.0)


def test_every_line_failing_is_an_error_not_a_silent_passthrough(monkeypatch):
    """Handing back the source captions under a "translated" label would burn
    a full render for nothing."""
    ai = _AI(lambda n: "not json at all")
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda s: ai)
    with pytest.raises(RuntimeError, match="never returned a usable result"):
        asyncio.run(trun._translate_segments(_segments(3), "Chinese", object()))


def test_the_split_does_not_fan_out_into_dozens_of_calls(monkeypatch):
    """attempts=1 on recursion. A 20-line batch that never succeeds costs the
    ~40 calls of a binary split, not the ~60+ of retrying at every level."""
    ai = _AI(lambda n: "garbage")
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda s: ai)
    with pytest.raises(RuntimeError):          # nothing translated at all
        asyncio.run(trun._translate_segments(_segments(20), "Chinese", object()))
    assert ai.calls <= 45, f"fan-out too wide: {ai.calls} calls"


def test_markdown_fenced_json_is_accepted(monkeypatch):
    ai = _AI(lambda n: "```json\n" + json.dumps(["x"] * n) + "\n```")
    out = _run(ai, _segments(2), monkeypatch)
    assert [s["text"] for s in out] == ["x", "x"]


def test_blank_segments_are_skipped(monkeypatch):
    ai = _AI(lambda n: json.dumps(["x"] * n))
    segs = _segments(2) + [{"start": 9, "end": 10, "text": "   "}]
    out = _run(ai, segs, monkeypatch)
    assert len(out) == 2


def test_no_segments_returns_empty_without_calling_the_model(monkeypatch):
    ai = _AI(lambda n: json.dumps([]))
    assert _run(ai, [], monkeypatch) == []
    assert ai.calls == 0


def test_non_string_output_is_rejected_then_recovered(monkeypatch):
    """A model returning numbers must not put ints in the caption text."""
    state = {"first": True}

    def responder(n):
        if state["first"]:
            state["first"] = False
            return json.dumps([1, 2])
        return json.dumps(["ok"] * n)

    ai = _AI(responder)
    out = _run(ai, _segments(2), monkeypatch)
    assert [s["text"] for s in out] == ["ok", "ok"]
