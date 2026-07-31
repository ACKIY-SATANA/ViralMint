# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/config — serve app config defaults to frontend."""
from fastapi import APIRouter, HTTPException

router = APIRouter()

_DEFAULTS = {
    "model_registry": {
        # Models verified May 2026. Anthropic / OpenAI defaults pick the
        # best price/perf tier; OpenRouter defaults to a premium model
        # because BYOK users routing through OpenRouter typically want
        # access to the top tier without juggling multiple provider keys.
        "anthropic": {
            "default_model": "claude-sonnet-4-6",
            "models": [
                "claude-haiku-4-5",
                "claude-sonnet-4-6",
                "claude-opus-4-6",
                "claude-opus-4-7",
            ],
        },
        "openai": {
            "default_model": "gpt-5.4-mini",
            "models": [
                "gpt-5.4-nano",
                "gpt-5.4-mini",
                "gpt-5.4",
                "gpt-5.5",
            ],
        },
        # OpenRouter slugs use the vendor/model format. Verified live
        # against https://openrouter.ai/api/v1/models on 2026-05-07. If
        # you want a model that isn't here, OpenRouter accepts any of its
        # published slugs — but the dropdown only exposes this curated
        # set so users don't have to guess.
        "openrouter": {
            "default_model": "anthropic/claude-opus-4.7",
            "models": [
                "anthropic/claude-opus-4.7",
                "anthropic/claude-opus-4.6-fast",
                "anthropic/claude-sonnet-4.6",
                "openai/gpt-5.5",
                "openai/gpt-5.4",
                "openai/gpt-5.4-mini",
                "google/gemini-3.1-pro-preview",
                "google/gemini-3.1-flash-lite",
            ],
        },
    },
    "tts_providers": {
        "edge_tts":   {"label": "Edge TTS — Free",          "cost_1k": 0.0,   "requires_key": False},
        "openai_tts": {"label": "OpenAI TTS — Standard",    "cost_1k": 0.015, "requires_key": True},
    },
    # Mirrors caption_service.CAPTION_STYLES (minus `brainrot`, which is a
    # pipeline look rather than a picker option) plus the "none" sentinel.
    # This list used to stop at the original three, so Smart Video offered 3 of
    # the engine's 10 styles. tests/test_caption_styles_parity.py pins it.
    "caption_styles": {
        "viral":   {"label": "Viral — yellow word highlight",   "words_per_group": 6},
        "classic": {"label": "Classic — full sentence",         "words_per_group": 8},
        "bold":    {"label": "Bold — green word highlight",     "words_per_group": 4},
        "neon":    {"label": "Neon — pink text, yellow pop",    "words_per_group": 6},
        "minimal": {"label": "Minimal — plain, no highlight",   "words_per_group": 10},
        "karaoke": {"label": "Karaoke — words light up",        "words_per_group": 6},
        "glow":    {"label": "Glow — soft orange glow",         "words_per_group": 6},
        "urban":   {"label": "Bold Urban — punchy bursts",      "words_per_group": 3},
        "warm":    {"label": "Warm Glow — serif, cream tone",   "words_per_group": 6},
        "mono":    {"label": "Monochrome — understated grey",   "words_per_group": 6},
        "none":    {"label": "No captions"},
    },
}


@router.get("/config/{key}")
async def get_config(key: str):
    """Return config for a given key."""
    value = _DEFAULTS.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Unknown config key: {key}")
    return {"key": key, "value": value}
