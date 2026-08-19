# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Library API — one faceted view over everything you own, plus its lineage.

    GET    /api/library/items?media=&origin=&producer=&q=&sort=&limit=&offset=
    GET    /api/library/families?…            group-by-what-it-was-made-from
    GET    /api/library/item?key=             one row (title, urls, store)
    GET    /api/library/lineage?key=          ancestors + children
    GET    /api/library/taxonomy              axis labels + the producer map
    GET    /api/library/poster?key=job:<id>   extracted frame for a tool video
    GET    /api/library/asset/{job_id}        stream/download a tool output
    DELETE /api/library/asset/{job_id}        remove the row and its file
    POST   /api/library/bulk-delete           the same, for a selection

The classification lives in `library_taxonomy`; the projection over the four
stores lives in `library_index`. This module is the HTTP shape and nothing
else, deliberately — the two questions the taxonomy answers are the same
whether they are asked by this router, by the page or by a test.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.job import Job
from backend.services import library_index
from backend.services.library_taxonomy import classify, taxonomy_payload

# The "/api" half is added at registration, like every other router here.
router = APIRouter(prefix="/library", tags=["library"])
logger = logging.getLogger(__name__)

# Seek past a black intro frame. A clip SHORTER than this yields no frame at
# all, which is handled at the callsite rather than by lowering the seek.
POSTER_SEEK_SECONDS = 2.0

# MIME by suffix for the asset endpoint. Anything unlisted still gets its bytes,
# just without a content-aware Content-Type.
SUFFIX_MEDIA_TYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".srt": "text/plain", ".vtt": "text/vtt", ".txt": "text/plain",
    ".json": "application/json",
}


@router.get("/items")
async def list_items(
    media: str | None = Query(None, description="Comma-separated: video,image,audio,doc"),
    origin: str | None = Query(None, description="Comma-separated: created,edited,imported"),
    producer: str | None = Query(None, description="Comma-separated producer keys (e.g. tool:captions)"),
    q: str | None = Query(None, description="Substring match on title / tool / niche"),
    sort: str = Query("newest"),
    limit: int = Query(library_index.DEFAULT_LIMIT, ge=1, le=library_index.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """One faceted page across everything you own.

    Unions generated videos, downloads/imports, tool outputs and music-library
    tracks into a single classified list. Filters are two orthogonal axes —
    `media` (what a file is) and `origin` (where it came from) — plus an
    optional `producer` narrowing and a title search.

    Rows whose file is missing on disk are omitted, never deleted: a read must
    not mutate.

    Returns: {items, total, limit, offset, facets: {all, media, origin,
    producers}, library_total}. Each item carries `key` ("gv:<id>" / "dl:<id>" /
    "job:<id>" / "trk:<name>"), `media`, `origin`, `producer`, `parent_key` for
    lineage, plus title / thumb_url / stream_url / aspect / duration_seconds /
    size_mb / created_at.
    """
    return await library_index.query(
        db, media=media, origin=origin, producer=producer,
        q=q, sort=sort, limit=limit, offset=offset,
    )


@router.get("/families")
async def list_families(
    media: str | None = Query(None),
    origin: str | None = Query(None),
    producer: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """The same items grouped by what they were made from.

    A download appears with the clips cut from it and the captioned version of
    one of those clips as one family. Members outside the current filters are
    still returned (the caller dims them) because that context is the whole
    reason to use this view.

    Paged: a family carries its whole subtree, so this is the one view that
    could otherwise hand the browser thousands of rows at once.

    Returns: {families: [{root, children: [{item, children}]}], loners,
    matched_keys, family_total, loner_total, limit, offset, total}.
    """
    return await library_index.families(
        db, limit=limit, offset=offset,
        media=media, origin=origin, producer=producer, q=q,
    )


@router.get("/item")
async def get_item(
    key: str = Query(..., description='Item key, e.g. "gv:<id>" or "job:<id>"'),
    db: AsyncSession = Depends(get_db),
):
    """One library item by key. 404 when the key names nothing in the index."""
    result = await library_index.lineage(db, key)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found in the library index")
    return result["item"]


@router.get("/lineage")
async def get_lineage(
    key: str = Query(..., description='Item key, e.g. "gv:<id>" or "job:<id>"'),
    db: AsyncSession = Depends(get_db),
):
    """What an item was made from, and what was made from it.

    Powers the drawer's Lineage tab. Asked per item rather than held for the
    whole library, because the grid deliberately does not load the family tree.

    Returns: {item, ancestors: [nearest first], children}. 404 when the key
    names nothing (a deleted row, or a file that has since gone).
    """
    result = await library_index.lineage(db, key)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found in the library index")
    return result


@router.get("/taxonomy")
async def get_taxonomy():
    """Axis labels + the producer map, so the UI renders from one source.

    Returns: {media: [{key, label}], origins: [{key, label, hint}],
    producers: [{key, label, media, origin}]}.
    """
    return taxonomy_payload()


@router.get("/poster")
async def get_poster(
    key: str = Query(..., description='Item key of a job-produced video, e.g. "job:<id>"'),
    db: AsyncSession = Depends(get_db),
):
    """Poster frame for a tool-produced video, extracted on first request.

    Nothing in the tool pipeline writes a thumbnail, so every edited video — a
    captioned cut, a reframe, a merge — had no image to show. Extract one frame
    past any black intro, cache it, serve it.

    404 when the key isn't an indexed job video or extraction fails; the tile
    falls back to its provenance-tinted plate, which is why this can be lazy.
    """
    from backend.config import settings as app_settings
    from backend.services.ffmpeg_service import extract_thumbnail

    src = await library_index.poster_source(db, key)
    if src is None:
        raise HTTPException(status_code=404, detail="No poster for that item")

    # One cache file per job id. `key` came back from the index and the id is a
    # UUID, so it cannot escape the directory.
    job_id = key.split(":", 1)[1]
    cache = app_settings.THUMBNAILS_DIR / f"libposter_{job_id}.jpg"
    if not cache.is_file():
        produced = await extract_thumbnail(src, cache, POSTER_SEEK_SECONDS)
        if produced is None or not cache.is_file():
            # A clip SHORTER than the seek yields zero frames and ffmpeg exits
            # non-zero — ordinary for GIF sources and quick transforms. Retry
            # from the first frame before giving up.
            produced = await extract_thumbnail(src, cache, 0.0)
        if produced is None or not cache.is_file():
            raise HTTPException(status_code=404, detail="Could not extract a poster")
    return FileResponse(
        cache,
        media_type="image/jpeg",
        # Content-addressed by job id and never regenerated, so it can be cached
        # hard — the grid asks for one per edited video, on every paint.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def resolve_asset_path(job_id: str, db: AsyncSession) -> Path:
    """Resolve a tool output's job id to its file on disk, or raise.

    The rule is: **what the Library shows, this serves.** Gating on a separate
    allowlist is how the two drift — a tile whose download 404s is worse than
    one that was never shown, because the user has already been told it exists.

    Shared by the stream and delete endpoints so both go through the same
    classification and the same traversal-safe path resolution.
    """
    job = await db.get(Job, job_id)
    if not job or job.status != "success" or classify(job.job_type) is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not job.output_json:
        raise HTTPException(status_code=404, detail="Asset has no output")
    try:
        out = json.loads(job.output_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupt output_json")
    file_str = (out or {}).get("file")
    if not file_str:
        raise HTTPException(status_code=404, detail="Asset has no file")

    from backend.api.videos import _safe_resolve_path
    path = _safe_resolve_path(file_str)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Asset file missing on disk")
    return path


@router.get("/asset/{job_id}")
async def stream_asset(job_id: str, db: AsyncSession = Depends(get_db)):
    """Stream a tool output — the tile's preview, the drawer's player, and the
    only download the Library offers for a file no other route serves."""
    path = await resolve_asset_path(job_id, db)
    return FileResponse(
        path,
        media_type=SUFFIX_MEDIA_TYPE.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )


@router.get("/track/{filename}")
async def stream_track(filename: str):
    """Stream a background-music file from storage/music/.

    There is no music router here — tracks are files you drop into that
    directory yourself — but they are still files you own, so the Library lists
    them and therefore has to be able to play them. Path traversal is refused
    the same way the music service refuses it: resolve, then require the result
    to still be inside the directory.
    """
    from backend.services.music_service import MUSIC_DIR
    from backend.services.library_index import MUSIC_EXTS

    try:
        candidate = (MUSIC_DIR / filename).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Bad track name")
    if not candidate.is_relative_to(MUSIC_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Bad track name")
    if not candidate.is_file() or candidate.suffix.lower() not in MUSIC_EXTS:
        raise HTTPException(status_code=404, detail="Track not found")
    return FileResponse(
        candidate,
        media_type=SUFFIX_MEDIA_TYPE.get(candidate.suffix.lower(), "audio/mpeg"),
        filename=candidate.name,
    )


def _drop_poster_cache(job_id: str) -> None:
    """Remove the extracted poster for a deleted job asset.

    Keyed `libposter_<job_id>.jpg`; without this, every deleted edited video
    leaves an orphaned JPEG behind forever."""
    from backend.config import settings as app_settings
    try:
        (app_settings.THUMBNAILS_DIR / f"libposter_{job_id}.jpg").unlink(missing_ok=True)
    except OSError:  # pragma: no cover — cache cleanup is best-effort
        logger.warning("Could not remove poster cache for job %s", job_id)


def _unlink_job_output(job: Job) -> None:
    """Best-effort removal of the file a job wrote.

    Traversal-rejected or already-missing paths are skipped rather than raised:
    the ROW still has to disappear, or the Library keeps a tile for a file the
    user asked to be rid of.
    """
    if not job.output_json:
        return
    try:
        out = json.loads(job.output_json)
    except json.JSONDecodeError:
        return
    file_str = (out or {}).get("file")
    if not file_str:
        return
    from backend.api.videos import _safe_resolve_path
    try:
        path = _safe_resolve_path(file_str)
    except HTTPException:
        logger.warning("Skipped unsafe file delete for job %s", job.id)
        return
    if path.is_file():
        path.unlink(missing_ok=True)


@router.delete("/asset/{job_id}")
async def delete_asset(job_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a tool output's file and its job row.

    Idempotent in the way that matters: a stale UI calling twice gets a 404 on
    the second call, but the first always cleans up everything it could.
    """
    job = await db.get(Job, job_id)
    if not job or classify(job.job_type) is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    _unlink_job_output(job)
    _drop_poster_cache(job_id)
    await db.delete(job)
    await db.commit()
    return {"ok": True}


@router.post("/bulk-delete")
async def bulk_delete_assets(body: dict, db: AsyncSession = Depends(get_db)):
    """Delete many tool outputs at once. Body: {"job_ids": [...]}.

    404 only when NONE of the requested ids exist; a partial match succeeds
    with `deleted` reflecting what actually went, because a selection spanning
    several stores is normal and failing the whole call would strand it.
    """
    job_ids = body.get("job_ids", [])
    if not job_ids or not isinstance(job_ids, list):
        raise HTTPException(status_code=400, detail="job_ids must be a non-empty list")
    if len(job_ids) > 200:
        raise HTTPException(status_code=400, detail="Cannot delete more than 200 assets at once")

    rows = (await db.execute(select(Job).where(Job.id.in_(job_ids)))).scalars().all()
    rows = [j for j in rows if classify(j.job_type) is not None]
    if not rows:
        raise HTTPException(status_code=404, detail="No matching assets found")

    deleted = 0
    for job in rows:
        _unlink_job_output(job)
        _drop_poster_cache(job.id)
        await db.delete(job)
        deleted += 1
    await db.commit()
    return {"ok": True, "deleted": deleted}
