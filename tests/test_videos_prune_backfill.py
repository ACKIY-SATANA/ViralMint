# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""`GET /api/videos` must return a FULL page even when it prunes orphans.

The self-heal prune (rows whose video_path file is gone) used to run *within*
the fetched page and return the page short. With enough stale rows the whole
page was consumed, so `list_videos(limit=3)` answered `videos: []` alongside
`total: 431` — which reads as a bug to any caller and gives it no way forward.

The backfill re-queries after committing the deletes. The subtle part is the
offset: rows kept so far occupy `[offset, offset + len(live))` in the *post-delete*
ordering, so a later pass must start after them — re-using the original offset
would duplicate what's already collected.

ISOLATION: every row is seeded with a private `source_type` and every read
passes `source_type=` (which the endpoint filters on, count included), so these
tests see only their own rows. Cleanup deletes by that marker. An earlier
revision used an autouse `delete(GeneratedVideo)` with no WHERE clause and wiped
the dev library — never wipe a whole table in a test; the DB is shared.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MARKER = "__test_prune_backfill__"


@pytest.fixture(scope="module", autouse=True)
def _init_schema():
    from backend.database import init_db
    asyncio.run(init_db())


def _wipe_marked() -> None:
    """Delete ONLY rows this module created."""
    from backend.database import AsyncSessionLocal
    from backend.models.generated_video import GeneratedVideo
    from sqlalchemy import delete

    async def go():
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(GeneratedVideo).where(GeneratedVideo.source_type == MARKER))
            await db.commit()
    asyncio.run(go())


@pytest.fixture(autouse=True)
def _only_our_rows():
    _wipe_marked()
    yield
    _wipe_marked()


async def _insert(vid: str, video_path: str | None, *, ago_seconds: int) -> None:
    from backend.database import AsyncSessionLocal
    from backend.models.generated_video import GeneratedVideo
    async with AsyncSessionLocal() as db:
        db.add(GeneratedVideo(
            id=f"{MARKER}-{vid}", user_id="local", title=vid, status="ready",
            aspect_ratio="9:16", video_path=video_path, source_type=MARKER,
            created_at=(datetime.now(timezone.utc)
                        - timedelta(seconds=ago_seconds)).replace(tzinfo=None),
        ))
        await db.commit()


def _seed(tmp_path: Path, spec: list[tuple[str, bool]]) -> None:
    """spec = [(id, file_exists_on_disk)] in newest-first order."""
    async def go():
        for i, (vid, exists) in enumerate(spec):
            p = tmp_path / f"{vid}.mp4"
            if exists:
                p.write_bytes(b"\x00" * 16)
            await _insert(vid, str(p), ago_seconds=i)
    asyncio.run(go())


def _list(**kw) -> dict:
    """Call the endpoint directly, scoped to our marker.

    Every filter is passed explicitly: an un-passed `Query()`-defaulted param
    arrives as the Query sentinel (not None) when the function is called
    outside FastAPI. Over HTTP FastAPI resolves the defaults, so that's a
    harness detail, not endpoint behaviour.
    """
    from backend.api.videos import list_videos
    from backend.database import AsyncSessionLocal

    params = {"status": None, "source_type": MARKER, "limit": 20, "offset": 0}
    params.update(kw)

    async def go():
        async with AsyncSessionLocal() as db:
            return await list_videos(db=db, **params)
    return asyncio.run(go())


def _ids(out: dict) -> list[str]:
    return [v["id"].removeprefix(f"{MARKER}-") for v in out["videos"]]


class TestBackfill:
    def test_full_page_when_the_head_is_all_orphans(self, tmp_path):
        """The regression: leading orphans used to eat the whole page."""
        _seed(tmp_path, [("v1", False), ("v2", False), ("v3", True),
                         ("v4", True), ("v5", True)])
        out = _list(limit=3)
        assert _ids(out) == ["v3", "v4", "v5"]
        assert out["total"] == 3          # reflects the pruned set


    def test_no_duplicates_across_backfill_passes(self, tmp_path):
        """Guards the offset arithmetic — re-using the original offset on pass 2
        would re-emit the rows already kept."""
        _seed(tmp_path, [("a", True), ("b", False), ("c", True),
                         ("d", False), ("e", True), ("f", True)])
        ids = _ids(_list(limit=4))
        assert ids == ["a", "c", "e", "f"]
        assert len(ids) == len(set(ids)), f"duplicates: {ids}"

    def test_orphans_are_actually_deleted(self, tmp_path):
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        from sqlalchemy import select

        _seed(tmp_path, [("g1", False), ("g2", True)])
        _list(limit=10)

        async def remaining():
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(GeneratedVideo.id)
                    .where(GeneratedVideo.source_type == MARKER))).scalars().all()
                return [r.removeprefix(f"{MARKER}-") for r in rows]
        assert asyncio.run(remaining()) == ["g2"]

    def test_offset_page_is_correct_after_pruning(self, tmp_path):
        """Rows before `offset` are untouched, so page 2 stays coherent."""
        _seed(tmp_path, [("p1", True), ("p2", True), ("p3", False),
                         ("p4", True), ("p5", True)])
        ids = _ids(_list(limit=2, offset=2))
        assert ids == ["p4", "p5"]
        assert len(ids) == len(set(ids))

    def test_short_last_page_is_not_an_infinite_loop(self, tmp_path):
        """Fewer rows than `limit` must terminate, not spin."""
        _seed(tmp_path, [("s1", True), ("s2", True)])
        assert _ids(_list(limit=50)) == ["s1", "s2"]

    def test_all_orphans_yields_empty_but_terminates(self, tmp_path):
        _seed(tmp_path, [("z1", False), ("z2", False), ("z3", False)])
        out = _list(limit=10)
        assert out["videos"] == []
        assert out["total"] == 0          # honest: nothing left, not "3 hidden"

    def test_pass_cap_bounds_the_work(self, tmp_path):
        """A long tail of orphans must not scan forever in one request."""
        _seed(tmp_path, [(f"m{i}", False) for i in range(40)] + [("keep", True)])
        out = _list(limit=2)
        # Either it reached `keep` within the cap or stopped early — both fine;
        # spinning is not. It must return, and must not invent rows.
        assert isinstance(out["videos"], list)
        assert out["total"] <= 41

    def test_rows_without_a_path_are_left_alone(self, tmp_path):
        """A draft/failed row with no file yet is legitimately path-less."""
        asyncio.run(_insert("draft", None, ago_seconds=0))
        assert _ids(_list(limit=10)) == ["draft"]

    def test_filters_still_apply_through_the_backfill(self, tmp_path):
        """status= narrows correctly even when the page had to backfill."""
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        from sqlalchemy import select

        _seed(tmp_path, [("f1", False), ("f2", True), ("f3", True)])

        async def mark_draft():
            async with AsyncSessionLocal() as db:
                row = (await db.execute(
                    select(GeneratedVideo).where(
                        GeneratedVideo.id == f"{MARKER}-f3"))).scalar_one()
                row.status = "draft"
                await db.commit()
        asyncio.run(mark_draft())

        out = _list(limit=5, status="ready")
        assert _ids(out) == ["f2"]
        assert out["total"] == 1
