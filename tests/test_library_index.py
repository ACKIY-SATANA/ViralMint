# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Library index — one faceted view over four stores.

The page it replaced tabbed on a MIX of two axes (Scout / Downloaded /
Generated is where a file came FROM; a file's media type is what it IS), so a
row with a true answer on both had to be filed under one and was lost from the
other. And every tool output — captions, reframes, merges, GIFs, subtitle
files — appeared on no surface at all.

These pin the properties that keep that from coming back:

  1. a tool output IS a library item, classified on both axes
  2. an audio-only download is audio · imported (the axis-collision case)
  3. a clip job is not double-counted against its generated_videos row
  4. a missing file is OMITTED from the index but NOT deleted from the DB
  5. facet counts exclude their own axis
  6. lineage links across stores
  7. resolve_job_video answers only for indexed videos inside the storage root

Handlers and services are called directly with a real AsyncSession. Every row
written here carries an `idx-probe-` prefix and is removed in a finally.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

PREFIX = "idx-probe-"


async def _session():
    from backend.database import AsyncSessionLocal, init_db
    await init_db()
    return AsyncSessionLocal()


def _real_file(name: str, body: bytes = b"x" * 64) -> Path:
    """A file inside STORAGE_ROOT. The index skips rows whose file is gone, so
    a fixture pointing at a dangling path tests nothing."""
    from backend.config import settings
    out = settings.STORAGE_ROOT / "tools" / "out"
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    p.write_bytes(body)
    return p


async def _cleanup(db):
    from backend.models.downloaded_video import DownloadedVideo
    from backend.models.generated_video import GeneratedVideo
    from backend.models.job import Job
    for Model in (Job, GeneratedVideo, DownloadedVideo):
        rows = (await db.execute(select(Model).where(Model.id.startswith(PREFIX)))).scalars().all()
        for r in rows:
            await db.delete(r)
    await db.commit()


def _by_key(items, key):
    return next((i for i in items if i["key"] == key), None)


async def test_a_tool_output_is_a_library_item():
    """The headline bug: every tool wrote a file and no surface showed it."""
    from backend.models.job import Job
    from backend.services import library_index

    db = await _session()
    f = _real_file(f"{PREFIX}captions.mp4")
    try:
        db.add(Job(id=f"{PREFIX}1", job_type="tool:captions", status="success",
                   title="Captioned cut", output_json=json.dumps({"file": str(f)}),
                   created_at=datetime.utcnow()))
        await db.commit()

        items = await library_index.gather(db)
        item = _by_key(items, f"job:{PREFIX}1")
        assert item is not None, "a successful tool output is missing from the index"
        assert item["media"] == "video"
        assert item["origin"] == "edited"
        assert item["producer"] == "Captions"
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_an_audio_only_download_is_audio_and_imported():
    """The axis collision: the row is a download (a video store) and the file
    is audio. Under the old tabs it had to be one or the other."""
    from backend.models.downloaded_video import DownloadedVideo
    from backend.services import library_index

    db = await _session()
    f = _real_file(f"{PREFIX}pod.mp3")
    try:
        db.add(DownloadedVideo(id=f"{PREFIX}dl1", title="A podcast", platform="youtube",
                               video_path=None, audio_path=str(f)))
        await db.commit()

        item = _by_key(await library_index.gather(db), f"dl:{PREFIX}dl1")
        assert item is not None
        assert item["media"] == "audio"
        assert item["origin"] == "imported"
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_a_clip_job_is_not_counted_twice():
    """A clip run writes a file AND registers a generated_videos row for it.
    The row is the richer record, so the job copy must not appear as a second
    tile for the same bytes."""
    from backend.models.generated_video import GeneratedVideo
    from backend.models.job import Job
    from backend.services import library_index

    db = await _session()
    f = _real_file(f"{PREFIX}clip.mp4")
    try:
        db.add(GeneratedVideo(id=f"{PREFIX}gv1", title="Clip 1", video_path=str(f),
                              source_type="clip_extraction"))
        db.add(Job(id=f"{PREFIX}2", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(f), "generated_video_id": f"{PREFIX}gv1"})))
        await db.commit()

        items = await library_index.gather(db)
        assert _by_key(items, f"gv:{PREFIX}gv1") is not None
        assert _by_key(items, f"job:{PREFIX}2") is None
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_a_missing_file_is_omitted_but_never_deleted():
    """A read must not mutate. An index that pruned rows on GET would be a
    data-loss surface reachable by typing a URL."""
    from backend.models.job import Job
    from backend.services import library_index

    db = await _session()
    try:
        db.add(Job(id=f"{PREFIX}3", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": "/nope/gone.mp4"})))
        await db.commit()

        assert _by_key(await library_index.gather(db), f"job:{PREFIX}3") is None
        assert await db.get(Job, f"{PREFIX}3") is not None, "the index deleted a row on a READ"
    finally:
        await _cleanup(db)
        await db.close()


async def test_facet_counts_exclude_their_own_axis():
    """A count computed with its own filter applied would promise "3 audio
    files" and then show one."""
    from backend.models.job import Job
    from backend.services import library_index

    db = await _session()
    v = _real_file(f"{PREFIX}f1.mp4")
    a = _real_file(f"{PREFIX}f2.mp3")
    try:
        db.add(Job(id=f"{PREFIX}4", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(v)})))
        db.add(Job(id=f"{PREFIX}5", job_type="tool:voiceover", status="success",
                   output_json=json.dumps({"file": str(a)})))
        await db.commit()

        res = await library_index.query(db, media="video")
        # media facet is computed with media NOT applied, so audio is still counted
        assert res["facets"]["media"]["audio"] >= 1
        # …while the page itself really is narrowed
        assert all(i["media"] == "video" for i in res["items"])
    finally:
        await _cleanup(db)
        v.unlink(missing_ok=True)
        a.unlink(missing_ok=True)
        await db.close()


async def test_lineage_links_a_clip_to_the_download_it_came_from():
    """Across stores: the clip lives in generated_videos, its parent in
    downloaded_videos, and the tree has to cross that boundary."""
    from backend.models.downloaded_video import DownloadedVideo
    from backend.models.generated_video import GeneratedVideo
    from backend.services import library_index

    db = await _session()
    src = _real_file(f"{PREFIX}src.mp4")
    clip = _real_file(f"{PREFIX}cut.mp4")
    try:
        db.add(DownloadedVideo(id=f"{PREFIX}dl2", title="Source", platform="youtube",
                               video_path=str(src)))
        db.add(GeneratedVideo(id=f"{PREFIX}gv2", title="Cut", video_path=str(clip),
                              source_type="clip_extraction",
                              source_downloaded_video_id=f"{PREFIX}dl2"))
        await db.commit()

        result = await library_index.lineage(db, f"gv:{PREFIX}gv2")
        assert result is not None
        assert [a["key"] for a in result["ancestors"]] == [f"dl:{PREFIX}dl2"]
    finally:
        await _cleanup(db)
        src.unlink(missing_ok=True)
        clip.unlink(missing_ok=True)
        await db.close()


async def test_resolve_job_video_answers_only_for_indexed_videos():
    """It is the one place that answers "is this job id a video I own, and
    where". A doc output is not a video, and a row the index skips is not
    resolvable at all."""
    from backend.models.job import Job
    from backend.services import library_index

    db = await _session()
    vid = _real_file(f"{PREFIX}ok.mp4")
    doc = _real_file(f"{PREFIX}subs.srt")
    try:
        db.add(Job(id=f"{PREFIX}6", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(vid)})))
        db.add(Job(id=f"{PREFIX}7", job_type="tool:subtitle_export", status="success",
                   output_json=json.dumps({"file": str(doc)})))
        await db.commit()

        assert await library_index.resolve_job_video(db, f"{PREFIX}6") == vid
        assert await library_index.resolve_job_video(db, f"{PREFIX}7") is None
        assert await library_index.resolve_job_video(db, "no-such-job") is None
    finally:
        await _cleanup(db)
        vid.unlink(missing_ok=True)
        doc.unlink(missing_ok=True)
        await db.close()


async def test_the_asset_endpoint_serves_what_the_library_shows():
    """The gate is the taxonomy, not a second allowlist: a tile whose download
    404s is worse than one that was never shown."""
    from fastapi import HTTPException

    from backend.api.library import resolve_asset_path
    from backend.models.job import Job

    db = await _session()
    f = _real_file(f"{PREFIX}chapters.txt")
    try:
        db.add(Job(id=f"{PREFIX}8", job_type="tool:auto_chapters", status="success",
                   output_json=json.dumps({"file": str(f)})))
        db.add(Job(id=f"{PREFIX}9", job_type="scout", status="success",
                   output_json=json.dumps({"file": str(f)})))
        await db.commit()

        assert await resolve_asset_path(f"{PREFIX}8", db) == f
        # a scout row is activity, not a file the user owns — even pointed at one
        with pytest.raises(HTTPException) as exc:
            await resolve_asset_path(f"{PREFIX}9", db)
        assert exc.value.status_code == 404
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()
