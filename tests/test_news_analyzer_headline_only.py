# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""A headline-only article must be scored, not discarded.

Google News links go through a redirector whose target frequently can't be
resolved, so the scraper returns a title and no body. Treating that as "no
article text available" scored it zero and dropped it — which is most of a
news scout's results on some queries. It now falls back to the headline, and
the prompt is told not to penalise the missing body.

A genuinely empty article — no text AND no title — is still a real failure and
still scores zero.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncio
import pytest

from backend.services import news_analyzer_service as nas


class _FakeAI:
    """Stands in for the user's AI client; records the prompt it was given."""

    def __init__(self, store):
        self.store = store

    async def chat(self, messages, **kwargs):
        self.store["prompt"] = messages[0]["content"]
        return '{"virality_score": 55, "angle": "x", "hook": "y"}'


@pytest.mark.asyncio
async def test_headline_only_article_is_still_analyzed():
    store = {}
    article = {"title": "Bank collapses overnight", "source_domain": "news.example"}

    with patch("backend.core.ai_provider.get_ai_client",
               return_value=_FakeAI(store)):
        out = await nas._analyze_one(
            article, "finance", asyncio.Semaphore(1), None,
        )

    assert out.get("analysis", {}).get("error") != "No article text available"
    assert "Bank collapses overnight" in store.get("prompt", ""), \
        "the headline should have been used as the article text"


@pytest.mark.asyncio
async def test_article_with_neither_text_nor_title_still_fails():
    article = {"source_domain": "news.example"}
    out = await nas._analyze_one(
        article, "finance", asyncio.Semaphore(1), None,
    )
    assert out["virality_score"] == 0
    assert out["analysis"]["error"] == "No article text available"


def test_the_prompt_tells_the_model_not_to_penalize_a_missing_body():
    """Without this, the model scores a headline-only item low on 'no
    substance' — the fallback would feed it text it then punishes."""
    assert "do NOT penalize for missing text" in nas.SINGLE_ARTICLE_PROMPT
