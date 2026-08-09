# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Clipper resilience — cancellation, orphaned files, and a wasted Whisper run.

Three ported fixes are locked in here.

1. Cancellation was cosmetic. DELETE /api/jobs/{id} (and the WS job_cancel
   message) flips the Job row to "cancelled", but nothing interrupted the
   running coroutine — a cancelled extraction kept burning Whisper, the AI
   selection call and N parallel ffmpeg re-encodes, saved every clip, and its
   final "success" write overwrote "cancelled" (updates AMONG terminal states
   are deliberately allowed so a mis-swept job can self-heal — that same door
   let a completed cancel-victim rewrite history).

2. Clip files are cut straight into GENERATED_DIR under their final names —
   no scratch dir — so a backend that died between the ffmpeg fan-out and the
   DB save leaked mp4s no Library row references. Every other scratch surface
   had a sweeper; this one didn't.

   The reference set must cover EVERY file-path column: exporting a clip to
   16:9 writes `{stem}_16x9_{method}.mp4` next to the source, so a clip's
   cached export is ITSELF a `clip_*.mp4` — referenced only by
   `video_path_landscape`. A purge reading one column deleted it.

3. Manual mode with captions off still transcribed the whole source.
   `_build_manual_clip_windows` never reads a segment, so the transcript was
   bought and thrown away — on the first cut of a newly imported video that is
   Whisper chewing through the entire file to produce a 7-second trim.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from backend.core.exceptions import JobCancelledError
from backend.database import init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


# ── 1: job_cancelled() + the phase-boundary raise ────────────────────────────

class TestJobCancelledHelper:
    async def _make_job(self, status: str) -> str:
        from backend.agents.job_helper import create_job, update_job_status
        job = await create_job("clip_extraction", "local", {})
        if status != "pending":
            await update_job_status(job.id, status)
        return job.id

    def test_cancelled_job_reads_true(self):
        async def run():
            from backend.agents.job_helper import job_cancelled
            return await job_cancelled(await self._make_job("cancelled"))
        assert asyncio.run(run()) is True

    @pytest.mark.parametrize("status", ["pending", "running", "success", "failed"])
    def test_other_statuses_read_false(self, status):
        async def run():
            from backend.agents.job_helper import job_cancelled
            return await job_cancelled(await self._make_job(status))
        assert asyncio.run(run()) is False

    def test_none_and_unknown_ids_read_false(self):
        """A pipeline invoked without a job (direct service call, tests) must
        never think it was cancelled."""
        async def run():
            from backend.agents.job_helper import job_cancelled
            return (await job_cancelled(None), await job_cancelled("no-such-job"))
        assert asyncio.run(run()) == (False, False)

    def test_db_error_reads_false(self, monkeypatch):
        """Cancellation is best-effort — a transient read failure must not kill
        a healthy job."""
        async def run():
            from backend.agents import job_helper

            def boom(*a, **k):
                raise RuntimeError("db down")

            monkeypatch.setattr(job_helper, "AsyncSessionLocal", boom)
            return await job_helper.job_cancelled("some-id")
        assert asyncio.run(run()) is False

    def test_raise_if_cancelled_raises_the_specific_exception(self):
        async def run():
            from backend.services.clip_extractor import _raise_if_cancelled
            with pytest.raises(JobCancelledError):
                await _raise_if_cancelled(await self._make_job("cancelled"))
            # …and is silent for a live job.
            await _raise_if_cancelled(await self._make_job("running"))
        asyncio.run(run())


class TestRunnerHonoursCancel:
    """The end-to-end path: a cancel that lands during the ffmpeg fan-out is
    only visible after the clips exist. The runner must delete the files, save
    no rows, and keep the row cancelled."""

    def test_cancelled_job_saves_nothing_and_cleans_up(self, tmp_path):
        async def run():
            from backend.agents.job_helper import create_job, update_job_status
            from backend.core import task_runner
            from backend.database import AsyncSessionLocal
            from backend.models.downloaded_video import DownloadedVideo
            from backend.models.generated_video import GeneratedVideo
            from backend.models.job import Job
            from backend.services.clip_options import ExtractOptions
            from sqlalchemy import select
            import backend.services.clip_extractor as CE

            src = tmp_path / "src.mp4"
            src.write_bytes(b"fake")
            async with AsyncSessionLocal() as db:
                video = DownloadedVideo(
                    user_id="local", title="Cancel test src", platform="youtube",
                    video_path=str(src), duration_seconds=60,
                )
                db.add(video)
                await db.commit()
                video_id = video.id

            job = await create_job("clip_extraction", "local", {})

            clip_a = tmp_path / "clip_aaaa_000001.mp4"
            clip_b = tmp_path / "clip_aaaa_000002.mp4"

            async def fake_extract(**kwargs):
                # Produce real files, then the user's cancel lands mid-fan-out.
                clip_a.write_bytes(b"clip")
                clip_b.write_bytes(b"clip")
                await update_job_status(job.id, "cancelled")
                return [
                    {"video_path": clip_a, "thumbnail_path": None,
                     "caption_status": "skipped", "start": 0, "end": 10},
                    {"video_path": clip_b, "thumbnail_path": None,
                     "caption_status": "skipped", "start": 20, "end": 30},
                ]

            original = CE.extract_viral_clips
            CE.extract_viral_clips = fake_extract
            try:
                await task_runner.run_extract_clips(
                    job_id=job.id, downloaded_video_id=video_id,
                    opts=ExtractOptions(mode="manual", max_clips=2,
                                        time_ranges=[{"start": 0, "end": 10}]),
                    user_id="local",
                )
            finally:
                CE.extract_viral_clips = original

            async with AsyncSessionLocal() as db:
                row = (await db.execute(select(Job).where(Job.id == job.id))).scalar_one()
                clips = (await db.execute(
                    select(GeneratedVideo).where(
                        GeneratedVideo.source_downloaded_video_id == video_id)
                )).scalars().all()
            return row.status, clips, clip_a.exists(), clip_b.exists()

        status, clips, a_exists, b_exists = asyncio.run(run())
        assert status == "cancelled", f"row must stay cancelled, got {status}"
        assert clips == [], "a cancelled job must save no Library rows"
        assert not a_exists and not b_exists, "produced files must be cleaned up"


# ── 2: orphaned clip-file purge ──────────────────────────────────────────────

class TestOrphanClipPurge:
    @pytest.fixture()
    def gen_dir(self, tmp_path, monkeypatch):
        from backend.config import settings as S
        d = tmp_path / "generated"
        d.mkdir()
        monkeypatch.setattr(type(S), "GENERATED_DIR", property(lambda self: d))
        return d

    def _age(self, p: Path):
        two_days = time.time() - 48 * 3600
        os.utime(p, (two_days, two_days))

    def test_purges_only_old_unreferenced_clip_files(self, gen_dir):
        async def run():
            from backend.database import AsyncSessionLocal
            from backend.models.generated_video import GeneratedVideo
            from backend.services.clip_extractor import purge_orphan_clip_files

            orphan_old = gen_dir / "clip_dead01_aaaaaa.mp4"
            orphan_fresh = gen_dir / "clip_live02_bbbbbb.mp4"
            referenced_old = gen_dir / "clip_kept03_cccccc.mp4"
            not_a_clip = gen_dir / "video_regular_output.mp4"
            for p in (orphan_old, orphan_fresh, referenced_old, not_a_clip):
                p.write_bytes(b"x")
            for p in (orphan_old, referenced_old, not_a_clip):
                self._age(p)

            async with AsyncSessionLocal() as db:
                db.add(GeneratedVideo(
                    user_id="local", title="kept", video_path=str(referenced_old),
                    source_type="clip_extraction", status="ready",
                ))
                await db.commit()

            deleted = await asyncio.to_thread(purge_orphan_clip_files)
            return (deleted, orphan_old.exists(), orphan_fresh.exists(),
                    referenced_old.exists(), not_a_clip.exists())

        deleted, dead, fresh, kept, other = asyncio.run(run())
        assert deleted >= 1
        assert not dead, "old unreferenced clip_* file must be purged"
        assert fresh, "fresh files are in-flight — never touched"
        assert kept, "a Library-referenced file must survive"
        assert other, "non-clip_* files are out of scope"

    def test_a_cached_landscape_export_is_never_purged(self, gen_dir):
        """Exporting a clip to 16:9 writes `{stem}_16x9_{method}.mp4` NEXT TO
        the source, so the cached export of `clip_x.mp4` is itself a
        `clip_*.mp4` in GENERATED_DIR — referenced by `video_path_landscape`,
        NOT `video_path`. A purge reading one column deleted the user's cached
        export after 24h and left the row pointing at a missing file."""
        async def run():
            from backend.database import AsyncSessionLocal
            from backend.models.generated_video import GeneratedVideo
            from backend.services.clip_extractor import purge_orphan_clip_files

            clip = gen_dir / "clip_exp001_aaaaaa.mp4"
            export = gen_dir / "clip_exp001_aaaaaa_16x9_letterbox.mp4"
            for p in (clip, export):
                p.write_bytes(b"x")
                self._age(p)

            async with AsyncSessionLocal() as db:
                db.add(GeneratedVideo(
                    user_id="local", title="exported clip", video_path=str(clip),
                    video_path_landscape=str(export),
                    source_type="clip_extraction", status="ready",
                ))
                await db.commit()

            await asyncio.to_thread(purge_orphan_clip_files)
            return clip.exists(), export.exists()

        clip_kept, export_kept = asyncio.run(run())
        assert clip_kept, "the source clip is referenced and must survive"
        assert export_kept, (
            "the cached 16:9 export was deleted — video_path_landscape now "
            "points at a missing file"
        )

    def test_db_error_aborts_instead_of_deleting(self, gen_dir, monkeypatch):
        """'Couldn't check references' must never become 'unreferenced'."""
        import sqlalchemy
        from backend.services import clip_extractor as CE

        orphan_old = gen_dir / "clip_dead04_dddddd.mp4"
        orphan_old.write_bytes(b"x")
        self._age(orphan_old)

        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(sqlalchemy, "create_engine", boom)
        with pytest.raises(RuntimeError):
            CE.purge_orphan_clip_files()
        assert orphan_old.exists(), "purge must abort, not delete, on DB error"

    def test_empty_dir_is_a_noop(self, gen_dir):
        from backend.services.clip_extractor import purge_orphan_clip_files
        assert purge_orphan_clip_files() == 0


# ── 3: manual + captions off skips transcription ─────────────────────────────

class TestManualModeSkipsTranscription:
    """`_build_manual_clip_windows` never reads a segment, so with captions off
    the transcript is bought and thrown away."""

    def _run(self, *, mode: str, caption_style, tmp_path):
        """Drive extract_viral_clips far enough to observe whether the
        transcription step was reached. Returns True when it was."""
        from types import SimpleNamespace

        import backend.services.clip_extractor as CE
        from backend.services.clip_options import ExtractOptions

        called = {"transcribed": False}

        async def fake_load(*a, **k):
            called["transcribed"] = True
            return [{"start": 0.0, "end": 5.0, "text": "hello there friend"}]

        async def fake_process(**kwargs):
            return [{"video_path": tmp_path / "clip_x.mp4", "status": "ok"}]

        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake")
        video = SimpleNamespace(
            id="abcdef012345", title="Src", video_path=str(src),
            duration_seconds=600, transcript=None,
        )

        orig_load = CE._load_or_transcribe_segments
        orig_process = CE._process_clips_parallel
        CE._load_or_transcribe_segments = fake_load
        CE._process_clips_parallel = fake_process
        try:
            asyncio.run(CE.extract_viral_clips(
                video=video, user_settings=None,
                opts=ExtractOptions(
                    mode=mode, max_clips=1, caption_style=caption_style,
                    time_ranges=[{"start": 0, "end": 7}] if mode == "manual" else None,
                ),
                job_id=None, user_id="local",
            ))
        finally:
            CE._load_or_transcribe_segments = orig_load
            CE._process_clips_parallel = orig_process
        return called["transcribed"]

    def test_manual_with_captions_off_does_not_transcribe(self, tmp_path):
        assert self._run(mode="manual", caption_style="none", tmp_path=tmp_path) is False

    def test_manual_with_captions_on_still_transcribes(self, tmp_path):
        assert self._run(mode="manual", caption_style="viral", tmp_path=tmp_path) is True

    def test_unspecified_style_is_not_off(self, tmp_path):
        """None means "the caller didn't say", never "off" — captions would
        render with the default style, so the timings are still needed."""
        assert self._run(mode="manual", caption_style=None, tmp_path=tmp_path) is True
