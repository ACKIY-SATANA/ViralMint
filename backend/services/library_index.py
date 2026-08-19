# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Library index — one paged, faceted view over everything the user owns.

Four stores hold durable files today, and until now each had its own reader
with its own vocabulary:

    generated_videos   the studios' renders + extracted clips
    downloaded_videos  what came in from a link or the user's disk
    jobs               every tool output (which no surface showed at all)
    storage/music/     uploaded background-music tracks

This module projects all four onto ONE item shape classified by
[library_taxonomy.py](backend/services/library_taxonomy.py), so the page and
the tool pickers can ask a single question with facets instead of each
stitching per-store lists together.

Shape of the work: gather → filter → facet → sort → page. Gathering runs the
cheap SQL-side filters (job status, job-type allowlist, title LIKE) and then
finishes in Python, because facet counts have to be computed per axis with the
OTHER axes applied — a count that ignored the current filters would tell the
user "42 audio files" and then show them three. Library sizes here are in the
hundreds to low thousands of rows, so one projected pass is cheaper than four
paginated queries plus a merge, and it keeps the ordering honest across stores.

A row whose file is gone is SKIPPED, never deleted. An index that silently
deleted rows on a READ would be a data-loss surface reachable by anyone typing
a URL, and "the file moved" is not the same fact as "the user wants it gone".
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.downloaded_video import DownloadedVideo
from backend.models.generated_video import GeneratedVideo, NICHE_MAX_CHARS
from backend.models.job import Job
from backend.services.library_taxonomy import (
    MEDIA_AUDIO,
    MEDIA_ORDER,
    ORIGIN_ORDER,
    classify,
)

logger = logging.getLogger(__name__)

SORTS = ("newest", "oldest", "longest", "title")
DEFAULT_LIMIT = 60
MAX_LIMIT = 200

# Item keys are prefixed per store so one id space addresses the whole library
# (and so a parent pointer can cross stores).
KEY_GENERATED = "gv"
KEY_DOWNLOADED = "dl"
KEY_JOB = "job"
KEY_TRACK = "trk"


def _suffix_media(suffix: str) -> str | None:
    """Media implied by a file extension, for rows a key can't answer alone."""
    s = suffix.lower()
    if s in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return "video"
    if s in {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}:
        return MEDIA_AUDIO
    if s in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    if s in {".srt", ".vtt", ".txt", ".md", ".json"}:
        return "doc"
    # An archive is a FILE, and it must say so here rather than inherit its
    # producer's media: a bundle merge and a multi-aspect export both write a
    # .zip from a VIDEO producer, so without this the Library offered a poster
    # and a player for an archive. `doc` is the index's not-audio/video/image
    # bucket, which is what a zip is.
    if s == ".zip":
        return "doc"
    return None


# Tool runners name their output file after the job id, so the file stem is a
# UUID — useless as a tile label. `_fill_titles` replaces those with something
# a person can scan.
_UUID_STEM = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# Input fields worth showing when a tool never stored a title. Ordered: the
# first hit wins, so a podcast reads by its topic rather than "2 speakers".
_TITLE_DETAIL_FIELDS = ("topic", "prompt", "text", "script", "custom_prompt", "url")
_TITLE_DETAIL_MAX = 70

# The Translate tool's 21 targets, so a title says "Spanish" and not "es".
_LANGUAGE_NAMES = {
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "ru": "Russian",
    "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "id": "Indonesian",
    "vi": "Vietnamese", "th": "Thai", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "zh-cn": "Chinese", "zh-tw": "Chinese (Traditional)",
    "en": "English", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
    "fi": "Finnish", "uk": "Ukrainian", "he": "Hebrew", "el": "Greek",
    "cs": "Czech", "ro": "Romanian", "hu": "Hungarian",
}


def _detail_from_input(inp: dict) -> str | None:
    """A short, human detail from a job's arguments, or None.

    Without this a library of tool outputs reads as a wall of identical labels —
    eleven tiles all saying "Dialogue". The arguments are what tell them apart.
    """
    for field in _TITLE_DETAIL_FIELDS:
        val = inp.get(field)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            return text[:_TITLE_DETAIL_MAX] + ("…" if len(text) > _TITLE_DETAIL_MAX else "")
    lang = inp.get("target_language")
    if isinstance(lang, str) and lang.strip():
        code = lang.strip()
        # "Translate — es" is a database value, not a label a person reads.
        return _LANGUAGE_NAMES.get(code.lower(), code)
    speakers = inp.get("speakers")
    if isinstance(speakers, int) and speakers > 0:
        return f"{speakers} voice{'s' if speakers > 1 else ''}"
    style = inp.get("style")
    if isinstance(style, str) and style.strip():
        return style.strip()
    return None


def _json_or_none(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _exists(path_str: str | None) -> bool:
    if not path_str:
        return False
    try:
        return Path(path_str).is_file()
    except OSError:  # pragma: no cover — malformed path on the platform
        return False


def _version(path_str: str | None) -> str | None:
    """Cache-busting stamp for an image URL: the file's mtime.

    Thumbnails are served with a year-long, immutable Cache-Control ONLY when the
    URL carries this (see videos.thumbnail_cache_headers). Without it the browser
    revalidated on every scroll-into-view, so a long grid reloaded its cards as
    you scrolled. Regenerating a thumbnail changes the mtime, hence the URL, so a
    hard cache can never go stale.
    """
    if not path_str:
        return None
    try:
        return str(int(Path(path_str).stat().st_mtime))
    except OSError:
        return None


def _size_mb(path_str: str | None) -> float | None:
    if not path_str:
        return None
    try:
        return round(Path(path_str).stat().st_size / (1024 * 1024), 2)
    except OSError:
        return None


async def _generated_items(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(GeneratedVideo))).scalars().all()
    items = []
    for v in rows:
        if not _exists(v.video_path):
            continue
        p = classify(v.source_type or "smart_video")
        if p is None:
            continue
        # Prose that leaked into `niche` before 2026-07-30 renders as an absurd
        # filter value; the same guard the niches endpoint uses.
        niche = v.niche if v.niche and len(v.niche) <= NICHE_MAX_CHARS else None
        items.append({
            "key": f"{KEY_GENERATED}:{v.id}",
            "source": "generated",
            "id": v.id,
            "media": p.media,
            "origin": p.origin,
            "producer_key": p.key,
            "producer": p.label,
            "title": v.title or v.youtube_title or "Untitled video",
            "thumb_url": (
                f"/api/videos/{v.id}/thumbnail?v={_version(v.thumbnail_path)}"
                if _exists(v.thumbnail_path) else None
            ),
            "stream_url": f"/api/videos/{v.id}/stream",
            "aspect": v.aspect_ratio,
            "duration_seconds": v.duration_seconds,
            "size_mb": _size_mb(v.video_path),
            "ext": Path(v.video_path).suffix.lstrip(".") or None,
            "status": v.status,
            "niche": niche,
            "platform": None,
            "hook_score": v.clip_hook_score,
            "virality_score": v.clip_virality_score,
            "parent_key": (
                f"{KEY_DOWNLOADED}:{v.source_downloaded_video_id}"
                if v.source_downloaded_video_id else None
            ),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    return items


async def _downloaded_items(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(DownloadedVideo))).scalars().all()
    items = []
    for v in rows:
        has_video = _exists(v.video_path)
        has_audio = _exists(v.audio_path)
        if not (has_video or has_audio):
            continue
        # An audio-only download is the case the old tabs could not file: it
        # belonged to both "Downloaded" and "Audio". Here it is one row that
        # answers audio · imported.
        media = "video" if has_video else MEDIA_AUDIO
        key = "import" if (v.platform or "").lower() == "import" else "download"
        p = classify(key, media_override=media)
        if p is None:  # pragma: no cover — both keys are mapped
            continue
        file_path = v.video_path if has_video else v.audio_path
        items.append({
            "key": f"{KEY_DOWNLOADED}:{v.id}",
            "source": "downloaded",
            "id": v.id,
            "media": p.media,
            "origin": p.origin,
            "producer_key": p.key,
            "producer": p.label,
            "title": v.title or "Untitled",
            "thumb_url": (
                f"/api/downloaded/{v.id}/thumbnail?v={_version(v.thumbnail_path)}"
                if _exists(v.thumbnail_path) else None
            ),
            "stream_url": f"/api/downloaded/{v.id}/stream",
            "aspect": None,
            "duration_seconds": v.duration_seconds,
            "size_mb": v.file_size_mb or _size_mb(file_path),
            "ext": Path(file_path).suffix.lstrip(".") or None,
            "status": None,
            "niche": None,
            "platform": v.platform,
            "hook_score": None,
            "virality_score": None,
            "parent_key": None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    return items


async def _job_items(db: AsyncSession) -> tuple[list[dict], set[str]]:
    """Tool outputs, plus the music-library filenames they also wrote.

    A runner that ALSO copies its output into storage/music/ would otherwise
    have that copy appear a second time as an uploaded track. The contract is
    that such a runner records the copy's name in `output_json.library_filename`
    and the music scan skips exactly those names — an explicit record rather
    than guessing at the filename shape. No runner writes it today; the scan
    honours it so the first one that does cannot double-count.
    """
    rows = (
        await db.execute(select(Job).where(Job.status == "success"))
    ).scalars().all()
    items: list[dict] = []
    music_copies: set[str] = set()
    for j in rows:
        out = _json_or_none(j.output_json)
        if not out:
            continue
        file_str = out.get("file")
        if not file_str or not _exists(file_str):
            continue
        # A clip job ALSO registers a GeneratedVideo row for the same file.
        # That row is the richer record (aspect, scores, thumbnail), so the job
        # copy is dropped here rather than double-counted.
        if out.get("generated_video_id"):
            continue
        p = classify(j.job_type)
        if p is None:
            # scout / analyze / download / render jobs — activity, not a file
            # the user picks. The taxonomy's NON_LIBRARY_JOB_TYPES records which
            # types are deliberately here, so an unclassified NEW tool reads as
            # the oversight it is rather than as a decision.
            continue
        lib_name = out.get("library_filename")
        if isinstance(lib_name, str) and lib_name:
            music_copies.add(lib_name)
        path = Path(file_str)
        suffix_media = _suffix_media(path.suffix)
        inp = _json_or_none(j.input_json) or {}
        duration = out.get("duration_seconds")
        if duration is None:
            duration = out.get("duration")
        items.append({
            "key": f"{KEY_JOB}:{j.id}",
            "source": "job",
            "id": j.id,
            # The extension wins over the map when they disagree: `tool:chain`
            # and `tool:transform` can emit audio or video depending on the ops
            # the user picked, and the file on disk is the ground truth.
            "media": suffix_media or p.media,
            "origin": p.origin,
            "producer_key": p.key,
            "producer": p.label,
            # None here means "no stored name" — filled in by _fill_titles once
            # lineage is resolved, so a captioned clip reads "<clip> — Captions"
            # instead of a UUID.
            "title": j.title or out.get("title") or (
                None if (_UUID_STEM.match(path.stem) or path.stem.startswith(j.id))
                else path.stem
            ),
            # Fallback material, consumed by _fill_titles and then dropped.
            "_detail": _detail_from_input(inp),
            # Only offer a poster when the file is somewhere the poster endpoint
            # can legally read it. Emitting the URL regardless meant every load
            # fired a request that was guaranteed to 404 (rows written by tests
            # or older builds point outside the storage root), and a failed
            # extraction isn't cached, so the miss repeated on every render.
            "thumb_url": _job_thumb_url(j.id, suffix_media or p.media, path),
            "stream_url": f"/api/library/asset/{j.id}",
            "aspect": inp.get("aspect_ratio"),
            "duration_seconds": duration if isinstance(duration, (int, float)) else None,
            "size_mb": out.get("file_size_mb") or _size_mb(file_str),
            "ext": path.suffix.lstrip(".") or None,
            "status": None,
            "niche": None,
            "platform": None,
            "hook_score": None,
            "virality_score": None,
            # Tool pages take an UPLOAD, so their rows genuinely have no parent
            # and By-source shows them as loners. Lineage here is only as good
            # as what a runner records: a job that names its input under one of
            # `_resolve_parent`'s keys gets a family, and nothing does today
            # apart from the clip and render paths (which carry it on the
            # generated_videos row instead).
            "parent_key": _resolve_parent(inp),
            "created_at": (j.completed_at or j.created_at).isoformat() if (j.completed_at or j.created_at) else None,
        })
    return items, music_copies


def _job_thumb_url(job_id: str, media: str, path: Path) -> str | None:
    """Where a job output's poster comes from.

    An image IS its own poster. A video produced by a tool has no thumbnail
    anywhere — nothing in the pipeline made one — so it points at the poster
    endpoint, which extracts a frame on first request and caches it. Audio and
    text have no poster; their tiles draw a waveform or a snippet instead.
    """
    if media == "image":
        return f"/api/library/asset/{job_id}"
    if media == "video" and _within_storage(path):
        return f"/api/library/poster?key={KEY_JOB}:{job_id}"
    return None


def _within_storage(path: Path) -> bool:
    """Is this file inside STORAGE_ROOT — i.e. readable by the media endpoints?"""
    from backend.config import settings
    try:
        return path.resolve().is_relative_to(settings.STORAGE_ROOT.resolve())
    except OSError:  # pragma: no cover — unresolvable path
        return False


async def resolve_job_video(db: AsyncSession, job_id: str) -> Path | None:
    """On-disk video file for a tool-produced library item, or None.

    The one place that answers "is this job id a video I own, and where is it".
    Both the poster endpoint and the two id→path resolvers behind the tool
    inputs (`_resolve_video_path`, `resolve_library_video_path`) go through it,
    so a captioned cut is either usable everywhere or nowhere — the old split,
    where edited videos existed on disk but no resolver could find them, is
    exactly why they couldn't be fed back into a tool.

    Resolution goes THROUGH the index, so only a file the library lists — and
    only one inside STORAGE_ROOT — can ever come back.
    """
    all_items = await gather(db)
    item = next(
        (it for it in all_items
         if it["source"] == "job" and it["id"] == job_id and it["media"] == "video"),
        None,
    )
    if item is None:
        return None
    from backend.models.job import Job as _Job
    job = await db.get(_Job, job_id)
    out = _json_or_none(job.output_json) if job else None
    file_str = out.get("file") if out else None
    if not file_str:
        return None
    from backend.api.videos import _safe_resolve_path
    try:
        path = _safe_resolve_path(file_str)
    except Exception:  # noqa: BLE001 — traversal guard rejected it
        return None
    return path if path.is_file() else None


async def poster_source(db: AsyncSession, key: str) -> Path | None:
    """The video file a poster should be extracted from, or None."""
    if not key.startswith(f"{KEY_JOB}:"):
        return None
    return await resolve_job_video(db, key.split(":", 1)[1])


def _resolve_parent(inp: dict) -> str | None:
    """Raw parent reference from a job's input, still store-unqualified.

    Resolved against the gathered item set in `_link_parents`, because the same
    id could name a job asset, a generated video or a download depending on
    which surface started the run.
    """
    for field in ("asset_id", "source_asset_id", "video_id", "downloaded_video_id"):
        val = inp.get(field)
        if isinstance(val, str) and val:
            return val
    return None


def _link_parents(items: list[dict]) -> None:
    """Turn raw parent ids into store-qualified keys, in place."""
    by_id: dict[str, str] = {}
    for it in items:
        by_id.setdefault(str(it["id"]), it["key"])
    for it in items:
        parent = it.get("parent_key")
        if not parent or ":" in parent:
            continue  # already qualified (generated → dl:<id>) or absent
        resolved = by_id.get(parent)
        it["parent_key"] = resolved if resolved and resolved != it["key"] else None


def _fill_titles(items: list[dict]) -> None:
    """Give unnamed tool outputs a readable label, in place.

    Two passes so a child can borrow a parent's name even when the parent is
    itself unnamed: the first pass names every item that can be named from its
    own producer, the second reads those names.
    """
    by_key = {it["key"]: it for it in items}
    # Lineage first: "<the clip> — Captions" beats "Captions — classic", because
    # what you are looking for is the clip.
    for it in items:
        if not it["title"]:
            parent = by_key.get(it["parent_key"] or "")
            if parent and parent.get("title"):
                it["title"] = f"{parent['title']} — {it['producer']}"
    for it in items:
        if not it["title"]:
            detail = it.get("_detail")
            it["title"] = f"{it['producer']} — {detail}" if detail else it["producer"]
    for it in items:
        it.pop("_detail", None)


# Background-music files. There is no music API here — tracks are files you
# drop into storage/music/ yourself — so the small presentation helpers the
# hosted variant imports from its music router live inline.
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def _track_display_name(filename: str) -> str:
    """A filename read as a title: drop the extension, restore the spaces."""
    stem = Path(filename).stem
    return stem.replace("_", " ").replace("-", " ").strip() or filename


def _track_genre(filename: str) -> str | None:
    """Genre by convention — `upbeat_something.mp3` → "upbeat".

    The music service already globs by genre substring, so the leading token is
    the closest thing to a genre these files carry.
    """
    stem = Path(filename).stem
    head = re.split(r"[_\-\s]", stem, maxsplit=1)[0].strip().lower()
    return head or None


def _track_items(skip_filenames: set[str]) -> list[dict]:
    from backend.services.music_service import MUSIC_DIR

    items = []
    try:
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        paths = sorted(MUSIC_DIR.iterdir())
    except OSError:
        return items
    p = classify("music_upload")
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in MUSIC_EXTS:
            continue
        if path.name in skip_filenames:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        items.append({
            "key": f"{KEY_TRACK}:{path.name}",
            "source": "music",
            "id": path.name,
            "media": p.media,
            "origin": p.origin,
            "producer_key": p.key,
            "producer": p.label,
            "title": _track_display_name(path.name),
            "thumb_url": None,
            "stream_url": f"/api/library/track/{path.name}",
            "aspect": None,
            "duration_seconds": None,
            "size_mb": round(st.st_size / (1024 * 1024), 2),
            "ext": path.suffix.lstrip(".") or None,
            "status": None,
            "niche": None,
            "platform": None,
            "genre": _track_genre(path.name),
            "hook_score": None,
            "virality_score": None,
            "parent_key": None,
            "created_at": _iso_from_mtime(st.st_mtime),
        })
    return items


def _iso_from_mtime(mtime: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None).isoformat()


async def gather(db: AsyncSession) -> list[dict]:
    """Every library item, unfiltered and unsorted."""
    generated = await _generated_items(db)
    downloaded = await _downloaded_items(db)
    jobs, music_copies = await _job_items(db)
    tracks = _track_items(music_copies)
    items = [*generated, *downloaded, *jobs, *tracks]
    _link_parents(items)
    _fill_titles(items)
    return items


def _csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _sorted(items: list[dict], sort: str) -> list[dict]:
    if sort == "oldest":
        return sorted(items, key=lambda i: i["created_at"] or "")
    if sort == "longest":
        return sorted(items, key=lambda i: i["duration_seconds"] or 0, reverse=True)
    if sort == "title":
        return sorted(items, key=lambda i: (i["title"] or "").lower())
    return sorted(items, key=lambda i: i["created_at"] or "", reverse=True)


def _matches(item: dict, *, media: list[str], origins: list[str],
             producers: list[str], q: str) -> bool:
    if media and item["media"] not in media:
        return False
    if origins and item["origin"] not in origins:
        return False
    if producers and item["producer_key"] not in producers:
        return False
    if q:
        haystack = f"{item['title']} {item['producer']} {item.get('niche') or ''}".lower()
        if q not in haystack:
            return False
    return True


def _count_by(items: Iterable[dict], field: str, keys: Iterable[str]) -> dict[str, int]:
    counts = {k: 0 for k in keys}
    for it in items:
        if it[field] in counts:
            counts[it[field]] += 1
    return counts


async def query(
    db: AsyncSession,
    *,
    media: str | None = None,
    origin: str | None = None,
    producer: str | None = None,
    q: str | None = None,
    sort: str = "newest",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Paged, faceted library page.

    Facet counts for each axis are computed with the OTHER axes applied, which
    is what makes them answer "what would I get if I picked this" rather than
    "what am I already looking at".
    """
    media_sel = _csv(media)
    origin_sel = _csv(origin)
    producer_sel = _csv(producer)
    needle = (q or "").strip().lower()
    sort = sort if sort in SORTS else "newest"
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    all_items = await gather(db)

    def subset(*, skip: str) -> list[dict]:
        return [
            it for it in all_items
            if _matches(
                it,
                media=[] if skip == "media" else media_sel,
                origins=[] if skip == "origin" else origin_sel,
                producers=[] if skip == "producer" else producer_sel,
                q=needle,
            )
        ]

    media_base = subset(skip="media")
    origin_base = subset(skip="origin")
    producer_base = subset(skip="producer")
    matched = _sorted(subset(skip=""), sort)

    producer_counts: dict[str, dict] = {}
    for it in producer_base:
        entry = producer_counts.setdefault(
            it["producer_key"], {"key": it["producer_key"], "label": it["producer"], "count": 0}
        )
        entry["count"] += 1

    return {
        "items": matched[offset:offset + limit],
        "total": len(matched),
        "limit": limit,
        "offset": offset,
        "facets": {
            "all": len(media_base),
            "media": _count_by(media_base, "media", MEDIA_ORDER),
            "origin": _count_by(origin_base, "origin", ORIGIN_ORDER),
            "producers": sorted(
                producer_counts.values(),
                key=lambda e: (-e["count"], e["label"].lower()),
            ),
        },
        "library_total": len(all_items),
    }


async def lineage(db: AsyncSession, key: str) -> dict[str, Any] | None:
    """One item's ancestry and its direct descendants.

    The drawer needs "made from" / "used to make" in BOTH views, and the grid
    deliberately doesn't load the whole tree, so this answers for a single key
    instead of making the page hold a lineage map it mostly won't use.
    """
    all_items = await gather(db)
    by_key = {it["key"]: it for it in all_items}
    item = by_key.get(key)
    if item is None:
        return None
    ancestors: list[dict] = []
    cur = item
    seen = {cur["key"]}
    while cur["parent_key"] and cur["parent_key"] in by_key:
        cur = by_key[cur["parent_key"]]
        if cur["key"] in seen:  # pragma: no cover — cycle guard
            break
        seen.add(cur["key"])
        ancestors.append(cur)
    children = _sorted([it for it in all_items if it["parent_key"] == key], "newest")
    return {"item": item, "ancestors": ancestors, "children": children}


async def families(db: AsyncSession, limit: int = 20, offset: int = 0, **filters) -> dict[str, Any]:
    """The same items grouped by what they were made from.

    Roots are the items nothing links from; a matched descendant pulls in its
    whole family so the view can show context, marking the members that fall
    outside the current filters rather than hiding them (a clip listed without
    the download it came from is the confusion this view exists to remove).
    """
    all_items = await gather(db)
    by_key = {it["key"]: it for it in all_items}
    children: dict[str, list[dict]] = {}
    for it in all_items:
        if it["parent_key"] and it["parent_key"] in by_key:
            children.setdefault(it["parent_key"], []).append(it)

    media_sel = _csv(filters.get("media"))
    origin_sel = _csv(filters.get("origin"))
    producer_sel = _csv(filters.get("producer"))
    needle = (filters.get("q") or "").strip().lower()
    matched_keys = {
        it["key"] for it in all_items
        if _matches(it, media=media_sel, origins=origin_sel,
                    producers=producer_sel, q=needle)
    }

    def root_of(item: dict) -> dict:
        cur = item
        seen = {cur["key"]}
        while cur["parent_key"] and cur["parent_key"] in by_key:
            nxt = by_key[cur["parent_key"]]
            if nxt["key"] in seen:  # pragma: no cover — cycle guard
                break
            seen.add(nxt["key"])
            cur = nxt
        return cur

    def subtree(key: str, seen: frozenset[str] = frozenset()) -> list[dict]:
        # Same cycle guard as root_of()/lineage(): _link_parents only breaks
        # self-loops, so a 2-cycle in recorded parents must not recurse forever.
        if key in seen:  # pragma: no cover — cycle guard
            return []
        seen = seen | {key}
        return [
            {"item": child, "children": subtree(child["key"], seen)}
            for child in _sorted(children.get(key, []), "newest")
        ]

    roots: list[dict] = []
    seen_roots: set[str] = set()
    for it in _sorted(all_items, "newest"):
        if it["key"] not in matched_keys:
            continue
        root = root_of(it)
        if root["key"] in seen_roots:
            continue
        seen_roots.add(root["key"])
        roots.append({"root": root, "children": subtree(root["key"])})

    with_kids = [r for r in roots if r["children"]]
    loners = [r["root"] for r in roots if not r["children"]]
    # Paged like the grid: a family carries its whole subtree, so an unbounded
    # response is the one place this view could hand the browser thousands of
    # rows at once.
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    page = with_kids[offset:offset + limit]
    # Only the loners belonging to THIS page's tail are useful; sending every
    # childless root on page 1 defeats the paging.
    loner_page = loners[offset:offset + limit] if offset < len(loners) else []
    keys_on_page = set()
    def _collect(nodes):
        for n in nodes:
            keys_on_page.add(n["item"]["key"])
            _collect(n["children"])
    for fam in page:
        keys_on_page.add(fam["root"]["key"])
        _collect(fam["children"])
    for l in loner_page:
        keys_on_page.add(l["key"])
    return {
        "families": page,
        "loners": loner_page,
        # Scoped to what this page renders — the caller only needs to know which
        # of the rows in front of it fall outside the filters.
        "matched_keys": sorted(matched_keys & keys_on_page),
        "family_total": len(with_kids),
        "loner_total": len(loners),
        "limit": limit,
        "offset": offset,
        "total": len(matched_keys),
    }
