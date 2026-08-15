# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Guards for per-download yt-dlp options.

Downloading is the most load-bearing feature in the app, and these options are
now reachable from several surfaces — the Video Download tool page, the batch
endpoint, and every internal caller that passes nothing. The tests below encode
the two properties that make that safe:

  1. NO OPTIONS == THE OLD PATH. Anything that makes `normalize(None)` return
     a non-empty dict, or `format_chain(None)` return a list, silently changes
     what every pre-existing caller downloads.
  2. A QUALITY CAP CANNOT CAUSE A HARD FAILURE. Every ladder ends in the
     unconstrained rungs, so a source with nothing at the requested height
     yields a bigger file rather than an error.
"""

import pytest

from backend.services import download_options as dlo


class TestDefaultPathUntouched:
    """The properties that keep existing callers byte-identical."""

    @pytest.mark.parametrize("empty", [None, {}, "", 0, [], "nonsense", 42])
    def test_normalize_of_nothing_is_empty(self, empty):
        assert dlo.normalize(empty) == {}

    @pytest.mark.parametrize("empty", [None, {}])
    def test_format_chain_of_nothing_is_none(self, empty):
        # None is the sentinel download_video reads as "keep
        # FORMAT_FALLBACK_CHAIN". A list here would silently swap the ladder.
        assert dlo.format_chain(empty) is None

    def test_format_chain_none_is_not_a_quality_ladder(self):
        # The default chain and the built ladders are similar but NOT
        # identical; returning one for an unspecified quality would be a
        # behaviour change disguised as a no-op.
        assert dlo.format_chain({}) is not dlo.QUALITY_LADDERS["720p"]

    @pytest.mark.parametrize("empty", [None, {}])
    def test_ydl_overrides_of_nothing_is_empty(self, empty):
        assert dlo.ydl_overrides(empty) == {}

    def test_unknown_keys_and_values_are_dropped_not_raised(self):
        # These dicts can arrive from an LLM. Failing a download because a
        # model invented an option would be worse than ignoring it.
        assert dlo.normalize({"audio_quality": 0, "sponsorblock": "all"}) == {}
        assert dlo.normalize({"quality": "8k"}) == {}
        assert dlo.normalize({"subtitles": "yes"}) == {}
        assert dlo.normalize({"container": "avi"}) == {}


class TestQualityLadders:
    def test_every_ladder_ends_unconstrained(self):
        # The safety property: a cap degrades, never fails.
        for name, ladder in dlo.QUALITY_LADDERS.items():
            assert ladder[-1] == "best", f"{name} ladder must end uncapped"
            assert len(ladder) >= 2, f"{name} needs a fallback rung"

    def test_capped_ladders_lead_with_their_cap(self):
        for name, ladder in dlo.QUALITY_LADDERS.items():
            if name == "best":
                continue
            height = name.removesuffix("p")
            assert f"height<={height}" in ladder[0], f"{name} must lead with its own cap"

    def test_best_has_no_cap_anywhere(self):
        assert not any("height<=" in rung for rung in dlo.QUALITY_LADDERS["best"])

    @pytest.mark.parametrize(
        "raw,expected",
        [("4k", "2160p"), ("2160", "2160p"), ("1080", "1080p"), ("720P", "720p"),
         ("best", "best"), ("highest", "best"), ("max", "best"), ("2k", "1440p")],
    )
    def test_casual_spellings_normalize(self, raw, expected):
        # A model writes "4k" or "1080"; a UI writes the canonical id.
        assert dlo.normalize({"quality": raw})["quality"] == expected


class TestSubtitlesAndExtras:
    def test_embed_adds_the_postprocessor_and_writes_autosubs(self):
        over = dlo.ydl_overrides({"subtitles": "embed"})
        pp = over.pop("_vm_postprocessors")
        assert {"key": "FFmpegEmbedSubtitle"} in pp
        # Most videos only have auto-generated captions; without this the
        # toggle silently does nothing on the majority of YouTube URLs.
        assert over["writeautomaticsub"] is True
        assert over["subtitleslangs"] == ["en"]

    def test_file_mode_writes_but_does_not_embed(self):
        over = dlo.ydl_overrides({"subtitles": "file"})
        assert over["writesubtitles"] is True
        assert "_vm_postprocessors" not in over

    def test_off_is_the_same_as_absent(self):
        assert dlo.normalize({"subtitles": "off"}) == {}

    def test_sub_langs_are_capped_and_all_is_rejected(self):
        # "all" on a heavily-subtitled video pulls 100+ sidecar files.
        assert "sub_langs" not in dlo.normalize({"subtitles": "file", "sub_langs": ["all"]})
        many = dlo.normalize({"subtitles": "file",
                              "sub_langs": ["en", "es", "fr", "de", "pt", "ru"]})
        assert len(many["sub_langs"]) == 5

    @pytest.mark.parametrize("sneaky", [".*", "en.*", "", "  ", "english!"])
    def test_a_regex_cannot_smuggle_in_the_all_fan_out(self, sneaky):
        """yt-dlp's subtitleslangs takes REGEXES — ".*" is "all" spelled
        differently, and these values can come from an LLM. Shape-check the
        code instead of blacklisting the spellings we thought of."""
        assert "sub_langs" not in dlo.normalize(
            {"subtitles": "file", "sub_langs": [sneaky]})

    @pytest.mark.parametrize("ok", ["en", "zh-hans", "pt-br", "fil"])
    def test_real_language_codes_survive(self, ok):
        assert dlo.normalize({"subtitles": "file", "sub_langs": [ok]})["sub_langs"] == [ok]

    def test_sub_langs_accept_a_comma_string(self):
        got = dlo.normalize({"subtitles": "file", "sub_langs": "en, es"})
        assert got["sub_langs"] == ["en", "es"]

    def test_thumbnail_and_metadata_only_on_explicit_true(self):
        assert dlo.normalize({"thumbnail": "yes", "metadata": 1}) == {}
        assert dlo.normalize({"thumbnail": True, "metadata": True}) == {
            "thumbnail": True, "metadata": True}

    def test_normalize_is_idempotent(self):
        # ydl_overrides() re-normalizes whatever it is handed; that is only
        # safe if a second pass is a no-op.
        once = dlo.normalize({"quality": "4k", "subtitles": "embed",
                              "sub_langs": "en", "container": "mkv", "thumbnail": True})
        assert dlo.normalize(once) == once


class TestContainerIsAPreference:
    """A container request must degrade, never fail.

    yt-dlp's merge_output_format takes a '/'-separated preference list. A
    bare "mp4" FORCES codec pairs that can't be stream-copied into mp4
    (vp9+opus — YouTube's routine bestvideo+bestaudio pick) into the
    container anyway, and ffmpeg's merge then fails — an option turning a
    working download into an error. The "/mkv" tail is the escape hatch.
    """

    def test_mp4_request_carries_the_mkv_fallback(self):
        over = dlo.ydl_overrides({"container": "mp4"})
        assert over["merge_output_format"] == "mp4/mkv"

    def test_mkv_request_is_literal(self):
        # mkv holds anything — no fallback needed, and "mkv/mkv" would be odd.
        over = dlo.ydl_overrides({"container": "mkv"})
        assert over["merge_output_format"] == "mkv"

    def test_thumbnail_without_container_steers_away_from_webm(self):
        # EmbedThumbnail hard-raises on webm; an unconstrained YouTube merge
        # routinely produces webm. Cover-art embedding must pick a container
        # that can hold the art.
        over = dlo.ydl_overrides({"thumbnail": True})
        assert over["merge_output_format"] == "mp4/mkv"

    def test_explicit_container_wins_over_the_thumbnail_steer(self):
        over = dlo.ydl_overrides({"thumbnail": True, "container": "mkv"})
        assert over["merge_output_format"] == "mkv"

    def test_no_container_no_thumbnail_leaves_merge_format_alone(self):
        # Anything here would silently change what every subtitles/metadata
        # request downloads — merge container stays yt-dlp's choice.
        over = dlo.ydl_overrides({"subtitles": "file", "metadata": True})
        assert "merge_output_format" not in over
