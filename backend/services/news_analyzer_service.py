# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
AI-powered news analysis engine for ViralMint.
Analyzes articles one-at-a-time in parallel for reliability.
"""
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Concurrency limit for parallel AI calls (avoid rate limiting)
AI_ANALYSIS_SEMAPHORE = 3

SINGLE_ARTICLE_PROMPT = """You are a viral content strategist. Analyze this news article for video creation potential.

User is searching for: "{query}"

Title: {title}
Source: {source_domain}
Published: {published_at}

Article text:
{full_text}

1. FILTER: Is this article relevant to the query? If it's completely off-topic — set virality_score to 0. Note: if only a title/headline is provided (no full text), score based on the headline's news value — do NOT penalize for missing text.
2. SCORE: Rate viral video potential 0-100 based on: controversy, novelty, emotional impact, broad appeal, timeliness. Be generous — most real news from reputable sources should score 30-60+. Only score 0 for completely irrelevant articles.
3. ANALYZE: Provide deep analysis useful for creating a video.

Return ONLY this JSON object, no explanation or markdown fences:

{{
  "virality_score": 0-100,
  "why_trending": "why this story matters right now",
  "hook": "attention-grabbing video opening (under 15 words)",
  "video_format": "news_explainer|hot_take|reaction|weekly_recap|deep_dive|story_time",
  "suggested_angle": "unique perspective for a video",
  "talking_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "key_quotes": ["notable quotes worth citing"],
  "emotional_tone": "urgent|shocking|inspiring|informative|controversial|entertaining",
  "suggested_title": "click-worthy video title (under 60 chars)",
  "suggested_hashtags": ["#Tag1", "#Tag2", "#Tag3", "#Tag4"],
  "target_audience": "who would watch this video",
  "related_context": "background context that adds depth to the story"
}}"""


async def _analyze_one(
    article: dict,
    query: str,
    sem: asyncio.Semaphore,
    user_settings=None,
) -> dict:
    """
    Analyze a single article with retry. Returns the article enriched with analysis.
    Runs under a semaphore to limit concurrent API calls.
    """
    from backend.core.ai_provider import get_ai_client

    text = article.get("full_text") or article.get("summary") or ""
    title = article.get("title", "")
    if not text and not title:
        article["virality_score"] = 0
        article["analysis"] = {"error": "No article text available"}
        return article

    # Headline-only is a normal outcome, not a failure: Google News links go
    # through a redirector whose target often can't be resolved, so the scraper
    # comes back with a title and nothing else. Scoring those zero threw away
    # most of a news scout's results. The prompt is told not to penalise a
    # missing body so a strong headline can still score.
    if not text:
        text = title

    prompt = SINGLE_ARTICLE_PROMPT.format(
        query=query,
        title=article.get("title", "Unknown"),
        source_domain=article.get("source_domain", "Unknown"),
        published_at=article.get("published_at", "Unknown"),
        full_text=text[:3000],
    )

    async with sem:
        last_error = None
        for attempt in range(2):
            try:
                ai = get_ai_client(user_settings)
                response = await ai.chat(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1024,
                )

                cleaned = response.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                analysis = json.loads(cleaned)

                article["virality_score"] = analysis.pop("virality_score", 50)
                article["analysis"] = analysis
                return article

            except json.JSONDecodeError as e:
                logger.warning("Failed to parse AI analysis for '%s': %s",
                               article.get("title", "?")[:60], e)
                article["virality_score"] = 30
                article["analysis"] = {"error": "AI returned invalid JSON"}
                return article
            except Exception as e:
                last_error = e
                is_transient = any(k in str(e).lower() for k in ("503", "502", "timeout", "overloaded"))
                if is_transient and attempt == 0:
                    logger.warning("Analysis transient error for '%s' (retry in 3s): %s",
                                   article.get("title", "?")[:60], e)
                    await asyncio.sleep(3)
                    continue
                logger.error("Analysis failed for '%s': %s",
                             article.get("title", "?")[:60], e)
                article["virality_score"] = 0
                article["analysis"] = {"error": str(e)}
                return article

        # Exhausted retries
        article["virality_score"] = 0
        article["analysis"] = {"error": str(last_error)}
        return article


async def analyze_articles(
    articles: list[dict],
    query: str,
    user_settings=None,
) -> list[dict]:
    """
    AI quality filter + deep analysis — one article per API call, run in parallel.
    Returns only articles that pass the quality threshold (score >= 25).
    """
    if not articles:
        return []

    sem = asyncio.Semaphore(AI_ANALYSIS_SEMAPHORE)

    tasks = [
        _analyze_one(article, query, sem, user_settings)
        for article in articles
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect successfully analyzed articles
    enriched = []
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            logger.error("Unexpected analysis error: %s", r)
            errors += 1
            continue
        if isinstance(r, dict) and r.get("virality_score", 0) >= 25:
            enriched.append(r)

    if errors == len(articles):
        # All failed — raise so caller can report meaningful error
        raise RuntimeError(f"All {len(articles)} article analyses failed")

    logger.info("Analyzed %d articles: %d passed filter, %d errors",
                len(articles), len(enriched), errors)

    # Sort by score descending
    enriched.sort(key=lambda a: a.get("virality_score", 0), reverse=True)
    return enriched


async def analyze_single_article(
    article: dict,
    user_settings=None,
) -> dict:
    """
    Deep analysis of a single article (for direct URL mode).
    Returns the article enriched with analysis.
    """
    sem = asyncio.Semaphore(1)  # No concurrency needed for single article
    return await _analyze_one(article, "", sem, user_settings)
