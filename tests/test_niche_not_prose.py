# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""`GeneratedVideo.niche` must hold a niche, not a paragraph.

The Smart Video pipeline stored the analyzer's `topic_angle`
in this column. That field is prose BY DESIGN — the analyzer prompt asks for
"What makes this specific angle work" — so the column ended up holding 250-450
char sentences ("The specific angle works because it connects two major
geopolitical concerns…") in a column meant for a search keyword. The column is
String(200) but SQLite doesn't enforce VARCHAR length, so nothing complained.

The writer — generator._resolve_row_niche — resolves a real short niche or
None, and never falls back to topic_angle.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.models.generated_video import NICHE_MAX_CHARS

PROSE = ("The specific angle works because it connects two major geopolitical "
         "concerns (Iran sanctions and oil markets) that the audience already "
         "cares about, then reframes them through a single human story.")


@pytest.fixture(scope="module", autouse=True)
def _init_schema():
    from backend.database import init_db
    asyncio.run(init_db())


class TestSharedConstant:
    def test_is_long_enough_for_a_real_niche(self):
        """Must not reject legitimately long multi-word niches."""
        assert NICHE_MAX_CHARS >= 100
        for real in ("stoicism",
                     "personal finance for gen z",
                     "traditional chinese culture and philosophy explained"):
            assert len(real) <= NICHE_MAX_CHARS, real

    def test_still_excludes_the_prose_that_caused_the_bug(self):
        """The two observed bad rows were 252 and 411 chars."""
        assert len(PROSE) > NICHE_MAX_CHARS


class TestWriter:
    """generator._resolve_row_niche"""

    def _resolve(self, source):
        from backend.agents.generator import GeneratorAgent
        return asyncio.run(GeneratorAgent()._resolve_row_niche(source))

    def test_none_when_there_is_no_source(self):
        assert self._resolve(None) is None

    def test_uses_the_sources_own_niche(self):
        src = type("S", (), {"niche": "stoicism", "scout_result_id": None})()
        assert self._resolve(src) == "stoicism"

    def test_strips_whitespace(self):
        src = type("S", (), {"niche": "  stoicism \n", "scout_result_id": None})()
        assert self._resolve(src) == "stoicism"

    def test_falls_back_to_the_scout_query(self):
        from backend.database import AsyncSessionLocal
        from backend.models.scout_result import ScoutResult
        from sqlalchemy import delete

        # Idempotent seed: the dev DB is shared across runs, so a plain insert
        # collides on the PK the second time the suite executes.
        async def seed():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(ScoutResult).where(ScoutResult.id == "sr-niche-1"))
                db.add(ScoutResult(id="sr-niche-1", user_id="local",
                                   platform="youtube", niche="ancient philosophy",
                                   title="t", video_id="vid-1", video_url="u",
                                   created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
                await db.commit()

        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(ScoutResult).where(ScoutResult.id == "sr-niche-1"))
                await db.commit()

        asyncio.run(seed())
        try:
            src = type("S", (), {"niche": None, "scout_result_id": "sr-niche-1"})()
            assert self._resolve(src) == "ancient philosophy"
        finally:
            asyncio.run(cleanup())

    def test_none_when_the_scout_row_is_missing(self):
        src = type("S", (), {"niche": None, "scout_result_id": "does-not-exist"})()
        assert self._resolve(src) is None

    def test_discards_prose_rather_than_storing_it(self):
        """The actual regression: a paragraph must never reach the column."""
        src = type("S", (), {"niche": PROSE, "scout_result_id": None})()
        assert self._resolve(src) is None

    def test_never_reads_topic_angle(self):
        """`_get_search_demand_section` may fall back to topic_angle; the row
        niche resolver must NOT — that fallback is what caused the bug."""
        import inspect
        from backend.agents.generator import GeneratorAgent
        src = inspect.getsource(GeneratorAgent._resolve_row_niche)
        assert "topic_angle" not in src.split('"""')[2]  # body, not the docstring
