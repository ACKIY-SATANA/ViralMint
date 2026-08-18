# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The embedded Motion Graphics studio: its install gate, the composition
contract it enforces, and the archive rule that keeps work from being lost.

Three things here are load-bearing and easy to break by accident:

  * Every endpoint answers "not installed" as a STRUCTURED 200, never a 500.
    The UI keys on that to offer the install; a stack trace is a dead end.
  * A composition that references a CDN passes every visual check and then
    renders blank, because rendering is offline by design. That has to be
    caught before it is set live, not after someone waits for a render.
  * Replacing the live composition ARCHIVES the old one. The studio has a
    single mutable index.html, so overwrite-in-place means a bad submission
    destroys the last good composition.
"""
from __future__ import annotations

import pytest

from backend.api import generate as gen_api
from backend.core.exceptions import HyperFramesNotInstalledError
from backend.services.studio_service import StudioService


# ── The install gate ──────────────────────────────────────────────────────────

@pytest.fixture
def not_installed(monkeypatch):
    from backend.services.hyperframes_service import HyperFramesService
    monkeypatch.setattr(HyperFramesService, "is_installed", classmethod(lambda cls: False))


async def test_start_answers_with_an_actionable_envelope_not_an_error(not_installed):
    """A missing optional plugin is a state, not a failure. The envelope carries
    the code the UI switches on AND the place to go — so the answer to "why is
    this page empty" is a button, not a log line."""
    res = await gen_api.motion_studio_start()
    assert res["ok"] is False
    assert res["error_code"] == "hyperframes_not_installed"
    assert res["action_url"] == "/settings#motion-graphics"


async def test_the_envelope_has_exactly_one_source(not_installed):
    """Two copies of this wording drift, and the drifted one is always the one
    the user reads."""
    assert await gen_api.motion_studio_start() == HyperFramesNotInstalledError.ENVELOPE


async def test_background_sync_degrades_silently_rather_than_erroring(not_installed):
    """sync-renders is polled on a timer. Surfacing an install error from a
    background poll would toast the user every six seconds."""
    assert await gen_api.motion_studio_sync_renders() == {"imported": 0, "ids": []}


async def test_listing_and_cleanup_degrade_to_empty_when_not_installed(not_installed):
    assert await gen_api.motion_studio_list_comps() == {"comps": []}
    assert await gen_api.motion_studio_cleanup(
        gen_api.MotionCleanupRequest()) == {"removed": [], "freed_mb": 0}


# ── The composition contract ──────────────────────────────────────────────────

VALID = ('<html><body>'
         '<div data-composition-id="x" data-width="1080" data-height="1920"></div>'
         '<script src="assets/gsap.min.js"></script>'
         '<script>window.__timelines = {};</script>'
         '</body></html>')


def test_a_composition_needs_a_stage_and_a_timeline():
    issues = StudioService._composition_issues("<html><body>hi</body></html>")
    assert any("data-composition-id" in i for i in issues)
    assert any("__timelines" in i for i in issues)


def test_a_valid_composition_has_nothing_to_report():
    assert StudioService._composition_issues(VALID) == []


@pytest.mark.parametrize("bad", [
    '<script src="https://cdn.example.com/gsap.js"></script>',
    '<link href="https://fonts.example.com/x.css" rel="stylesheet">',
    '<style>@import "https://example.com/a.css";</style>',
    '<style>body{background:url(https://example.com/bg.png)}</style>',
    '<img src="https://example.com/hero.png">',
    '<video src="https://example.com/clip.mp4"></video>',
])
def test_external_references_are_refused(bad):
    """Rendering happens offline against local files. Every one of these
    previews perfectly in a browser that has a network and then renders blank
    or aborts on the render host — the worst kind of bug to debug, because the
    thing you are looking at works."""
    issues = StudioService._composition_issues(VALID.replace("</body>", bad + "</body>"))
    assert any("external" in i.lower() for i in issues), bad


def test_a_data_uri_is_not_an_external_reference():
    """The guard must not fire on inlined content. An SVG data: URI commonly
    carries an xmlns URL in its payload, and rejecting those would make the
    check useless by making it unbelievable."""
    inline = VALID.replace(
        "</body>",
        '<img src="data:image/svg+xml;utf8,'
        '<svg xmlns=\'http://www.w3.org/2000/svg\'></svg>"></body>')
    assert StudioService._composition_issues(inline) == []


def test_a_missing_local_asset_is_treated_as_fatal():
    """Distinguishes "this could be better" from "this cannot come out right".
    A missing media file aborts the render, or ships a blank clip with the
    coverage gate off — telling someone "it should still render" over one of
    these is how a good composition becomes a mystery failure."""
    assert StudioService.has_fatal_issue(["hero.mp4 not found in the project"]) is True
    assert StudioService.has_fatal_issue(["Contrast below AA on the subtitle"]) is False
    assert StudioService.has_fatal_issue(None) is False


# ── Archive semantics ─────────────────────────────────────────────────────────

@pytest.fixture
def project(tmp_path, monkeypatch):
    import backend.services.studio_service as svc
    proj = tmp_path / "project"
    proj.mkdir()
    monkeypatch.setattr(svc, "_project_dir", lambda: proj)
    return proj


def test_replacing_a_composition_preserves_the_previous_one(project):
    """index.html is a single mutable file. Overwriting in place means one bad
    submission destroys the last thing that worked."""
    project.joinpath("index.html").write_text(
        '<html><div data-composition-id="my-work"></div></html>')
    name = StudioService._archive_current(project)
    assert name and name.startswith("comp_") and name.endswith(".html")
    assert "my-work" in (StudioService.archive_dir() / name).read_text()


def test_the_seed_composition_is_not_worth_archiving(project):
    """Otherwise every first-time user accumulates identical copies of the
    starter template and has to clean them up by hand."""
    project.joinpath("index.html").write_text('<html><div data-composition-id="hook"></div></html>')
    assert StudioService._archive_current(project) is None


def test_archives_live_in_a_subdirectory_not_the_project_root(project):
    """Two root-level files carrying data-composition-id make the engine report
    multiple entry points, which also makes the LIVE composition impossible to
    lint. The archive has to be somewhere the project scan does not treat as an
    entry point."""
    project.joinpath("index.html").write_text('<html><div data-composition-id="a"></div></html>')
    StudioService._archive_current(project)
    assert not list(project.glob("comp_*.html"))
    assert len(list(StudioService.archive_dir().glob("comp_*.html"))) == 1


def test_a_composition_filename_cannot_escape_the_project(project):
    """The name arrives from a request. `..` in a filename that becomes a path
    is the oldest bug there is."""
    assert StudioService._safe_name("../../etc/passwd") == "passwd.html"
    assert StudioService._safe_name("comp_deadbeef.html") == "comp_deadbeef.html"
    assert StudioService.comp_path("../../../evil.html").parent in (
        project, StudioService.archive_dir())


def test_listing_reports_archives_newest_first(project):
    import os, time
    arch = StudioService.archive_dir()
    arch.mkdir(parents=True)
    for i, name in enumerate(("comp_aaaaaaaa.html", "comp_bbbbbbbb.html")):
        f = arch / name
        f.write_text("<html></html>")
        os.utime(f, (time.time() + i * 10, time.time() + i * 10))
    assert [c["file"] for c in StudioService.list_comps()] == [
        "comp_bbbbbbbb.html", "comp_aaaaaaaa.html"]


def test_cleanup_removes_only_what_it_was_asked_to(project):
    arch = StudioService.archive_dir()
    arch.mkdir(parents=True)
    keep, drop = arch / "comp_11111111.html", arch / "comp_22222222.html"
    for f in (keep, drop):
        f.write_text("<html></html>" * 100)
    project.joinpath("index.html").write_text("<html>live</html>")

    res = StudioService.cleanup_comps([drop.name])
    assert res["removed"] == [drop.name]
    assert keep.exists() and not drop.exists()
    # The live composition is never a cleanup target — it cannot be recovered.
    assert project.joinpath("index.html").exists()


# ── Staged assets ─────────────────────────────────────────────────────────────

def test_an_unsupported_asset_type_is_refused(project):
    with pytest.raises(ValueError, match="Unsupported asset type"):
        StudioService.stage_asset("payload.exe", b"MZ")


def test_an_oversized_asset_is_refused_before_it_is_written(project):
    with pytest.raises(ValueError, match="too large"):
        StudioService.stage_asset("big.mp4", b"x" * (StudioService.ASSET_MAX_BYTES + 1))
    assert not (project / "assets").exists() or not list((project / "assets").iterdir())


def test_a_staged_filename_is_sanitised_and_made_unique(project):
    """The name comes from the user's disk. It ends up inside an HTML src
    attribute, and two files called photo.png must not become one."""
    a = StudioService.stage_asset("../../my photo!.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    b = StudioService.stage_asset("../../my photo!.png", b"\x89PNG\r\n\x1a\n" + b"1" * 64)
    assert a["file"] != b["file"]
    for res in (a, b):
        assert res["type"] == "image"
        assert "/" not in res["file"] and ".." not in res["file"]
        assert (project / "assets" / res["file"]).exists()


def test_staging_a_library_file_never_consumes_the_original(project, tmp_path):
    """Assets come from the user's own library. Staging is a read; if it ever
    becomes a move, using a video in a composition deletes it from everywhere
    else it was."""
    src = tmp_path / "source.mp4"
    src.write_bytes(b"\x00" * 2048)
    res = StudioService.stage_asset_from_path(src, "Source clip")
    assert src.exists(), "the library original must survive staging"
    assert (project / "assets" / res["file"]).exists()
