# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Small text has to clear 4.5:1 — measured, not asserted as a hex.

Two colours in the app sat between the DECORATION floor (3:1) and the TEXT
floor (4.5:1), which is the band where a colour looks deliberate and is still
unreadable:

  - the brand terracotta that marks "Created" in the Library measures 3.44:1
    on the light canvas. Right for the provenance rail and the dot, too faint
    for the word at 10px uppercase.
  - a filled success chip is white on `success.main` (#16a34a) = 3.30:1, and a
    scout grid puts a column of them on one screen.

Pinning the ratio rather than the hex means a future palette tweak is free to
pick a different colour and is not free to make it unreadable.
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"

TEXT_FLOOR = 4.5
DECORATION_FLOOR = 3.0

LIGHT_CANVAS = "#f5f0ea"     # theme.palette.background.default (light)
LIGHT_PAPER = "#ffffff"


def _lum(hexstr: str) -> float:
    h = hexstr.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _origin_colors() -> dict[str, dict[str, str]]:
    """Parse ORIGINS out of assetModel.js — the file the UI actually reads."""
    text = (FRONTEND / "components/librarynext/assetModel.js").read_text()
    body = text[text.index("export const ORIGINS = {"):text.index("export const ORIGIN_KEYS")]
    out: dict[str, dict[str, str]] = {}
    for key in ("created", "imported", "edited"):
        chunk = body[body.index(f"  {key}: {{"):]
        chunk = chunk[:chunk.index("\n  },")]
        out[key] = dict(re.findall(r'(light|dark|textLight):\s*"(#[0-9a-fA-F]{6})"', chunk))
    return out


def test_the_sanity_check_of_the_measurement_itself():
    """A contrast function that always returns a big number would pass every
    test below without noticing anything."""
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast("#ffffff", "#ffffff"), 1) == 1.0


def test_every_origin_LABEL_colour_is_readable_as_text():
    """This is the assertion the app was failing: "CREATED" at 10px in brand
    terracotta measured 3.44:1 on the light canvas."""
    for key, colors in _origin_colors().items():
        fg = colors.get("textLight") or colors["light"]
        for ground, name in ((LIGHT_CANVAS, "canvas"), (LIGHT_PAPER, "paper")):
            ratio = contrast(fg, ground)
            assert ratio >= TEXT_FLOOR, (
                f"origin '{key}' label {fg} is {ratio:.2f}:1 on the light {name} "
                f"— under the {TEXT_FLOOR}:1 floor for small text"
            )


def test_the_origin_RAIL_keeps_the_brand_colour():
    """The step-down is text-only on purpose. If `light` ever equals
    `textLight`, the Library lost its brand terracotta to an a11y fix that
    should not have touched it."""
    created = _origin_colors()["created"]
    assert created["light"] == "#c96442", "the provenance rail must stay brand terracotta"
    assert created["textLight"] != created["light"]
    assert contrast(created["light"], LIGHT_CANVAS) >= DECORATION_FLOOR


def test_a_filled_success_chip_is_readable():
    """White on the palette's success.main is 3.30:1. Fixed on the CHIP, not
    the palette — success.main is also drawn as text and icons on the dark
    canvas, where darkening it would make those worse."""
    theme = (FRONTEND / "theme.js").read_text()
    chip = theme[theme.index("MuiChip: {"):theme.index("MuiTextField: {")]
    m = re.search(r'filledSuccess:\s*\{\s*backgroundColor:\s*"(#[0-9a-fA-F]{6})"', chip)
    assert m, "MuiChip.filledSuccess override is gone — the chip is back to 3.30:1"
    assert contrast("#ffffff", m.group(1)) >= TEXT_FLOOR

    # And the palette entry itself is untouched, which is the whole point.
    assert '"#16a34a"' in theme or "#16a34a" in theme


def test_segmented_controls_have_one_home_in_the_theme():
    """Without a theme entry every call site styled itself and the same widget
    rendered several ways on one screen."""
    theme = (FRONTEND / "theme.js").read_text()
    assert "MuiToggleButtonGroup: {" in theme
    assert "MuiToggleButton: {" in theme
