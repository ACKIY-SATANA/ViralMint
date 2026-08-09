# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Captions must survive the platform they're posted to.

`margin_v_portrait` is a pixel offset from the bottom of a 1920-tall frame,
set independently per style, and nothing ever checked it against the UI the
destination app draws on top of the video. Most styles sit at 450-840px.
Three do not — minimal at 80, classic at 100, karaoke at 140 — inside the band
TikTok covers with the username, caption and music ticker. The user cannot fix
it after export; the pixels are cooked in.

`safe_zones` supplies the floor. It is a FLOOR, never a setter: a style that
already clears the chrome stays exactly where its designer put it. Insets are
FRACTIONS of the passed extent, so a 4K render gets the same *fraction* as a
720p one — the pixel constants they replace silently meant something different
on each.

Also pinned here: the derived `/api/captions/styles` payload. That list was a
hand-maintained copy of the engine's styles and drifted exactly as predicted —
7 of 10 styles, and `alignment: 5` on four of them, which libass pins to the
literal middle of the frame while IGNORING margin_v entirely (so the floor
would silently do nothing).
"""
from __future__ import annotations

import pytest

from backend.services import safe_zones
from backend.services.caption_service import CAPTION_STYLES, _build_ass_header


def _margin_v(header: str) -> int:
    """MarginV out of the Default style line (last-but-one numeric field)."""
    line = next(ln for ln in header.splitlines() if ln.startswith("Style: Default,"))
    return int(line.split(",")[-2])


def _hook_margin_v(header: str) -> int:
    line = next(ln for ln in header.splitlines() if ln.startswith("Style: Hook,"))
    return int(line.split(",")[-2])


class TestZoneResolution:
    def test_vertical_default_is_the_tiktok_zone(self):
        """The vertical default IS the most invasive zone, so clearing it
        clears the others."""
        assert safe_zones.resolve_zone("9:16") is safe_zones.SAFE_ZONES["tiktok"]

    def test_square_and_landscape_get_the_padding_zone(self):
        """A square feed post draws its controls OUTSIDE the media — there's
        no app chrome overlapping it to clear."""
        for aspect in ("1:1", "16:9"):
            assert safe_zones.resolve_zone(aspect) is safe_zones.SAFE_ZONES["landscape"]

    def test_unknown_platform_is_ignored_not_raised(self):
        """A caption is never worth failing a render over."""
        assert safe_zones.resolve_zone("9:16", "myspace") is safe_zones.SAFE_ZONES["tiktok"]

    def test_insets_are_fractions_so_they_scale(self):
        """The same fraction on 1080p and 4K — the pixel constants this
        replaces meant something different on each."""
        assert safe_zones.min_margin_v("9:16", 3840) == 2 * safe_zones.min_margin_v("9:16", 1920)

    def test_never_pushes_past_the_middle_of_the_frame(self):
        """A mis-typed 0.6 must degrade to "high but on screen", not throw the
        caption into the top half."""
        assert safe_zones._inset_px(0.9, 1920, 0) <= int(0.40 * 1920)

    def test_zero_extent_is_zero(self):
        assert safe_zones.min_margin_v("9:16", 0) == 0


class TestFloorApplication:
    @pytest.mark.parametrize("style_id", ["classic", "minimal", "karaoke"])
    def test_unsafe_styles_are_raised(self, style_id):
        style = CAPTION_STYLES[style_id]
        header = _build_ass_header(style, "9:16", (1080, 1920))
        declared = style["margin_v_portrait"]
        assert _margin_v(header) > declared, (
            f"{style_id} declares {declared}px — inside TikTok's caption bar"
        )
        assert _margin_v(header) >= safe_zones.min_margin_v("9:16", 1920)

    @pytest.mark.parametrize("style_id", ["viral", "bold", "neon", "glow",
                                          "urban", "warm", "mono", "brainrot"])
    def test_safe_styles_are_left_exactly_where_the_designer_put_them(self, style_id):
        style = CAPTION_STYLES[style_id]
        header = _build_ass_header(style, "9:16", (1080, 1920))
        assert _margin_v(header) == style["margin_v_portrait"]

    def test_the_pad_covers_outline_and_shadow(self):
        """libass draws both OUTSIDE the text box, so the visible bottom of
        the glyphs sits below MarginV."""
        thin = {**CAPTION_STYLES["minimal"], "outline_width": 0, "shadow_depth": 0}
        thick = {**CAPTION_STYLES["minimal"], "outline_width": 8, "shadow_depth": 4}
        thin_v = _margin_v(_build_ass_header(thin, "9:16", (1080, 1920)))
        thick_v = _margin_v(_build_ass_header(thick, "9:16", (1080, 1920)))
        assert thick_v == thin_v + 12

    def test_a_custom_low_margin_style_goes_through_the_same_floor(self):
        """Custom styles from the DB — including AI-generated ones, the most
        likely source of an unreviewed low margin — are not exempt."""
        custom = {**CAPTION_STYLES["viral"], "margin_v_portrait": None, "margin_v": 10}
        assert _margin_v(_build_ass_header(custom, "9:16", (1080, 1920))) >= \
            safe_zones.min_margin_v("9:16", 1920)

    def test_opt_out_places_text_exactly_where_the_style_says(self):
        style = CAPTION_STYLES["minimal"]
        header = _build_ass_header(style, "9:16", (1080, 1920), respect_safe_zone=False)
        assert _margin_v(header) == style["margin_v_portrait"]

    def test_square_is_not_given_the_vertical_inset(self):
        """A 1:1 post's chrome sits outside the media — 1080x1080 must not
        inherit the vertical chrome band."""
        style = CAPTION_STYLES["minimal"]
        square = _margin_v(_build_ass_header(style, "1:1", (1080, 1080)))
        vertical = _margin_v(_build_ass_header(style, "9:16", (1080, 1920)))
        assert square < vertical

    def test_the_hook_overlay_clears_the_top_bar(self):
        """alignment=8 makes the hook's MarginV a TOP offset — the status bar
        and For-You tab row live there."""
        header = _build_ass_header(CAPTION_STYLES["viral"], "9:16", (1080, 1920),
                                   include_hook_style=True)
        assert _hook_margin_v(header) >= safe_zones.min_margin_top("9:16", 1920)
        assert _hook_margin_v(header) > 200, "200px was inside the top bar"


class TestDriftGuards:
    def test_every_builtin_style_keeps_a_bottom_alignment(self):
        """Under a mid alignment (5) libass IGNORES MarginV, so the floor
        would silently do nothing — and the caption would pin to the literal
        centre of the frame. The API's stale copy shipped exactly that."""
        for sid, style in CAPTION_STYLES.items():
            assert style["alignment"] in (1, 2, 3), (
                f"{sid} uses alignment={style['alignment']}; the safe-zone "
                f"floor only has meaning under a BOTTOM alignment"
            )

    def test_no_builtin_style_may_be_added_inside_the_zone_unnoticed(self):
        """Not a ban — a new low-margin style is fine, it just has to be a
        KNOWN one. This list is the record of which styles the floor moves."""
        floor = safe_zones.min_margin_v("9:16", 1920)
        inside = {sid for sid, s in CAPTION_STYLES.items()
                  if s["margin_v_portrait"] < floor}
        assert inside == {"classic", "minimal", "karaoke"}, (
            f"styles inside the safe zone changed: {sorted(inside)}"
        )


class TestStylesApiIsDerived:
    def test_it_serves_every_general_engine_style(self):
        from backend.api.captions import BUILTIN_STYLES
        served = {s["id"] for s in BUILTIN_STYLES}
        expected = set(CAPTION_STYLES) - {"brainrot"}
        assert served == expected, f"drifted from the engine: {expected ^ served}"

    def test_the_params_are_the_engine_s_own(self):
        from backend.api.captions import BUILTIN_STYLES
        for s in BUILTIN_STYLES:
            engine = CAPTION_STYLES[s["id"]]
            assert s["alignment"] == engine["alignment"]
            assert s["margin_v"] == engine["margin_v_portrait"]
            assert s["font"] == engine["font"]
            assert s["words_per_group"] == engine["words_per_group"]

    def test_no_style_advertises_a_middle_alignment(self):
        """The stale copy had alignment 5 on four styles — a preview that
        promised centred text the renderer never produces."""
        from backend.api.captions import BUILTIN_STYLES
        assert all(s["alignment"] == 2 for s in BUILTIN_STYLES)


class TestCustomStyleAlignment:
    """The safe-zone floor only has meaning under a BOTTOM alignment — under a
    mid or top one libass ignores MarginV entirely, so the style pins to the
    literal centre of the frame and the floor cannot move it off the chrome.

    Both places a custom style can be born defaulted to 5 (mid-centre), which
    the engine's own notes call broken.
    """

    def test_a_created_style_defaults_to_bottom_centre(self):
        from backend.api.captions import CaptionStyleCreate
        assert CaptionStyleCreate(name="mine").alignment == 2

    @pytest.mark.parametrize("given,expected", [
        (1, 1), (2, 2), (3, 3),          # bottom row — kept
        (5, 2), (8, 2), (4, 2), (None, 2), ("x", 2), (99, 2),
    ])
    def test_a_non_bottom_alignment_is_normalized(self, given, expected):
        from backend.api.captions import normalize_alignment
        assert normalize_alignment(given) == expected

    def test_the_create_route_normalizes_what_the_client_sent(self):
        """Not just the default — a client that explicitly posts 5 would
        otherwise store a style the floor cannot reach."""
        import inspect
        from backend.api import captions
        src = inspect.getsource(captions.create_caption_style)
        assert "normalize_alignment(body.alignment)" in src

    def test_the_prompt_no_longer_offers_the_broken_option(self):
        """The model was being told 5=center is a valid choice."""
        from backend.api import captions
        import inspect
        src = inspect.getsource(captions)
        assert "5=center" not in src
