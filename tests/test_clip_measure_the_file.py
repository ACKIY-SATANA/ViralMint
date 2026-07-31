# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Clipper must measure the finished file, not trust the request.

Three ported fixes are locked in here.

1. caption_style="none" burned viral subtitles anyway. Clip Studio offers a
   "none" chip and even greys out AutoEmoji when it's picked, but nothing
   backend-side treated it as a sentinel: `generate_captions_ass` falls back to
   the "viral" preset for ANY unknown style name (a deliberate never-crash
   default), so "no captions" produced fully burned-in word-by-word subs.

2. Every extracted clip was persisted as aspect_ratio="9:16" and with the
   REQUESTED window length. Extraction only reframes a LANDSCAPE source, so a
   square / 4:5 source keeps its own shape; and remove_silence cuts content
   out, so the window overstates the duration. Both now come from one ffprobe
   of the finished file.

3. AI metadata was spread LAST and unfiltered into the clip dict, so any key
   the model invented won — including `video_path`, which is what we persist
   and serve.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.services.caption_service import (
    CAPTION_STYLES,
    CAPTIONS_OFF,
    captions_disabled,
)
from backend.services.clip_extractor import _burn_clip_captions, _metadata_only
from backend.services.video_utils import aspect_from_dims


def _segments() -> list[dict]:
    return [{
        "start": 0.0, "end": 2.0, "text": "hello world",
        "words": [
            {"text": "hello", "start": 0.0, "end": 1.0},
            {"text": "world", "start": 1.0, "end": 2.0},
        ],
    }]


# ── 1. the "no captions" sentinel ──────────────────────────────────────────

class TestCaptionsDisabledSentinel:
    @pytest.mark.parametrize("style", ["none", "None", " NONE ", "off", "disabled"])
    def test_off_sentinels_recognised(self, style):
        assert captions_disabled(style) is True

    @pytest.mark.parametrize("style", ["viral", "classic", "bold", "neon"])
    def test_real_styles_are_not_off(self, style):
        assert captions_disabled(style) is False

    @pytest.mark.parametrize("style", [None, "", "   "])
    def test_unspecified_is_not_off(self, style):
        """None/empty mean "caller didn't say" — callers default them to
        "viral" themselves. Treating them as off would silently strip captions
        from every path that passes an unset value through."""
        assert captions_disabled(style) is False

    def test_none_is_not_a_preset(self):
        """The bug's mechanism: "none" isn't in the preset table and the
        builder's unknown-style fallback is "viral". If someone ever adds a
        literal "none" preset, this fires and the guard needs a rethink."""
        assert "none" not in CAPTION_STYLES
        assert CAPTIONS_OFF.isdisjoint(CAPTION_STYLES)


class TestBurnClipCaptionsSkipsWhenOff:
    def test_style_none_returns_clip_untouched(self):
        clip = Path("/tmp/does-not-need-to-exist.mp4")
        out, status = asyncio.run(_burn_clip_captions(clip, _segments(), "none"))
        assert out == clip
        assert status == "skipped"

    def test_hook_overlay_is_skipped_too(self):
        """The hook rides the same ASS file — "none" means no burned text of
        any kind, not "no subtitles but keep the hook"."""
        clip = Path("/tmp/does-not-need-to-exist.mp4")
        out, status = asyncio.run(
            _burn_clip_captions(clip, _segments(), "none", hook_text="WATCH THIS"))
        assert (out, status) == (clip, "skipped")

    def test_guard_runs_before_the_ass_builder(self, monkeypatch):
        """If the short-circuit ever moves after the builder, the viral
        fallback silently comes back."""
        from backend.services import caption_service

        def _boom(*a, **kw):
            raise AssertionError("generate_captions_ass must not be reached")

        monkeypatch.setattr(caption_service, "generate_captions_ass", _boom)
        out, status = asyncio.run(
            _burn_clip_captions(Path("/tmp/x.mp4"), _segments(), "none"))
        assert status == "skipped"


# ── 2. aspect + duration come from the file ────────────────────────────────

@pytest.mark.parametrize("dims,expected", [
    ((1920, 1080), "16:9"),
    ((1080, 1920), "9:16"),
    ((1080, 1080), "1:1"),
    ((1080, 1082), "1:1"),     # encoder rounding is still square in intent
    ((1080, 1350), "9:16"),    # 4:5 is portrait, not square
])
def test_aspect_from_dims(dims, expected):
    assert aspect_from_dims(*dims) == expected


def test_aspect_from_dims_falls_back_when_unmeasurable():
    assert aspect_from_dims(0, 0) == "9:16"
    assert aspect_from_dims(0, 0, default="16:9") == "16:9"


def test_probe_media_returns_zeroes_instead_of_raising(tmp_path):
    from backend.services.video_utils import probe_media
    assert probe_media(tmp_path / "not-a-video.mp4") == (0, 0, 0.0)


# ── 3. model output can't overwrite pipeline-owned fields ──────────────────

class TestMetadataWhitelist:
    def test_keeps_the_four_requested_fields(self):
        meta = {
            "youtube_title": "T", "youtube_description": "D",
            "youtube_tags": ["a"], "tiktok_title": "TT",
        }
        assert _metadata_only(meta) == meta

    def test_drops_everything_else(self):
        """A hallucinated `video_path` used to become the row's video_path."""
        out = _metadata_only({
            "youtube_title": "T",
            "video_path": "/etc/passwd",
            "caption_status": "applied",
            "duration_seconds": 999,
            "start": 0, "end": 999,
        })
        assert out == {"youtube_title": "T"}

    @pytest.mark.parametrize("bad", [None, "a string", 42, ["list"]])
    def test_non_dict_model_output_is_dropped(self, bad):
        assert _metadata_only(bad) == {}
