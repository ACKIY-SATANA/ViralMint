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


def test_clip_surfaces_derive_their_styles_and_share_one_default():
    """/clips has TWO surfaces that burn captions, and they must agree.

    History: the extract dialog carried its own literal list, so most of the
    engine's styles were unreachable there — fixed by deriving from the shared
    captionOptions list. Then the cutting bench arrived with a second copy of
    the same four per-clip controls, and a style chosen on one surface did
    nothing on the other: whichever you happened to finish in decided the
    render.

    Both derive their chips from CAPTION_STYLES now, and the DEFAULT lives
    once in useClipSettings — which ClipStudio owns and hands to both, so
    there is exactly one caption_style in play at any moment.
    """
    dialog = (FRONTEND / "components" / "clip" / "ExtractDialog.jsx").read_text()
    bench = (FRONTEND / "components" / "clip" / "bench" / "SourceBench.jsx").read_text()
    shared = (FRONTEND / "components" / "clip" / "useClipSettings.js").read_text()

    # Neither surface may carry its own style ids.
    for name, src in (("ExtractDialog", dialog), ("SourceBench", bench)):
        assert "captionOptions" in src, f"{name} no longer derives from the shared list"
        assert "CAPTION_STYLES" in src, name

    # The off-state wire value has ONE home. The backend's captions_disabled
    # contract keys on it, and it must be intercepted before the ASS builder
    # (an unknown id falls back to `viral`, which is how Clipper's "none" chip
    # once burned full viral subtitles).
    assert 'caption_style: "none"' in shared, "the captions-off default left useClipSettings"
    assert 'caption_style: "none"' not in dialog, "ExtractDialog grew its own default again"
    assert 'caption_style: "none"' not in bench, "SourceBench grew its own default again"

    # And both read the shared object rather than local state.
    assert "settings.caption_style" in dialog
    assert "settings.caption_style" in bench
