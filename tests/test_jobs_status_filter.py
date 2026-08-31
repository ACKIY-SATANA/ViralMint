# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""`GET /api/jobs?status=` takes a comma-separated list.

The frontend's active-job restore wants exactly `running` and `pending`, and
was issuing one request per status on every page load. One filter answers
both.

Two properties worth pinning, because breaking either is silent:
  - a SINGLE status still means what it always did (every other caller), and
  - `total` is computed from the same filters as the rows. Those two used to be
    written out separately, which is the exact shape where a filter added to
    one makes the count disagree with the page.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.api.jobs import list_jobs
from backend.database import init_db, AsyncSessionLocal
from backend.models.job import Job


PREFIX = "statusfilter-probe-"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


@pytest.fixture
def probe_jobs():
    """Three rows, one per status, tagged so they can be told from real data."""
    made = []

    async def _make():
        async with AsyncSessionLocal() as db:
            for st in ("running", "pending", "completed"):
                j = Job(id=str(uuid.uuid4()), job_type=f"{PREFIX}{st}",
                        status=st, title=f"{PREFIX}{st}")
                db.add(j)
                made.append(j.id)
            await db.commit()

    async def _drop():
        async with AsyncSessionLocal() as db:
            for jid in made:
                row = await db.get(Job, jid)
                if row:
                    await db.delete(row)
            await db.commit()

    asyncio.run(_make())
    yield made
    asyncio.run(_drop())


def _mine(payload):
    return [j for j in payload["jobs"] if str(j.get("job_type", "")).startswith(PREFIX)]


@pytest.mark.asyncio
async def test_a_single_status_still_filters_to_that_status(probe_jobs):
    out = await list_jobs(status="running", limit=200)
    mine = _mine(out)
    assert len(mine) == 1
    assert mine[0]["status"] == "running"


@pytest.mark.asyncio
async def test_a_comma_list_returns_every_named_status(probe_jobs):
    out = await list_jobs(status="running,pending", limit=200)
    got = sorted(j["status"] for j in _mine(out))
    assert got == ["pending", "running"]


@pytest.mark.asyncio
async def test_a_comma_list_excludes_everything_else(probe_jobs):
    """The point of the filter is that `completed` does NOT come back — the
    restore reconciles zombies against this list, so a stray terminal row
    would keep a finished job pinned in the banner."""
    out = await list_jobs(status="running,pending", limit=200)
    assert all(j["status"] != "completed" for j in _mine(out))


@pytest.mark.asyncio
async def test_whitespace_and_empty_entries_are_tolerated(probe_jobs):
    out = await list_jobs(status=" running , , pending ", limit=200)
    assert sorted(j["status"] for j in _mine(out)) == ["pending", "running"]


@pytest.mark.asyncio
async def test_no_status_is_unfiltered(probe_jobs):
    out = await list_jobs(limit=200)
    assert len(_mine(out)) == 3


@pytest.mark.asyncio
async def test_total_is_computed_from_the_same_filters_as_the_rows(probe_jobs):
    """`total` drives "showing the latest N of M". Computed off a different
    filter set, it silently overstates — the page says 2 rows of 500."""
    everything = await list_jobs(limit=500)
    running_only = await list_jobs(status="running", limit=500)
    both = await list_jobs(status="running,pending", limit=500)

    assert running_only["total"] < everything["total"]
    assert running_only["total"] <= both["total"] <= everything["total"]
    # With a limit that covers the table, total must equal the rows returned.
    for payload in (everything, running_only, both):
        if len(payload["jobs"]) < 500:
            assert payload["total"] == len(payload["jobs"])


@pytest.mark.asyncio
async def test_an_unknown_status_matches_nothing(probe_jobs):
    out = await list_jobs(status="no-such-status", limit=200)
    assert _mine(out) == []
