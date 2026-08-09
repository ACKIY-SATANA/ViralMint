# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Platform safe zones — the parts of a vertical video the destination app
covers with its own UI.

A short posted to TikTok / Reels / Shorts is not shown edge to edge: the app
draws the username, caption, music ticker and a column of action icons *on
top of* the video. Anything burned into those bands is unreadable, and burned
pixels can't be undone after export.

Insets are expressed as **fractions of the frame**, never pixels. The caption
styles used absolute pixel margins tuned for a 1920-tall frame, which silently
means something different on a 1:1, a 4:5 or a 4K render. (OpenCut hit this
from the other direction: their ASS parser emits `fontSizeRatioOfPlayHeight`
rather than a pixel size so the consumer doesn't inherit the source file's
coordinate space.)

═══ WHERE THE NUMBERS COME FROM ═══
Derived from OpenCut's hand-traced TikTok UI overlay (MIT,
`guides/definitions/tiktok-layout.tsx`), whose SVG is authored at
`viewBox="0 0 1080 1920"` — the same coordinate space as a vertical short.
Parsing its path geometry gives, on that 1080×1920 frame:

    bottom block (username / caption / music)   y 1741 → 1884   → 179px
    right icon rail                             x  924 → 1052   → 156px
    top bar (status + For You tabs)             y   41 →  273   → 273px

Two adjustments before they become the constants below:

  1. The trace shows a ONE-LINE caption. TikTok wraps to two lines before
     truncating, which pushes the block up by roughly another 80px. The
     bottom inset therefore budgets ~2 lines, not the traced minimum.
  2. It is one designer's trace of one screenshot, not a spec from TikTok.

⚠️ These are the most defensible numbers available, NOT measurements from a
real device. If you have screenshots, measure and update the constants here —
that is the only place they live, and nothing else needs to change.

Only TikTok is modelled. OpenCut advertises Reels / Shorts / Spotlight guides
but all three render nothing (`renderOverlay: () => null`), so there was no
second data point to copy, and inventing per-platform numbers would be
fabricating precision. Instead the vertical default IS the TikTok zone: it's
the most invasive of the four, so clearing it clears the others. Add a real
platform entry when someone measures one.
"""
from __future__ import annotations

# Fractions of frame height (top/bottom) and width (left/right).
_TIKTOK = {
    "bottom": 0.16,   # 307px on 1920 — traced 179px + a two-line caption
    "top": 0.15,      # 288px on 1920 — traced 273px, rounded up
    "right": 0.15,    # 162px on 1080 — traced 156px icon rail
    "left": 0.03,     # traced 30px
}

# Landscape playback (YouTube, embeds) has no persistent overlay covering the
# frame; this is broadcast-style title-safe padding, not app chrome.
_LANDSCAPE = {"bottom": 0.04, "top": 0.04, "right": 0.03, "left": 0.03}

SAFE_ZONES: dict[str, dict[str, float]] = {
    "tiktok": _TIKTOK,
    # Default for any vertical render whose destination we don't know.
    "vertical": _TIKTOK,
    "landscape": _LANDSCAPE,
}

# Aspect labels that get the vertical treatment.
#
# `aspect_from_dims` only ever emits 9:16 / 16:9 / 1:1, so 4:5 is unreachable
# from the caption path today — it's listed because export offers 4:5 and a
# caller could pass it through later. It's grouped with vertical deliberately:
# a 4:5 post to Instagram's feed has its chrome BELOW the media (nothing to
# clear), but the same file posted to TikTok is letterboxed into a 9:16 screen
# whose chrome does overlap it. Erring high costs a little framing; erring low
# burns text under the UI permanently.
#
# 1:1 is NOT here — square feed posts draw their controls outside the media.
VERTICAL_ASPECTS = frozenset({"9:16", "4:5"})

# A floor must never push text past the middle of the frame, whatever a future
# table says — a mis-typed 0.6 should degrade to "high but on screen" rather
# than throw the caption into the top half.
_MAX_INSET_FRACTION = 0.40


def resolve_zone(aspect_ratio: str, platform: str | None = None) -> dict[str, float]:
    """Insets for this render.

    An explicit, known `platform` wins. Otherwise the aspect decides: vertical
    output gets the conservative vertical zone, everything else (16:9, 1:1)
    gets landscape padding. An unknown platform name is ignored rather than
    raising — a caption is never worth failing a render over.
    """
    if platform:
        zone = SAFE_ZONES.get(platform.strip().lower())
        if zone:
            return zone
    return SAFE_ZONES["vertical"] if aspect_ratio in VERTICAL_ASPECTS else SAFE_ZONES["landscape"]


def _inset_px(fraction: float, extent: int, pad: int) -> int:
    """Fraction of `extent`, plus `pad`, clamped so it stays sane."""
    if extent <= 0:
        return 0
    capped = min(max(fraction, 0.0), _MAX_INSET_FRACTION)
    return int(round(capped * extent)) + max(pad, 0)


def min_margin_v(aspect_ratio: str, frame_h: int, platform: str | None = None,
                 pad: int = 0) -> int:
    """Smallest bottom margin (ASS `MarginV` under a bottom alignment) that
    keeps the caption clear of the platform's bottom chrome.

    `pad` is added on top — pass the style's outline + shadow, which libass
    draws OUTSIDE the text box, so the visible bottom of the glyphs clears too.
    """
    return _inset_px(resolve_zone(aspect_ratio, platform)["bottom"], frame_h, pad)


def min_margin_top(aspect_ratio: str, frame_h: int, platform: str | None = None,
                   pad: int = 0) -> int:
    """Smallest top margin (ASS `MarginV` under a top alignment, e.g. the hook
    overlay) that keeps text clear of the status bar / tab row."""
    return _inset_px(resolve_zone(aspect_ratio, platform)["top"], frame_h, pad)


def min_margin_horizontal(aspect_ratio: str, frame_w: int, platform: str | None = None,
                          pad: int = 0) -> tuple[int, int]:
    """(left, right) insets. The right one is the action-icon rail.

    Unused by the caption burner today — captions are bottom-centre and full
    width — but it's the same measurement, and it's what auto-reframe needs to
    stop putting faces under the icon column.
    """
    zone = resolve_zone(aspect_ratio, platform)
    return (
        _inset_px(zone["left"], frame_w, pad),
        _inset_px(zone["right"], frame_w, pad),
    )
