# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The taxonomy's guards — the ones that stop the invisibility bug returning.

Before the Library index existed, every tool wrote its output to disk and no
surface showed it. The fix is a map from stored key → (label, media, origin),
and a map is only as good as the guarantee that it covers everything. These
tests are that guarantee:

  1. every job type a tool endpoint creates is either classified or explicitly
     recorded as producing no file — never merely absent
  2. a producer's declared media matches the extension its runner writes
  3. the frontend mirror agrees with this map, entry for entry
  4. a key is a library item or an activity-only row, never both

(1) and (3) are the two that would have caught the original bug.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _emitted_job_types() -> set[str]:
    """Every job type the tool endpoints actually create.

    Read out of the source rather than listed here on purpose: a hand-written
    copy is one more thing to forget when a tool is added, and forgetting is
    precisely the failure being guarded against.
    """
    src = (REPO / "backend/api/tools.py").read_text(encoding="utf-8")
    return set(re.findall(r'create_job\(\s*"(tool:[a-z_]+)"', src))


def test_every_tool_endpoint_job_type_is_accounted_for():
    """A tool that writes a file the map doesn't know is invisible in the
    Library — the whole reason this module exists. Being deliberately
    file-less is fine, but it has to be SAID, in NON_LIBRARY_JOB_TYPES."""
    from backend.services.library_taxonomy import NON_LIBRARY_JOB_TYPES, classify

    emitted = _emitted_job_types()
    assert emitted, "parsed no job types out of backend/api/tools.py — did its shape change?"

    unaccounted = sorted(
        jt for jt in emitted
        if classify(jt) is None and jt not in NON_LIBRARY_JOB_TYPES
    )
    assert unaccounted == [], (
        f"job types with no taxonomy entry: {unaccounted} — add them to "
        "PRODUCERS, or to NON_LIBRARY_JOB_TYPES if they genuinely write no file"
    )


def test_no_producer_claims_a_job_type_that_is_also_declared_file_less():
    """The two lists answer opposite questions; being in both is a contradiction
    that renders as one (no tile, yet a link to the item it 'didn't' make)."""
    from backend.services.library_taxonomy import NON_LIBRARY_JOB_TYPES, PRODUCERS

    both = sorted(set(PRODUCERS) & set(NON_LIBRARY_JOB_TYPES))
    assert both == [], f"declared as producing a library item AND as producing none: {both}"


def test_producer_media_matches_the_extension_its_runner_writes():
    """The index lets the file extension win over this map, so a mismatch is
    invisible on the tiles and shows up only in /api/library/taxonomy — which
    the UI renders its filters from."""
    from backend.services.library_index import _suffix_media
    from backend.services.library_taxonomy import PRODUCERS

    # producer key → the suffix its runner writes, for the runners that are
    # unambiguous. Tools that emit either media (audio_enhance, transform) are
    # deliberately absent: for those the extension is the only truth.
    KNOWN_SUFFIX = {
        "tool:captions": ".mp4",
        "tool:merge_clips": ".mp4",
        "tool:gif": ".gif",
        "tool:metadata": ".json",
        "tool:auto_chapters": ".txt",
        "tool:subtitle_export": ".srt",
        "tool:voiceover": ".mp3",
    }
    wrong = {
        key: {"declared": PRODUCERS[key].media, "from_extension": _suffix_media(suffix)}
        for key, suffix in KNOWN_SUFFIX.items()
        if _suffix_media(suffix) != PRODUCERS[key].media
    }
    assert wrong == {}, f"producer media disagrees with the artifact: {wrong}"


def test_the_frontend_taxonomy_mirror_does_not_drift():
    """One taxonomy, two files.

    library_taxonomy.py and assetModel.js both carry the producer map because
    one side classifies and the other renders. Nothing but this test compares
    them, and without it a producer can be added, relabelled or re-classified
    on one side while the other quietly disagrees — the Library would then file
    a row one way and label it another.

    The JS map is parsed as text deliberately: no node, no build step, and the
    failure quotes the literal a reviewer reads.
    """
    from backend.services.library_taxonomy import PRODUCERS

    js = (REPO / "frontend/src/components/librarynext/assetModel.js").read_text(encoding="utf-8")
    block = js.split("export const PRODUCERS = {", 1)[1].split("\n}\n", 1)[0]
    entry = re.compile(
        r'^\s*"?([A-Za-z_][\w:]*)"?:\s*\{\s*label:\s*"([^"]*)",\s*'
        r'media:\s*"(\w+)",\s*origin:\s*"(\w+)"\s*\},',
        re.M,
    )
    mirror = {k: (label, media, origin) for k, label, media, origin in entry.findall(block)}
    assert mirror, "could not parse PRODUCERS out of assetModel.js — did its shape change?"

    backend = {k: (p.label, p.media, p.origin) for k, p in PRODUCERS.items()}
    missing_in_js = sorted(set(backend) - set(mirror))
    extra_in_js = sorted(set(mirror) - set(backend))
    disagree = {
        k: {"py": backend[k], "js": mirror[k]}
        for k in sorted(set(backend) & set(mirror))
        if backend[k] != mirror[k]
    }
    assert not missing_in_js, f"producers only the backend knows: {missing_in_js}"
    assert not extra_in_js, f"producers only the frontend knows: {extra_in_js}"
    assert not disagree, f"label/media/origin disagree between the two maps: {disagree}"


def test_agent_style_hyphen_spellings_fold_onto_the_underscore_twin():
    """Endpoints are hyphenated and job types are not; both spellings have
    reached this map at different times, so they must classify identically."""
    from backend.services.library_taxonomy import classify, normalize_producer_key

    assert normalize_producer_key("tool:auto-zoom") == "tool:auto_zoom"
    assert classify("tool:auto-zoom") == classify("tool:auto_zoom")
    assert classify("tool:merge-clips").key == "tool:merge_clips"


def test_an_audio_only_download_is_audio_not_video():
    """The axis collision the whole model exists to resolve: the row is a
    `download` (video by default) and the file is genuinely audio."""
    from backend.services.library_taxonomy import classify

    p = classify("download", media_override="audio")
    assert p.media == "audio"
    assert p.origin == "imported"
    # …and the override must not leak into the shared map.
    assert classify("download").media == "video"


def test_an_unclassified_key_returns_none_rather_than_guessing():
    from backend.services.library_taxonomy import classify

    assert classify("tool:not_a_real_tool") is None
    assert classify(None) is None
    assert classify("") is None
