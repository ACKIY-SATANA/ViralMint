# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Library taxonomy — the ONE place a stored row is classified.

Every durable file you own answers two independent questions:

    media  = video | image | audio | doc     — what the file IS
    origin = created | edited | imported     — where it CAME FROM

Those axes are orthogonal, which is the whole point. The Library page used to
tab on a mix of the two (Scout / Downloaded / Generated is an origin axis), so
an item with a true answer on both — a downloaded mp3, a voice-over, audio
pulled out of your own video — had to be filed under one and was lost from the
other. Splitting them means every row lands in exactly one place per axis:

    downloaded mp3                     → audio · imported
    voice-over                         → audio · created
    a clip cut from a download         → video · edited    (parent = that download)

`PRODUCERS` maps the keys already stored on rows — `generated_videos.source_type`
and `jobs.job_type` — onto (label, media, origin). It is the single source for
that mapping: the Library index, the Library page and the API all resolve
through here rather than keeping their own lists.

**A file-writing job_type missing from this map is invisible in the Library.**
That is not a hypothetical — before this existed, every one of the twenty tools
wrote its output to disk and showed it on no surface at all. `tests/
test_library_taxonomy.py` fails the build when a tool endpoint creates a job
type this map doesn't cover.

The frontend mirror is `frontend/src/components/librarynext/assetModel.js` —
same keys, same labels, and a drift test compares the two literally, because
two hand-maintained copies of one map is how they end up disagreeing.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Media ───────────────────────────────────────────────────────────────────
MEDIA_VIDEO = "video"
MEDIA_IMAGE = "image"
MEDIA_AUDIO = "audio"
MEDIA_DOC = "doc"
MEDIA_ORDER: tuple[str, ...] = (MEDIA_VIDEO, MEDIA_IMAGE, MEDIA_AUDIO, MEDIA_DOC)

MEDIA_LABELS = {
    MEDIA_VIDEO: "Video",
    MEDIA_IMAGE: "Image",
    MEDIA_AUDIO: "Audio",
    # Not "Text": this bucket is everything that is not video/image/audio —
    # transcripts, subtitle files, the chapters .txt, the metadata .json.
    MEDIA_DOC: "Files",
}

# ── Origin ──────────────────────────────────────────────────────────────────
ORIGIN_CREATED = "created"
ORIGIN_EDITED = "edited"
ORIGIN_IMPORTED = "imported"
# UI order. Edited sits before Imported deliberately: most people import rarely
# but edit constantly, so the middle chip is the one they reach for.
ORIGIN_ORDER: tuple[str, ...] = (ORIGIN_CREATED, ORIGIN_EDITED, ORIGIN_IMPORTED)

# Labels, not keys. The key stays `imported` — it appears in URLs
# (`?origin=imported`), in the API contract and in the picker payloads — but the
# WORD overclaims: that bucket is overwhelmingly videos downloaded from links,
# not files someone imported from disk, so "Imported" read as a claim about the
# handful. "Sources" covers all three and matches the "By source" grouping.
ORIGIN_LABELS = {
    ORIGIN_CREATED: "Created",
    ORIGIN_EDITED: "Edited",
    ORIGIN_IMPORTED: "Sources",
}

ORIGIN_HINTS = {
    ORIGIN_CREATED: "Made in ViralMint from a script, a brief or a prompt",
    ORIGIN_EDITED: (
        "Made from something already in your library — a clip, a reframe, "
        "burned captions, a merge"
    ),
    ORIGIN_IMPORTED: (
        "Raw material that came from outside — downloaded from a link, imported "
        "from your disk, or uploaded"
    ),
}


@dataclass(frozen=True)
class Producer:
    """What made a file, and therefore where it belongs on both axes."""

    key: str
    label: str
    media: str
    origin: str


def _p(key: str, label: str, media: str, origin: str) -> Producer:
    return Producer(key=key, label=label, media=media, origin=origin)


# ── The map ─────────────────────────────────────────────────────────────────
# Keys are the values already stored on rows: `generated_videos.source_type`
# for renders, `jobs.job_type` for everything a tool wrote.
PRODUCERS: dict[str, Producer] = {
    # Made here, from nothing you already owned.
    # `smart_video` is the fallback for a GeneratedVideo with no source_type —
    # the Stock Video pipeline predates the column.
    "smart_video": _p("smart_video", "Stock Video", MEDIA_VIDEO, ORIGIN_CREATED),
    "motion_graphics": _p("motion_graphics", "Motion Graphics", MEDIA_VIDEO, ORIGIN_CREATED),
    "tool:voiceover": _p("tool:voiceover", "Voice-over", MEDIA_AUDIO, ORIGIN_CREATED),

    # Made FROM something you already had. A clip is the clearest case: it is
    # not a new video, it is a piece of one you downloaded.
    "clip_extraction": _p("clip_extraction", "Clipper", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:captions": _p("tool:captions", "Captions", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:reframe": _p("tool:reframe", "Reframe", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:trim": _p("tool:trim", "Trim", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:speed": _p("tool:speed", "Speed", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:transform": _p("tool:transform", "Transform", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:merge_clips": _p("tool:merge_clips", "Merge", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:watermark": _p("tool:watermark", "Watermark", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:auto_zoom": _p("tool:auto_zoom", "Auto zoom", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:remove_silence": _p("tool:remove_silence", "Silence cut", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:compress": _p("tool:compress", "Compress", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:crop": _p("tool:crop", "Crop", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:translate": _p("tool:translate", "Translate", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:music_visualizer": _p("tool:music_visualizer", "Visualizer", MEDIA_VIDEO, ORIGIN_EDITED),
    # Audio Enhance returns an mp3 for a bare audio input and an mp4 when the
    # input carried a picture. The index lets the file extension win over this
    # declaration for exactly that reason; `video` is the common case.
    "tool:audio_enhance": _p("tool:audio_enhance", "Enhanced", MEDIA_VIDEO, ORIGIN_EDITED),
    "tool:gif": _p("tool:gif", "GIF", MEDIA_IMAGE, ORIGIN_EDITED),
    "tool:subtitle_export": _p("tool:subtitle_export", "Subtitles", MEDIA_DOC, ORIGIN_EDITED),
    "tool:auto_chapters": _p("tool:auto_chapters", "Chapters", MEDIA_DOC, ORIGIN_EDITED),
    "tool:metadata": _p("tool:metadata", "Metadata", MEDIA_DOC, ORIGIN_EDITED),

    # Came from outside. `download` / `import` describe DownloadedVideo rows
    # (the download JOB is the act; the row is the file), and `music_upload` is
    # a file sitting in the music library directory.
    "download": _p("download", "Download", MEDIA_VIDEO, ORIGIN_IMPORTED),
    "import": _p("import", "Import", MEDIA_VIDEO, ORIGIN_IMPORTED),
    "music_upload": _p("music_upload", "Upload", MEDIA_AUDIO, ORIGIN_IMPORTED),
}

# Producer keys that describe a DownloadedVideo row rather than a job.
DOWNLOADED_PRODUCER_KEYS = ("download", "import")

# Job types that write NO durable file. They belong in Activity (a failed
# download is exactly what that panel is for) and nowhere in the Library.
# Listed here so `classify()` returning None is a decision on record rather
# than an oversight — the difference between the two is the invisibility bug.
NON_LIBRARY_JOB_TYPES = frozenset({
    "scout", "news_scout", "news_save", "analyze", "channel_analysis",
    "generate",          # registers a generated_videos row, which IS the item
    "extract_clips",     # ditto, one row per clip
    "motion_render",     # ditto
    "motion_compose",    # authors a composition, renders nothing
    "upload",            # publishes an existing video, produces no new file
    "tool:hook_analysis",  # structured text on the job row, no file
})

# ⚠️ `download` is deliberately NOT in that set even though a download JOB
# produces no library item of its own. The string means two different things
# depending on which column it came from: as a `job_type` it is the ACT of
# downloading, and as a producer key above it describes the DownloadedVideo ROW
# that the act created — which is very much a library item. A key cannot be in
# PRODUCERS and in NON_LIBRARY_JOB_TYPES at once (a test enforces it), so the
# job side is settled where it actually matters instead: the index only ever
# projects a job that recorded a `file` in its output, and a download records
# its result on the row rather than on the job.


def normalize_producer_key(raw: str | None) -> str:
    """Fold a stored key onto its canonical form.

    Only one transformation: hyphens become underscores inside the namespace,
    so `tool:auto-zoom` and `tool:auto_zoom` are the same producer. Endpoints
    are hyphenated and job types are not, and both spellings have reached this
    map at different times.
    """
    key = (raw or "").strip()
    if ":" in key:
        head, _, tail = key.partition(":")
        return f"{head}:{tail.replace('-', '_')}"
    return key.replace("-", "_")


def classify(raw: str | None, media_override: str | None = None) -> Producer | None:
    """Resolve a stored key to its Producer, or None when it isn't a library item.

    None means "no durable file the user owns" — see `NON_LIBRARY_JOB_TYPES`.
    Callers skip those rows rather than guessing.

    `media_override` covers the one case a key cannot answer on its own: a
    download whose file is audio-only. That row is a `download` (video by
    default) and is genuinely audio — the axis collision this whole model
    exists to resolve.
    """
    p = PRODUCERS.get(normalize_producer_key(raw))
    if p is None:
        return None
    if media_override and media_override != p.media:
        return Producer(key=p.key, label=p.label, media=media_override, origin=p.origin)
    return p


def taxonomy_payload() -> dict:
    """Axis labels + the producer map, served at `/api/library/taxonomy`.

    The UI renders its chips and tool filter from this rather than carrying its
    own copy of the words, so a relabel lands everywhere at once.
    """
    return {
        "media": [{"key": k, "label": MEDIA_LABELS[k]} for k in MEDIA_ORDER],
        "origins": [
            {"key": k, "label": ORIGIN_LABELS[k], "hint": ORIGIN_HINTS[k]}
            for k in ORIGIN_ORDER
        ],
        "producers": [
            {"key": p.key, "label": p.label, "media": p.media, "origin": p.origin}
            for p in PRODUCERS.values()
        ],
    }
