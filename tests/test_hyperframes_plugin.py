# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The Motion Graphics plugin: its install gate, its version gate, and the
GSAP runtime it has to stage for a composition to animate at all.

Motion Graphics is an on-demand plugin — a portable Node runtime plus an npm
package, downloaded into the user's data dir on opt-in. The install has one
property worth pinning hard: the on-disk marker is the ONLY thing that
distinguishes "never installed" from "installed, but the app has moved on to a
newer pinned engine". Lose that distinction and a user who should be offered a
quick in-place Update is told to download hundreds of megabytes again.

The other half is GSAP. Compositions are GSAP-driven, GSAP is not a dependency
of the hyperframes package, and it ships under GreenSock's own licence rather
than a free-software one — so it is not vendored here. It is installed on the
user's machine and copied into each staged project instead, which makes
"is it actually there" a render-time failure mode that deserves its own test:
without the file a composition renders a blank frame and exits 0.
"""
from __future__ import annotations

import json

import pytest

from backend.services import hyperframes_contract as hc
from backend.services.hyperframes_service import HyperFramesService
from backend.services.motion_render_service import (
    MotionRenderService, _composition_aspect, _templates_src_dir,
)
from backend.core.exceptions import HyperFramesNotInstalledError, MotionRenderError


# ── The bundled template ──────────────────────────────────────────────────────

def test_bundled_kinetic_hook_template_is_shipped():
    """The one template the install smoke-render and the Settings test button
    both depend on. If this moves, the install stops being verifiable."""
    tpl = _templates_src_dir() / "kinetic_hook"
    assert (tpl / "index.html").exists()
    manifest = json.loads((tpl / "manifest.json").read_text())
    assert manifest["id"] == "kinetic_hook"
    # Every variable the smoke render passes must be declared, or HyperFrames
    # silently renders the template's own defaults and the test proves nothing.
    declared = {v["id"] for v in manifest["variables"]}
    assert {"kicker", "title", "subtitle", "accent", "bg"} <= declared


def test_gsap_is_not_vendored_into_the_repository():
    """A licence guard, not a style preference.

    GSAP is distributed under GreenSock's Standard License. Committing it into
    an AGPL source tree would put a non-free file in the middle of a copyleft
    work, so it is fetched as an npm dependency at install time instead. If
    someone "fixes" a blank render by dropping gsap.min.js into the template,
    this fails and says why.
    """
    tpl = _templates_src_dir() / "kinetic_hook"
    assert not list(tpl.rglob("gsap*.js")), (
        "GSAP must not be committed to this repository — it is installed as an "
        "npm dependency and staged per render (see hyperframes_contract)."
    )
    # …and the template must still link it, so staging has something to satisfy.
    assert 'src="assets/gsap.min.js"' in (tpl / "index.html").read_text()


# ── The version gate ──────────────────────────────────────────────────────────

def _write_marker(tmp_path, **overrides):
    payload = {
        "hyperframes_version": hc.HYPERFRAMES_NPM_VERSION,
        "node_version": hc.NODE_VERSION,
        "gsap_version": hc.GSAP_NPM_VERSION,
        "installed_at": 0,
    }
    payload.update(overrides)
    (tmp_path / ".installed").write_text(json.dumps(payload))


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """A motion dir that looks installed: node binary, CLI, matching marker.

    Nested under tmp_path rather than being tmp_path, so a test can also place
    the engine's shared cache OUTSIDE the directory uninstall removes — which
    is the whole point of the footprint tests below.
    """
    import backend.services.hyperframes_service as svc

    tmp_path = tmp_path / "motion"
    tmp_path.mkdir()
    node = tmp_path / "node" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    cli = tmp_path / "hyperframes" / "node_modules" / "hyperframes" / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("//")

    monkeypatch.setattr(svc, "_motion_dir", lambda: tmp_path)
    monkeypatch.setattr(svc, "_node_bin", lambda: node)
    monkeypatch.setattr(svc, "_cli_js", lambda: cli)
    monkeypatch.setattr(svc, "_marker_path", lambda: tmp_path / ".installed")
    return tmp_path


def test_is_installed_requires_a_matching_marker(fake_install):
    assert HyperFramesService.is_installed() is False, "no marker → not installed"
    _write_marker(fake_install)
    assert HyperFramesService.is_installed() is True


def test_a_stale_engine_version_reads_as_update_available_not_missing(fake_install):
    """The distinction the marker exists for.

    A user upgrading the app has Node and the CLI on disk from the previous
    pinned engine. Reporting that as "not installed" costs them the full
    download; reporting it as update_available reuses everything but the package.
    """
    _write_marker(fake_install, hyperframes_version="0.0.1")
    state = HyperFramesService.get_install_state()
    assert state["installed"] is False
    assert state["update_available"] is True
    assert state["installed_version"] == "0.0.1"
    assert state["hyperframes_version"] == hc.HYPERFRAMES_NPM_VERSION


def test_a_missing_marker_reads_as_a_first_install(fake_install):
    state = HyperFramesService.get_install_state()
    assert state["installed"] is False
    assert state["update_available"] is False
    assert state["installed_version"] is None


def test_a_corrupt_marker_does_not_crash_the_status_card(fake_install):
    (fake_install / ".installed").write_text("{not json")
    assert HyperFramesService.is_installed() is False
    # The Settings card polls this every 2s; an exception here would leave the
    # user looking at a spinner forever with no way to reinstall.
    assert HyperFramesService.get_install_state()["installed"] is False


# ── The install gate ──────────────────────────────────────────────────────────

async def test_render_refuses_before_install(monkeypatch):
    monkeypatch.setattr(HyperFramesService, "is_installed", classmethod(lambda cls: False))
    with pytest.raises(HyperFramesNotInstalledError):
        await MotionRenderService.render("kinetic_hook", {})


async def test_render_rejects_an_unknown_template(monkeypatch):
    monkeypatch.setattr(HyperFramesService, "is_installed", classmethod(lambda cls: True))
    with pytest.raises(MotionRenderError, match="Unknown motion template"):
        await MotionRenderService.render("no_such_template", {})


# ── GSAP staging ──────────────────────────────────────────────────────────────

def test_staging_fails_loudly_when_gsap_is_missing(tmp_path, monkeypatch):
    """Without the runtime a composition renders a blank frame and exits 0.

    That is the worst possible failure shape — a plausible file, no error — so
    the missing dependency has to be caught before the CLI is ever invoked.
    """
    from backend.config import settings
    monkeypatch.setattr(type(settings), "MOTION_DIR",
                        property(lambda self: tmp_path), raising=False)
    with pytest.raises(MotionRenderError, match="GSAP"):
        MotionRenderService._stage_gsap(tmp_path / "project")


def test_staging_copies_gsap_where_the_composition_links_it(tmp_path, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(type(settings), "MOTION_DIR",
                        property(lambda self: tmp_path), raising=False)
    gsap = hc.resolve_gsap_js(tmp_path / "hyperframes")
    gsap.parent.mkdir(parents=True)
    gsap.write_text("/* gsap */")

    project = tmp_path / "project"
    project.mkdir()
    MotionRenderService._stage_gsap(project)
    # The path is not free to change: templates link it relatively.
    assert (project / "assets" / "gsap.min.js").read_text() == "/* gsap */"


# ── Aspect correction ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("w,h,expected", [
    (1080, 1920, "9:16"),
    (1920, 1080, "16:9"),
    (1080, 1080, "1:1"),
])
def test_the_composition_decides_its_own_aspect(tmp_path, w, h, expected):
    """The caller passes what it believes; the artifact carries the truth.

    HyperFrames hard-fails a resolution whose aspect disagrees with the
    composition, so trusting a stale caller value turns a good composition into
    a failed render.
    """
    (tmp_path / "index.html").write_text(
        f'<div data-composition-id="x" data-width="{w}" data-height="{h}"></div>')
    assert _composition_aspect(tmp_path, "16:9") == expected


def test_an_unreadable_composition_leaves_the_requested_aspect_alone(tmp_path):
    assert _composition_aspect(tmp_path, "9:16") == "9:16"


def test_every_aspect_has_a_supersample_partner():
    """Supersampling looks up "<aspect>-4k". A base aspect without one silently
    renders at 1080p while the logs claim it supersampled — which is how 1:1 was
    the one aspect that never got sharper for several engine versions."""
    base = [a for a in hc.ASPECT_RESOLUTION if not a.endswith("-4k")]
    assert base, "sanity"
    for aspect in base:
        assert f"{aspect}-4k" in hc.ASPECT_RESOLUTION, aspect


# ── The subprocess contract ───────────────────────────────────────────────────

def test_render_argv_is_a_list_never_a_shell_string(tmp_path):
    """User- and AI-authored text reaches this argv (titles, variables JSON).
    Passing it through a shell would make a headline an injection vector."""
    argv = hc.render_argv(tmp_path / "node", tmp_path / "cli.js", tmp_path,
                          tmp_path / "out.mp4", "portrait", 30, "standard",
                          json.dumps({"title": "a; rm -rf /"}))
    assert all(isinstance(a, str) for a in argv)
    assert argv[2] == "render"
    assert "--variables" in argv
    # The dangerous string is one argument, not a fragment of a command line.
    assert '{"title": "a; rm -rf /"}' in argv


def test_snapshot_never_lets_the_engine_call_its_own_vision_model():
    """`--describe` defaults ON whenever GEMINI_API_KEY is in the environment.
    Left implicit, capturing frames would ship them to a provider the user
    never chose."""
    for argv in (
        hc.snapshot_argv(hc.Path("n"), hc.Path("c"), hc.Path("p"), hc.Path("o")),
        hc.snapshot_at_argv(hc.Path("n"), hc.Path("c"), hc.Path("p"), hc.Path("o"), [1.0]),
    ):
        assert argv[argv.index("--describe") + 1] == "false"


def test_the_studio_preview_is_pinned_to_loopback():
    """The preview server has no auth of its own. The CLI has no --host flag,
    so this env pin is the only thing standing between an authoring surface and
    the local network if the engine's default ever changes."""
    assert hc.STUDIO_HOST == "127.0.0.1"
    assert hc.base_env()["HYPERFRAMES_PREVIEW_HOST"] == "127.0.0.1"


def test_base_env_disables_telemetry_and_update_checks():
    env = hc.base_env()
    assert env["HYPERFRAMES_NO_TELEMETRY"] == "1"
    assert env["HYPERFRAMES_NO_UPDATE_CHECK"] == "1"


# ── Footprint accounting ──────────────────────────────────────────────────────

def test_the_engines_own_chrome_cache_is_reported_not_hidden(fake_install, monkeypatch, tmp_path):
    """HyperFrames downloads its headless Chrome to a HOME-anchored path with no
    env override, so ~200 MB of what our install caused lands outside the
    directory uninstall removes. Telling the user "350 MB on disk" and then
    reclaiming 150 is the kind of small dishonesty that makes people distrust an
    uninstaller, so the state reports the two footprints separately.
    """
    import backend.services.hyperframes_service as svc
    cache = tmp_path / "engine-cache"
    (cache / "chrome").mkdir(parents=True)
    (cache / "chrome" / "blob").write_bytes(b"x" * (3 * 1024 * 1024))
    monkeypatch.setattr(svc, "_engine_cache_dir", lambda: cache)

    _write_marker(fake_install)
    state = HyperFramesService.get_install_state()
    assert state["installed"] is True
    assert state["engine_cache_path"] == str(cache)
    assert state["engine_cache_mb"] == 3
    # …and it is NOT folded into the number the uninstall dialog quotes.
    assert state["disk_size_mb"] != state["engine_cache_mb"]


async def test_uninstall_removes_motion_dir_and_leaves_the_shared_cache_alone(
        fake_install, monkeypatch, tmp_path):
    """The engine cache is a shared path — another HyperFrames install on the
    same machine uses it. Reclaiming our disk must not delete their browser."""
    import backend.services.hyperframes_service as svc
    cache = tmp_path / "engine-cache"
    cache.mkdir()
    (cache / "keepme").write_text("someone else's chrome")
    monkeypatch.setattr(svc, "_engine_cache_dir", lambda: cache)

    res = await HyperFramesService.uninstall_plugin()
    assert res["ok"] is True
    assert not fake_install.exists()
    assert (cache / "keepme").exists()
