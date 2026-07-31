# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Every surface that offers caption styles must offer the engine's styles.

They drifted: `caption_service.CAPTION_STYLES` grew from three presets to
eleven, but `/api/tools/captions` still validated
`Literal["viral","classic","bold"]`, the Captions page listed three, Clip
Studio listed three, and `/api/config/caption_styles` (which drives Smart
Video) listed three. So seven of the renderer's styles were unreachable, and
posting one came back 422 from the tool built to apply them.

`brainrot` is deliberately excluded everywhere: it is a look a pipeline
applies, not a preset a user picks in these places.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.api.config import _DEFAULTS
from backend.api.tools import _CAPTION_STYLE_IDS
from backend.services.caption_service import CAPTION_STYLES

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
ENGINE_IDS = {k for k in CAPTION_STYLES if k != "brainrot"}


def _js_values(path: Path, const: str) -> set[str]:
    """Pull the `value: "..."` entries out of a JS array literal."""
    src = path.read_text()
    start = src.index(f"export const {const} = [")
    end = src.index("]", start)
    return set(re.findall(r'value:\s*"([^"]+)"', src[start:end]))


def test_the_engine_still_has_the_styles_this_test_is_about():
    """A sanity floor: if someone deletes styles, the parity assertions below
    would pass vacuously."""
    assert len(ENGINE_IDS) >= 10
    assert {"viral", "classic", "bold", "neon", "karaoke"} <= ENGINE_IDS


def test_the_captions_endpoint_accepts_every_engine_style():
    assert set(_CAPTION_STYLE_IDS) == ENGINE_IDS


def test_the_shared_frontend_list_matches_the_engine():
    values = _js_values(FRONTEND / "components" / "tools" / "captionOptions.js",
                        "CAPTION_STYLES")
    assert values == ENGINE_IDS


def test_remote_config_matches_the_engine_plus_the_none_sentinel():
    served = set(_DEFAULTS["caption_styles"])
    assert served == ENGINE_IDS | {"none"}


def test_clip_studio_offers_the_shared_list_not_a_copy():
    """Clip Studio builds its chips from the shared module — a hardcoded array
    here is how it fell out of sync last time."""
    src = (FRONTEND / "pages" / "ClipStudio.jsx").read_text()
    assert "captionOptions" in src
    assert "CAPTION_STYLES.map" in src
