# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Clip extraction — the pure logic between the model and the ffmpeg fan-out.

Everything here is deterministic and offline. It's also where the expensive
mistakes live: these functions decide WHICH seconds of a video get cut, and a
wrong answer is only visible after minutes of Whisper and N re-encodes.

Four groups:

  * `_parse_json_response` — the model's output arrives fenced, prefixed,
    trailing-prose'd, or plain broken. It must extract what's there and return
    None rather than raise, because a parse error becomes a failed job.
  * window shaping — overlap removal, sentence snapping, duration fallback.
    A snap that grows a clip past the user's max_duration, or an overlap check
    that lets two clips share footage, ships duplicate/oversized output.
  * `_retime_segments_after_removal` — after silence is cut, every caption
    timestamp has to move with it. Getting this wrong desyncs every caption in
    the clip, which is the most visible failure the product has.
  * `_build_segments_text` — the transcript window handed to the model,
    including the long-video sampling that keeps a 3-hour podcast inside the
    context budget.
"""
from __future__ import annotations

import json

import pytest

from backend.services import clip_extractor as CE


# ── the model's JSON ────────────────────────────────────────────────────────

class TestParseJsonResponse:
    def test_plain_json(self):
        assert CE._parse_json_response('[{"start": 1}]') == [{"start": 1}]

    def test_a_fenced_block(self):
        assert CE._parse_json_response('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_an_unlabelled_fence(self):
        assert CE._parse_json_response('```\n{"a": 1}\n```') == {"a": 1}

    def test_json_buried_in_prose(self):
        """Models routinely prepend "Here are the clips:"."""
        assert CE._parse_json_response(
            'Here are the clips:\n[{"a": 1}]\nHope that helps!') == [{"a": 1}]

    def test_an_object_buried_in_prose(self):
        assert CE._parse_json_response('Sure — {"a": 1} — done') == {"a": 1}

    @pytest.mark.parametrize("bad", ["", None, "no json at all", "[unclosed",
                                     "```json\nnot json\n```"])
    def test_unparseable_input_is_none_not_an_exception(self, bad):
        """A raise here fails the whole extraction job."""
        assert CE._parse_json_response(bad) is None

    def test_whitespace_is_tolerated(self):
        assert CE._parse_json_response('   \n [1, 2] \n  ') == [1, 2]


# ── overlap removal ─────────────────────────────────────────────────────────

def _w(start, end, score=5.0, **kw):
    return {"start": start, "end": end, "virality_score": score,
            "title": "t", "hook": "", "reason": "", **kw}


class TestRemoveOverlappingClips:
    def test_non_overlapping_clips_all_survive(self):
        out = CE._remove_overlapping_clips([_w(0, 30), _w(40, 70), _w(80, 110)])
        assert len(out) == 3

    def test_the_first_of_an_overlapping_pair_wins(self):
        """Input is pre-sorted by score, so first == highest scored."""
        out = CE._remove_overlapping_clips([_w(0, 30, 9.0), _w(10, 40, 5.0)])
        assert len(out) == 1 and out[0]["virality_score"] == 9.0

    def test_a_two_second_touch_is_tolerated(self):
        """Clip boundaries land a beat apart constantly; a strict check would
        throw away perfectly good adjacent clips."""
        out = CE._remove_overlapping_clips([_w(0, 30), _w(29, 60)])
        assert len(out) == 2

    def test_a_real_overlap_is_dropped(self):
        out = CE._remove_overlapping_clips([_w(0, 30), _w(15, 45)])
        assert len(out) == 1

    def test_a_fully_contained_clip_is_dropped(self):
        out = CE._remove_overlapping_clips([_w(0, 60), _w(10, 20)])
        assert len(out) == 1

    def test_an_empty_list_is_empty(self):
        assert CE._remove_overlapping_clips([]) == []


# ── sentence snapping ───────────────────────────────────────────────────────

SEGS = [
    {"start": 0.0, "end": 5.0, "text": "First sentence."},
    {"start": 5.0, "end": 11.0, "text": "Second one here."},
    {"start": 11.0, "end": 20.0, "text": "And a third."},
    {"start": 20.0, "end": 32.0, "text": "Finally the fourth."},
]


class TestSnapToSentenceBoundaries:
    def test_a_near_boundary_is_snapped(self):
        """Cutting mid-word, just before the punchline, is the complaint this
        exists to fix."""
        out = CE._snap_to_sentence_boundaries([_w(5.4, 19.6)], SEGS)
        assert out[0]["start"] == 5.0 and out[0]["end"] == 20.0

    def test_a_far_boundary_is_left_alone(self):
        """The slide budget stops a snap from growing a clip past the user's
        max_duration."""
        out = CE._snap_to_sentence_boundaries([_w(8.0, 15.0)], SEGS)
        assert out[0]["start"] == 8.0 and out[0]["end"] == 15.0

    def test_the_input_is_not_mutated(self):
        original = _w(5.4, 19.6)
        CE._snap_to_sentence_boundaries([original], SEGS)
        assert original["start"] == 5.4, "callers still hold the pre-snap window"

    def test_no_segments_means_no_snapping(self):
        out = CE._snap_to_sentence_boundaries([_w(5.4, 19.6)], [])
        assert out[0]["start"] == 5.4

    def test_other_fields_survive_the_snap(self):
        out = CE._snap_to_sentence_boundaries(
            [_w(5.4, 19.6, hook="the hook", title="My clip")], SEGS)
        assert out[0]["hook"] == "the hook" and out[0]["title"] == "My clip"


# ── the no-speech duration fallback ─────────────────────────────────────────

class TestDurationBasedClips:
    def test_it_splits_a_video_into_clips(self):
        out = CE._generate_duration_based_clips(300, 5)
        assert 1 <= len(out) <= 5
        assert all(c["end"] > c["start"] for c in out)

    def test_clips_do_not_overrun_the_source(self):
        out = CE._generate_duration_based_clips(100, 10)
        assert all(c["end"] <= 100 + 0.01 for c in out), out

    def test_they_do_not_overlap(self):
        out = sorted(CE._generate_duration_based_clips(600, 8),
                     key=lambda c: c["start"])
        for a, b in zip(out, out[1:]):
            assert b["start"] >= a["end"] - 0.01

    def test_the_requested_length_is_honoured(self):
        out = CE._generate_duration_based_clips(600, 5, min_duration=20,
                                                max_duration=30)
        assert all(19 <= (c["end"] - c["start"]) <= 31 for c in out), out

    def test_a_video_shorter_than_the_minimum_yields_nothing_or_one_clip(self):
        out = CE._generate_duration_based_clips(5, 3, min_duration=30)
        assert len(out) <= 1

    def test_a_zero_duration_source_yields_a_degenerate_window(self):
        """Pinning real behaviour. When yt-dlp recorded no duration this
        returns ONE window of 0s→0s rather than nothing, and because it's
        non-empty the caller's `if not clip_windows` guard doesn't fire — so a
        zero-length cut is attempted instead of a clean "can't extract" error."""
        out = CE._generate_duration_based_clips(0, 5)
        assert len(out) == 1
        assert out[0]["start"] == out[0]["end"] == 0

    def test_the_title_is_carried_onto_each_clip(self):
        out = CE._generate_duration_based_clips(120, 2, title="My Podcast")
        assert all("My Podcast" in c["title"] for c in out)


# ── re-timing captions after silence removal ────────────────────────────────

class TestRetimeAfterRemoval:
    """After cutting silence, every caption timestamp has to move with the
    content. A mistake here desyncs every caption in the clip."""

    def test_a_segment_inside_the_first_kept_range_keeps_its_time(self):
        out = CE._retime_segments_after_removal(
            [{"start": 1.0, "end": 2.0, "text": "hi"}], [(0.0, 5.0)])
        assert out[0]["start"] == 1.0 and out[0]["end"] == 2.0

    def test_a_segment_after_a_cut_shifts_earlier_by_the_removed_time(self):
        """Keep 0-5 and 10-15: the 4 seconds of silence between them are gone,
        so content at 11s now plays at 6s."""
        out = CE._retime_segments_after_removal(
            [{"start": 11.0, "end": 12.0, "text": "later"}],
            [(0.0, 5.0), (10.0, 15.0)])
        assert out[0]["start"] == pytest.approx(6.0)
        assert out[0]["end"] == pytest.approx(7.0)

    def test_a_segment_entirely_inside_removed_silence_is_dropped(self):
        out = CE._retime_segments_after_removal(
            [{"start": 6.0, "end": 9.0, "text": "in the gap"}],
            [(0.0, 5.0), (10.0, 15.0)])
        assert out == []

    def test_word_timestamps_are_re_timed_too(self):
        """Word-level timings drive the per-word highlight; leaving them on
        the original timeline is the same desync, one level down."""
        out = CE._retime_segments_after_removal([{
            "start": 11.0, "end": 13.0, "text": "two words",
            "words": [{"word": "two", "start": 11.0, "end": 12.0},
                      {"word": "words", "start": 12.0, "end": 13.0}],
        }], [(0.0, 5.0), (10.0, 15.0)])
        assert [w["start"] for w in out[0]["words"]] == pytest.approx([6.0, 7.0])

    def test_a_segment_whose_words_all_vanish_is_dropped(self):
        out = CE._retime_segments_after_removal([{
            "start": 6.0, "end": 9.0, "text": "gone",
            "words": [{"word": "gone", "start": 6.0, "end": 9.0}],
        }], [(0.0, 5.0), (10.0, 15.0)])
        assert out == []

    def test_other_segment_fields_are_preserved(self):
        out = CE._retime_segments_after_removal(
            [{"start": 1.0, "end": 2.0, "text": "hi", "speaker": "A"}],
            [(0.0, 5.0)])
        assert out[0]["speaker"] == "A" and out[0]["text"] == "hi"

    def test_no_segments_is_empty(self):
        assert CE._retime_segments_after_removal([], [(0.0, 5.0)]) == []

    def test_the_original_segments_are_not_mutated(self):
        seg = {"start": 11.0, "end": 12.0, "text": "x"}
        CE._retime_segments_after_removal([seg], [(0.0, 5.0), (10.0, 15.0)])
        assert seg["start"] == 11.0


# ── the transcript window given to the model ────────────────────────────────

class TestBuildSegmentsText:
    def test_no_segments_is_empty(self):
        assert CE._build_segments_text([], 100) == ""

    def test_timestamps_are_included_so_the_model_can_cite_them(self):
        out = CE._build_segments_text(
            [{"start": 1.5, "end": 4.0, "text": "hello"}], 100)
        assert "1.5s" in out and "hello" in out

    def test_a_long_transcript_is_truncated_with_a_marker(self):
        segs = [{"start": i, "end": i + 1, "text": "word " * 40} for i in range(200)]
        out = CE._build_segments_text(segs, 300, max_chars=500)
        assert len(out) < 2000
        assert "truncated" in out

    def test_the_sampler_can_reach_its_first_skip_marker(self):
        segs = [{"start": i * 20, "end": i * 20 + 9, "text": f"seg {i}"}
                for i in range(240)]
        out = CE._build_segments_text(segs, duration=4800, max_chars=1500)
        assert out.count("segments omitted") == 1

    @pytest.mark.xfail(strict=True, reason=(
        "GAP: the sampler's second half is unreachable BY CONSTRUCTION. It "
        "engages only when total_text > 2*max_chars, i.e. the budget is under "
        "half the transcript — but reaching the SECOND marker requires the "
        "first two quarters (half the segments) to fit INSIDE that budget. "
        "The two conditions can't both hold, so the last-quarter slice is "
        "never emitted: the model never sees the END of a long video"))
    def test_the_sampler_also_shows_the_end_of_the_video(self):
        for n, mc in ((240, 1500), (400, 3000), (2160, 12000)):
            segs = [{"start": i * 20, "end": i * 20 + 9, "text": f"seg {i}"}
                    for i in range(n)]
            out = CE._build_segments_text(segs, duration=n * 20, max_chars=mc)
            if out.count("segments omitted") == 2:
                return
        raise AssertionError("no input reaches the second skip marker")

    @pytest.mark.xfail(strict=True, reason=(
        "GAP: on a REAL long video the sampler is defeated by its own budget. "
        "It slices [first quarter | marker | middle | marker | last quarter] "
        "and THEN truncates at max_chars — but a 3-hour podcast's first "
        "quarter alone (~540 segments) far exceeds the 12000-char default, so "
        "the output truncates inside it. Measured: the model is handed the "
        "first ~860s of a 10800s video, and clip selection therefore only "
        "ever considers the opening ~14 minutes"))
    def test_a_three_hour_podcast_is_sampled_across_its_whole_length(self):
        segs = [{"start": i * 5, "end": i * 5 + 4.5,
                 "text": "This is roughly what a whisper segment looks like ok"}
                for i in range(2160)]                       # ~3 hours
        out = CE._build_segments_text(segs, duration=10800)  # default max_chars
        assert "segments omitted" in out, (
            "the model only sees the first %s" % out.splitlines()[-1])

    def test_a_short_video_is_never_sampled(self):
        segs = [{"start": i, "end": i + 1, "text": "hi"} for i in range(10)]
        out = CE._build_segments_text(segs, duration=60)
        assert "segments omitted" not in out
        assert out.count("\n") == 9

    def test_a_segment_missing_its_text_does_not_crash(self):
        out = CE._build_segments_text([{"start": 0.0, "end": 1.0}], 60)
        assert isinstance(out, str)


# ── misc pure helpers ───────────────────────────────────────────────────────

class TestCjkDetection:
    @pytest.mark.parametrize("text,expected", [
        ("hello world", False),
        ("你好世界", True),
        ("mixed 中文 english", True),
        ("", False),
        ("日本語のテキスト", True),
        ("123 !@#", False),
    ])
    def test_it_detects_cjk(self, text, expected):
        assert CE._has_cjk(text) is expected


class TestCountSpeechUnits:
    def test_latin_text_counts_words(self):
        assert CE._count_speech_units("one two three four") == 4

    def test_cjk_text_counts_characters(self):
        """CJK has no spaces — counting "words" would report 1 for a whole
        paragraph and the clip estimator would scale everything wrong."""
        assert CE._count_speech_units("你好世界你好") >= 6

    def test_empty_is_zero(self):
        assert CE._count_speech_units("") == 0
