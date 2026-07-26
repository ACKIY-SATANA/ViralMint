# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Staleness-aware zombie-job sweep + restart-handoff watcher.

The boot sweep used to fail EVERY running/pending job, on the assumption that a
restart implies the previous process is gone. That is false: uvicorn frees its
listening socket at the START of graceful shutdown, so during a launcher
port-takeover a NEW backend boots while the OLD one is still draining
background jobs. The sweep would stamp a still-completing generation "Server
restarted — job did not complete"; the job then finished and last-write-won, so
the user saw a failure toast for a video that actually landed in the Library.
The sharp edge is a trusting retry redoing all the work.

Now every accepted update_job_status call touches Job.updated_at (progress
ticks route through it via ws_manager.send_progress), the boot sweep fails only
STALE jobs, and a lifespan watcher re-checks the fresh survivors until they
finish or go stale. These tests pin each piece.

Ported from the SaaS variant. DB-backed: rows use unique per-test user ids and
are deleted in a finally block.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from backend.database import init_db, AsyncSessionLocal, sweep_stale_jobs
from backend.models.job import Job
from backend.agents.job_helper import create_job, update_job_status


@pytest.fixture(scope="module", autouse=True)
def _db():
    asyncio.run(init_db())


async def _get(job_id: str) -> Job:
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _backdate(job_id: str, seconds: int | None):
    """Set updated_at to `seconds` ago (None → NULL, i.e. a pre-migration row)."""
    from sqlalchemy import update
    async with AsyncSessionLocal() as db:
        val = None if seconds is None else datetime.utcnow() - timedelta(seconds=seconds)
        await db.execute(update(Job).where(Job.id == job_id).values(updated_at=val))
        await db.commit()


async def _cleanup(job_ids: list[str]):
    from sqlalchemy import delete
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Job).where(Job.id.in_(job_ids)))
        await db.commit()


async def _mk_running(uid_tag: str) -> str:
    job = await create_job("generate", user_id=f"t_{uid_tag}_{uuid.uuid4().hex[:8]}")
    await update_job_status(job.id, "running", progress_pct=5, current_step="working")
    return job.id


class TestHeartbeat:
    def test_update_job_status_touches_updated_at(self):
        async def run():
            jid = await _mk_running("hb")
            try:
                first = (await _get(jid)).updated_at
                assert first is not None
                await update_job_status(jid, "running", progress_pct=6)
                assert (await _get(jid)).updated_at >= first
                # A REJECTED progress regression must still heartbeat — the
                # explicit touch in update_job_status carries it, not `onupdate`
                # (which never fires when no column actually changes).
                mid = (await _get(jid)).updated_at
                await update_job_status(jid, "running", progress_pct=1)  # regression → pct kept
                after = await _get(jid)
                assert after.progress_pct == 6.0
                assert after.updated_at >= mid
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_terminal_job_is_not_resurrected_by_a_late_progress_tick(self):
        # ws_manager.send_progress writes status="running"; a tick that lands
        # after completion must not revive the row (and hand the sweep a fresh
        # heartbeat for a job nobody is running).
        async def run():
            jid = await _mk_running("term")
            try:
                await update_job_status(jid, "success")
                await update_job_status(jid, "running", progress_pct=99,
                                        current_step="late tick")
                job = await _get(jid)
                assert job.status == "success"
                assert job.current_step != "late tick"
            finally:
                await _cleanup([jid])
        asyncio.run(run())


class TestSweep:
    def test_stale_running_job_is_swept(self):
        async def run():
            jid = await _mk_running("stale")
            await _backdate(jid, 600)
            try:
                swept, fresh = await sweep_stale_jobs(grace_seconds=180, only_ids=[jid])
                assert swept == 1 and fresh == []
                job = await _get(jid)
                assert job.status == "failed"
                assert "Server restarted" in job.error_message
                assert job.completed_at is not None
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_fresh_running_job_survives_sweep(self):
        # The regression this whole change exists for: a job a draining
        # predecessor is still heartbeating must NOT be failed at boot.
        async def run():
            jid = await _mk_running("fresh")
            try:
                swept, fresh = await sweep_stale_jobs(grace_seconds=180, only_ids=[jid])
                assert swept == 0 and fresh == [jid]
                assert (await _get(jid)).status == "running"
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_null_heartbeat_counts_as_stale(self):
        # Rows written before the updated_at column existed (the migration adds
        # it without backfill) keep the old always-sweep behaviour.
        async def run():
            jid = await _mk_running("null")
            await _backdate(jid, None)
            try:
                swept, _ = await sweep_stale_jobs(grace_seconds=180, only_ids=[jid])
                assert swept == 1
                assert (await _get(jid)).status == "failed"
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_only_ids_scopes_the_sweep(self):
        # The handoff watcher must never touch jobs outside its boot snapshot
        # (i.e. jobs the CURRENT instance created).
        async def run():
            watched = await _mk_running("scope_w")
            unwatched = await _mk_running("scope_u")
            await _backdate(watched, 600)
            await _backdate(unwatched, 600)
            try:
                swept, _ = await sweep_stale_jobs(grace_seconds=180, only_ids=[watched])
                assert swept == 1
                assert (await _get(watched)).status == "failed"
                assert (await _get(unwatched)).status == "running"
            finally:
                await _cleanup([watched, unwatched])
        asyncio.run(run())

    def test_empty_only_ids_is_a_no_op(self):
        # Guards the watcher's exit condition: an empty watch-set must sweep
        # nothing, NOT fall through to "every running job".
        async def run():
            jid = await _mk_running("empty")
            await _backdate(jid, 600)
            try:
                swept, fresh = await sweep_stale_jobs(grace_seconds=180, only_ids=[])
                assert swept == 0 and fresh == []
                assert (await _get(jid)).status == "running"
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_success_overwrites_a_swept_failed(self):
        # Self-heal: a draining predecessor's final success must land even after
        # a mis-sweep (terminal→terminal transitions are allowed).
        async def run():
            jid = await _mk_running("heal")
            await _backdate(jid, 600)
            try:
                await sweep_stale_jobs(grace_seconds=180, only_ids=[jid])
                assert (await _get(jid)).status == "failed"
                await update_job_status(jid, "success", output_data={"ok": True})
                assert (await _get(jid)).status == "success"
            finally:
                await _cleanup([jid])
        asyncio.run(run())


class TestHandoffWatcher:
    def test_watcher_sweeps_job_when_heartbeat_goes_stale(self):
        async def run():
            from backend.main import _watch_handoff_jobs
            # Clean field: the watcher snapshots ALL running/pending rows, so
            # fail any leftovers first (the same outcome the pre-fix boot sweep
            # gave them) — otherwise a stray row keeps the watcher alive past
            # the wait_for timeout.
            await sweep_stale_jobs(grace_seconds=0)
            jid = await _mk_running("watch")
            try:
                task = asyncio.create_task(_watch_handoff_jobs(poll_seconds=0.05))
                await asyncio.sleep(0.2)           # several passes; fresh → survives
                assert (await _get(jid)).status == "running"
                await _backdate(jid, 600)          # predecessor "died" — heartbeat frozen
                await asyncio.wait_for(task, timeout=10)
                assert (await _get(jid)).status == "failed"
            finally:
                await _cleanup([jid])
        asyncio.run(run())

    def test_watcher_exits_when_job_completes(self):
        async def run():
            from backend.main import _watch_handoff_jobs
            await sweep_stale_jobs(grace_seconds=0)
            jid = await _mk_running("watch_done")
            try:
                task = asyncio.create_task(_watch_handoff_jobs(poll_seconds=0.05))
                await asyncio.sleep(0.1)
                await update_job_status(jid, "success")   # predecessor finished draining
                await asyncio.wait_for(task, timeout=10)
                assert (await _get(jid)).status == "success"
            finally:
                await _cleanup([jid])
        asyncio.run(run())
