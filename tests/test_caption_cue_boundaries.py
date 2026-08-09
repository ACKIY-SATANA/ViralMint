# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Segments without word timings are cues, not a stream of words.

Everything that isn't Whisper's own word-level output flows through
`_extract_word_timestamps`' fallback path: translated sentences, scripted
lines, imported subtitle cues. Two defects lived there.

1. Cue boundaries were invisible to the line grouper. It breaks on pauses,
   punctuation and the word/char budget — and cues authored back-to-back (zero
   gap, no closing punctuation) trip none of them, so three cues rendered as
   ONE long caption line, on screen for the whole video, showing text long
   before its own cue time. A cue boundary IS the author's line break.

2. The timeline drifted without bound. `per_word` came from the cue's FULL
   duration but `w_end` was derived from the overlap-clamped `w_start`, so
   every overlapping cue pushed the cursor past its own end and the error
   COMPOUNDED. Rolling captions (YouTube auto-captions overlap by
   construction) are exactly this shape: a 200-cue import produced a caption
   track roughly TWICE the video's length — the back half burned past EOF and
   everything before it desynced. Clamping `last_end` to the cue's own end is
   what makes the error non-accumulating.
"""
from __future__ import annotations

from backend.services.caption_service import (
    _extract_word_timestamps,
    _group_words_into_lines,
)


def _cues(*spans):
    """Wordless segments — the shape imported cues / translated lines take."""
    return [{"start": s, "end": e, "text": t} for s, e, t in spans]


class TestCueBoundaries:
    def test_back_to_back_cues_do_not_merge_into_one_line(self):
        words = _extract_word_timestamps(_cues(
            (0.0, 2.0, "Imported caption number one"),
            (2.0, 4.0, "Injection attempt cue"),
            (4.0, 6.0, "Final"),
        ))
        lines = _group_words_into_lines(words, max_words=10, max_chars=80)
        assert len(lines) == 3, (
            "each cue is its own caption line — merged, the last cue's text "
            "shows from the first cue's start time"
        )
        assert [w["text"] for w in lines[0]] == ["Imported", "caption", "number", "one"]
        assert [w["text"] for w in lines[2]] == ["Final"]

    def test_a_deliberate_one_word_cue_is_not_folded_backwards(self):
        """The orphan-merge pass glues a lone trailing word onto the previous
        line — but not across a cue boundary the author chose."""
        words = _extract_word_timestamps(_cues(
            (0.0, 2.0, "The setup line here"),
            (2.0, 3.0, "Boom"),
        ))
        lines = _group_words_into_lines(words, max_words=10, max_chars=80)
        assert len(lines) == 2
        assert [w["text"] for w in lines[1]] == ["Boom"]

    def test_whispers_worded_path_is_left_unmarked(self):
        """Whisper's segment bounds are arbitrary — the pause/punctuation
        rules serve it better than a hard break per segment."""
        segments = [{
            "start": 0.0, "end": 2.0, "text": "one two",
            "words": [{"word": "one", "start": 0.0, "end": 0.5},
                      {"word": "two", "start": 0.5, "end": 1.0}],
        }]
        words = _extract_word_timestamps(segments)
        assert not any(w.get("line_break") for w in words)


class TestOverlapDoesNotAccumulate:
    def test_a_rolling_import_never_outruns_the_video(self):
        """Each cue repeats the previous one's tail and overlaps it by 1s —
        the shape YouTube auto-captions ship in. The old math grew the track
        by ~99% of the video length; it must now end at the last cue's end."""
        cues = [
            {"start": i * 1.0, "end": i * 1.0 + 2.0,
             "text": f"rolling caption line {i}"}
            for i in range(200)
        ]
        words = _extract_word_timestamps(cues)
        last_end = max(w["end"] for w in words)
        video_end = cues[-1]["end"]
        assert last_end <= video_end + 0.01, (
            f"caption track ends at {last_end:.1f}s for a {video_end:.1f}s "
            f"source (+{last_end - video_end:.1f}s of drift)"
        )

    def test_no_word_runs_past_its_own_cue(self):
        cues = _cues((0.0, 2.0, "alpha beta gamma"), (1.0, 3.0, "delta epsilon"))
        words = _extract_word_timestamps(cues)
        assert all(w["end"] <= 3.0 + 1e-9 for w in words)

    def test_a_fully_swallowed_cue_shows_in_its_own_tail(self):
        """Rather than borrowing time from the next cue."""
        cues = _cues((0.0, 5.0, "a long first cue that covers everything"),
                     (1.0, 2.0, "swallowed"))
        words = _extract_word_timestamps(cues)
        swallowed = [w for w in words if w["text"] == "swallowed"]
        assert swallowed, "the cue must still render"
        assert 1.0 <= swallowed[0]["start"] < 2.0
        assert swallowed[0]["end"] <= 2.0 + 1e-9

    def test_non_overlapping_cues_are_spread_evenly_as_before(self):
        """The fix must not disturb the ordinary case."""
        words = _extract_word_timestamps(_cues((0.0, 3.0, "one two three")))
        assert [round(w["start"], 3) for w in words] == [0.0, 1.0, 2.0]
        assert [round(w["end"], 3) for w in words] == [1.0, 2.0, 3.0]

    def test_timestamps_stay_monotonic(self):
        cues = _cues((0.0, 2.0, "a b"), (1.5, 3.0, "c d"), (2.9, 4.0, "e f"))
        words = _extract_word_timestamps(cues)
        starts = [w["start"] for w in words]
        assert starts == sorted(starts)
