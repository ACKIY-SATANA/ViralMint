# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""HyperFrames plugin lifecycle — the Motion Graphics on-demand dependency.

Motion Graphics needs a JavaScript runtime, and ViralMint is a Python app, so
the base install stays Python-only and this manager fetches what the feature
needs on opt-in: a *portable* Node runtime plus an `npm install` of HyperFrames
into the user's data dir. Nothing is bundled, nothing is downloaded until a
user asks for the feature, and `uninstall_plugin()` puts the disk back exactly
as it was. Render itself lives in motion_render_service.py.

The whole chain is local — Node, headless Chrome and FFmpeg on the user's own
machine — so a motion piece costs nothing per render and works offline once
installed.

Data-dir layout (DATA_DIR/motion/):
    node/         portable Node runtime (extracted archive dir)
    hyperframes/  npm install target (node_modules + package.json)
    .installed    JSON marker (the pinned versions this tree satisfies)
"""
import asyncio
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings
from backend.services.hyperframes_contract import (
    GSAP_NPM_VERSION,
    HYPERFRAMES_NPM_VERSION,
    NODE_VERSION,
    base_env,
    resolve_cli_js,
)
from backend.services.motion_proc import run_capped
from backend.services.plugin_download import (
    DownloadFailed, download_with_resume, sha256_file,
)

logger = logging.getLogger(__name__)

# Version pins (HYPERFRAMES_NPM_VERSION / NODE_VERSION) live in
# hyperframes_contract — the single bump point with the regression checklist.
_NODE_DIST_BASE = "https://nodejs.org/dist"

# Rough footprint for the pre-install warning chip. node_modules lands ~145 MB
# AFTER pruning onnxruntime-node (the 260 MB local-AI dependency the render path
# never imports), + portable Node ~50 MB, + the headless Chrome HyperFrames
# fetches for itself on its first run ~150 MB.
_APPROX_DOWNLOAD_MB = 350
_MIN_FREE_DISK_MB = 1536
_INSTALL_TIMEOUT_SECONDS = 600  # npm install + node download, generous

# node_modules subtrees that exist only for HyperFrames' LOCAL AI features
# (tts / transcribe / remove-background) — ViralMint uses its own TTS + Whisper,
# and the render path never imports these. Pruned post-install to cut ~260 MB.
# Spike-verified: render works identically without onnxruntime-node.
_PRUNABLE_MODULES = ("onnxruntime-node",)


def _motion_dir() -> Path:
    return settings.MOTION_DIR


def _node_dir() -> Path:
    return _motion_dir() / "node"


def _hyperframes_dir() -> Path:
    return _motion_dir() / "hyperframes"


def _marker_path() -> Path:
    return _motion_dir() / ".installed"


def _engine_cache_dir() -> Path:
    """Where HyperFrames keeps the headless Chrome it downloads for itself.

    Anchored on the user's home directory by the engine (`CACHE_ROOT_DIR =
    join(homedir(), ".cache", "hyperframes")`), with NO env override — the
    knobs we do set (HYPERFRAMES_FONT_CACHE_DIR / _EXTRACT_CACHE_DIR) redirect
    fonts and extracts but not the browser.

    So a ~200 MB Chrome lands OUTSIDE the directory uninstall removes, and we
    report it rather than pretend the plugin's whole footprint is under
    MOTION_DIR. We deliberately do NOT delete it: the path is shared with any
    other HyperFrames install on the machine (a global `npx hyperframes`, for
    instance), and quietly removing another tool's browser to tidy up after
    ourselves is not ours to do.
    """
    return Path.home() / ".cache" / "hyperframes"


def _dir_size_mb(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                     / (1024 * 1024))
    except OSError:
        return 0


def _node_archive_name() -> Optional[str]:
    """Portable Node archive base name for this platform, or None if unsupported.

    e.g. node-v22.14.0-darwin-arm64 / node-v22.14.0-win-x64
    """
    sysname = platform.system()
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x64", "amd64": "x64",
        "arm64": "arm64", "aarch64": "arm64",
    }.get(machine)
    if not arch:
        return None
    if sysname == "Darwin":
        return f"node-v{NODE_VERSION}-darwin-{arch}"
    if sysname == "Linux":
        return f"node-v{NODE_VERSION}-linux-{arch}"
    if sysname == "Windows":
        # Windows arm64 has no official portable build today; ship x64 (runs
        # under emulation on win-arm). x64 is the overwhelming majority anyway.
        return f"node-v{NODE_VERSION}-win-x64"
    return None


def _node_bin() -> Path:
    """Path to the portable `node` executable."""
    base = _node_dir() / (_node_archive_name() or "")
    if sys.platform.startswith("win"):
        return base / "node.exe"
    return base / "bin" / "node"


def _npm_cli_js() -> Path:
    """Path to npm's cli.js inside the portable Node — invoked via `node npm-cli.js`
    so we never depend on a shell-script `npm` wrapper (avoids shell=True)."""
    base = _node_dir() / (_node_archive_name() or "")
    if sys.platform.startswith("win"):
        return base / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return base / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"


def _cli_js() -> Path:
    """Path to the HyperFrames CLI entry, resolved from the installed package's
    package.json `bin` field (fallback dist/cli.js) so a version that relocates
    its entry doesn't break is_installed() after a clean npm install."""
    return resolve_cli_js(_hyperframes_dir())


class HyperFramesService:
    """Singleton orchestrating the HyperFrames plugin install/uninstall."""

    _install_lock = asyncio.Lock()
    _install_task: Optional[asyncio.Task] = None
    _installing: bool = False
    _install_progress_pct: int = 0
    _install_step: str = ""
    _install_error: Optional[str] = None
    _last_install_started_at: float = 0.0

    # ── Status ────────────────────────────────────────────────────────────────

    @classmethod
    def is_installed(cls) -> bool:
        """True iff the portable Node + HyperFrames CLI are present and the
        marker matches the pinned versions. Cheap O(small) existence checks."""
        try:
            marker = _marker_path()
            if not (_node_bin().exists() and _cli_js().exists() and marker.exists()):
                return False
            data = json.loads(marker.read_text())
            return (
                data.get("hyperframes_version") == HYPERFRAMES_NPM_VERSION
                and data.get("node_version") == NODE_VERSION
            )
        except (OSError, ValueError):
            return False

    @classmethod
    def get_disk_size_mb(cls) -> int:
        """Bytes under MOTION_DIR — the part uninstall actually reclaims."""
        return _dir_size_mb(_motion_dir())

    @classmethod
    def get_install_state(cls) -> dict:
        """Snapshot for the Settings UI to poll (mirrors playwright/status)."""
        path = _motion_dir()
        installed = cls.is_installed()
        supported = _node_archive_name() is not None

        # A prior install of a DIFFERENT pinned version (Node + CLI + marker all
        # present, but is_installed() failed the version check) → the UI offers an
        # in-place "Update" (fast: Node is reused, npm just bumps the package)
        # rather than a first-time "Install ~200 MB download".
        prior_version = None
        if not installed:
            try:
                marker = _marker_path()
                if _node_bin().exists() and _cli_js().exists() and marker.exists():
                    prior_version = json.loads(marker.read_text()).get("hyperframes_version")
            except (OSError, ValueError):
                prior_version = None
        update_available = bool(prior_version)

        free_mb = -1
        probe = path
        for _ in range(4):
            try:
                free_mb = shutil.disk_usage(probe).free // (1024 * 1024)
                break
            except (OSError, FileNotFoundError):
                probe = probe.parent

        return {
            "installed": installed,
            "update_available": update_available,
            "installed_version": prior_version,  # the stale on-disk version when an update is pending
            "installing": cls._installing,
            "progress_pct": cls._install_progress_pct,
            "current_step": cls._install_step,
            "error": cls._install_error,
            "platform_supported": supported,
            "motion_path": str(path),
            "disk_size_mb": cls.get_disk_size_mb() if installed else 0,
            "approx_download_mb": _APPROX_DOWNLOAD_MB,
            "free_disk_mb": free_mb,
            "min_free_disk_mb": _MIN_FREE_DISK_MB,
            "hyperframes_version": HYPERFRAMES_NPM_VERSION,
            "node_version": NODE_VERSION,
            "gsap_version": GSAP_NPM_VERSION,
            # The engine's own home-anchored cache (headless Chrome). Reported
            # separately because uninstall does not touch it — see
            # _engine_cache_dir.
            "engine_cache_path": str(_engine_cache_dir()),
            "engine_cache_mb": _dir_size_mb(_engine_cache_dir()) if installed else 0,
        }

    # ── Install / uninstall ────────────────────────────────────────────────────

    @classmethod
    async def install_plugin(cls) -> dict:
        """Trigger plugin install in the background. Idempotent. Returns immediately.

        UI polls /api/settings/motion-graphics/status every 2s for progress.
        """
        if cls._installing:
            return {"ok": True, "message": "Install already in progress"}
        if cls.is_installed():
            return {"ok": True, "message": "Already installed"}
        if _node_archive_name() is None:
            return {
                "ok": False,
                "error": f"Motion Graphics isn't supported on this platform "
                         f"({platform.system()}/{platform.machine()}).",
            }

        path = _motion_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
            free_mb = shutil.disk_usage(path).free // (1024 * 1024)
            if free_mb < _MIN_FREE_DISK_MB:
                return {
                    "ok": False,
                    "error": f"Need at least {_MIN_FREE_DISK_MB} MB free to install "
                             f"(you have {free_mb} MB)",
                }
        except OSError as e:
            return {"ok": False, "error": f"Cannot prepare install directory: {e}"}

        cls._installing = True
        cls._install_progress_pct = 0
        cls._install_step = "Preparing install…"
        cls._install_error = None
        cls._last_install_started_at = time.time()

        cls._install_task = asyncio.create_task(cls._run_install())
        return {"ok": True, "message": "Install started"}

    @classmethod
    async def _run_install(cls) -> None:
        """Background worker: download Node → extract → npm install → prune →
        smoke-render → write marker. Each failure sets _install_error + bails."""
        async with cls._install_lock:
            # An UPGRADE must fail safe as "needs update", not "not installed":
            # the marker is the only thing that distinguishes the two states
            # (update_available requires marker.exists()), so keep the old
            # marker's content and restore it if any step past this point
            # fails — the contract docstring's fail-safe promise depends on it.
            prior_marker: str | None = None
            try:
                if _marker_path().exists():
                    try:
                        prior_marker = _marker_path().read_text()
                    except OSError:
                        prior_marker = None
                    _marker_path().unlink()

                await cls._step_download_node()
                if cls._install_error:
                    return
                await cls._step_npm_install()
                if cls._install_error:
                    return
                await cls._step_prune()
                await cls._step_smoke_render()
                if cls._install_error:
                    return

                _marker_path().write_text(json.dumps({
                    "hyperframes_version": HYPERFRAMES_NPM_VERSION,
                    "node_version": NODE_VERSION,
                    "gsap_version": GSAP_NPM_VERSION,
                    "installed_at": time.time(),
                }))

                cls._install_progress_pct = 100
                cls._install_step = "Installed"
                logger.info(
                    "Motion Graphics: HyperFrames ready (%d MB on disk)",
                    cls.get_disk_size_mb(),
                )
            except Exception as e:
                cls._install_error = str(e)[:200]
                logger.exception("Motion Graphics: install crashed")
            finally:
                # Failed UPGRADE → restore the old marker so the UI offers
                # "Update" (needs-update fail-safe), not a first-time install.
                # is_installed() still version-gates against the pin, so the
                # feature stays correctly gated off either way.
                if cls._install_error and prior_marker and not _marker_path().exists():
                    try:
                        _marker_path().write_text(prior_marker)
                    except OSError:
                        logger.warning("Motion Graphics: could not restore prior marker")
                cls._installing = False
                cls._install_task = None

    @classmethod
    async def _step_download_node(cls) -> None:
        """Download + extract the portable Node archive (idempotent)."""
        if _node_bin().exists():
            cls._install_progress_pct = 25
            return
        cls._install_step = "Downloading Node runtime…"
        name = _node_archive_name()
        ext = "zip" if sys.platform.startswith("win") else "tar.gz"
        archive_name = f"{name}.{ext}"
        url = f"{_NODE_DIST_BASE}/v{NODE_VERSION}/{archive_name}"
        node_root = _node_dir()
        node_root.mkdir(parents=True, exist_ok=True)
        archive = node_root / archive_name

        def _progress(frac: float) -> None:
            # 0–20% of the bar is the Node download
            cls._install_progress_pct = min(20, int(20 * frac))

        try:
            # Shared resumable fetch (plugin_download): a dropped connection
            # mid-archive now resumes from its byte offset instead of
            # restarting, and `archive` only exists once the bytes are all
            # there — a truncated download can no longer reach the extractor.
            # The sha is verified below rather than here because it's resolved
            # from nodejs.org's manifest AFTER the fetch (see the note there).
            await download_with_resume(url, archive, on_progress=_progress)
        except DownloadFailed as e:
            cls._install_error = f"Node download failed: {e}"
            return

        # Integrity gate — verify the archive against nodejs.org's published
        # SHASUMS256.txt before we trust it (matches the VoxCPM installer's
        # sha-gate; the render pipeline was the one on-demand plugin without
        # download verification). A mismatch is a HARD fail — a corrupt /
        # truncated / CDN-poisoned archive is caught deterministically here
        # instead of surfacing later as a cryptic extract or smoke-render
        # error. If the manifest itself can't be fetched (transient), we log
        # and proceed — the HTTPS transport + the mandatory smoke-render still
        # gate the install, and blocking on a manifest hiccup would be a worse
        # regression than the residual risk of an un-cross-checked HTTPS fetch.
        cls._install_step = "Verifying Node runtime…"
        expected = await cls._fetch_node_sha256(archive_name)
        if expected:
            actual = await asyncio.to_thread(sha256_file, archive)
            if actual.lower() != expected.lower():
                archive.unlink(missing_ok=True)
                cls._install_error = (
                    "Node runtime failed its integrity check (sha256 mismatch) — "
                    "the download was corrupted or tampered. Retry."
                )
                return

        cls._install_step = "Extracting Node runtime…"
        try:
            if ext == "zip":
                await asyncio.to_thread(cls._extract_zip, archive, node_root)
            else:
                await asyncio.to_thread(cls._extract_tar, archive, node_root)
            archive.unlink(missing_ok=True)
        except Exception as e:
            cls._install_error = f"Node extract failed: {str(e)[:160]}"
            return
        if not _node_bin().exists():
            cls._install_error = "Node extracted but `node` binary not found"
            return
        cls._install_progress_pct = 25

    @classmethod
    async def _fetch_node_sha256(cls, archive_name: str) -> Optional[str]:
        """Expected sha256 for ``archive_name`` from nodejs.org's SHASUMS256.txt
        (same HTTPS origin as the archive). Returns None if the manifest can't be
        fetched or the entry is absent — the caller then proceeds (see the
        integrity-gate comment for why a manifest hiccup isn't fatal). The file is
        a list of ``<sha256>  <filename>`` lines."""
        url = f"{_NODE_DIST_BASE}/v{NODE_VERSION}/SHASUMS256.txt"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            for line in resp.text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == archive_name:
                    return parts[0]
            logger.warning(
                "Motion Graphics: %s not found in Node SHASUMS256 — skipping integrity check",
                archive_name,
            )
        except Exception as e:
            logger.warning(
                "Motion Graphics: could not fetch Node SHASUMS256 (%s) — skipping integrity check",
                str(e)[:120],
            )
        return None

    @staticmethod
    def _extract_tar(archive: Path, dest: Path) -> None:
        with tarfile.open(archive, "r:gz") as tf:
            # filter="data" (Python 3.12+) rejects unsafe members (absolute paths,
            # `..` traversal, device/symlink escapes) — defense in depth on top of
            # the sha gate. Guarded for 3.11 where the param doesn't exist.
            try:
                tf.extractall(dest, filter="data")
            except TypeError:
                tf.extractall(dest)

    @staticmethod
    def _extract_zip(archive: Path, dest: Path) -> None:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)

    @classmethod
    async def _step_npm_install(cls) -> None:
        """`npm install hyperframes@PIN` into the managed dir via portable Node."""
        cls._install_step = "Installing HyperFrames (npm)…"
        hf_dir = _hyperframes_dir()
        hf_dir.mkdir(parents=True, exist_ok=True)
        pkg = hf_dir / "package.json"
        if not pkg.exists():
            pkg.write_text(json.dumps(
                {"name": "viralmint-motion", "private": True, "version": "0.0.0"},
                indent=2,
            ))
        cmd = [
            str(_node_bin()), str(_npm_cli_js()),
            "install",
            f"hyperframes@{HYPERFRAMES_NPM_VERSION}",
            # GSAP is the animation runtime every composition drives. It is not
            # a dependency of hyperframes (compositions are expected to bring
            # their own), and it ships under GreenSock's own licence rather
            # than a free-software one — so it is installed here on the user's
            # machine instead of being vendored into this repository. Staged
            # projects get a copy at render time.
            f"gsap@{GSAP_NPM_VERSION}",
            "--no-audit", "--no-fund", "--loglevel=error",
        ]
        env = {**os.environ, "npm_config_update_notifier": "false"}
        try:
            rc, _tail, timed_out = await run_capped(
                cmd, cwd=str(hf_dir), env=env,
                overall_timeout=_INSTALL_TIMEOUT_SECONDS, line_timeout=10.0,
                on_idle=cls._heartbeat_npm,
                on_line=lambda line: logger.debug("npm install: %s", line),
            )
        except Exception as e:
            cls._install_error = f"Cannot launch npm: {str(e)[:160]}"
            return

        if timed_out:
            cls._install_error = "npm install timed out. Check your connection and retry."
            return
        if rc != 0:
            cls._install_error = (
                f"npm install failed (exit {rc}). Often a proxy/antivirus or "
                f"offline network — check the connection and retry."
            )
            return
        if not _cli_js().exists():
            cls._install_error = "npm install finished but HyperFrames CLI not found — retry"
            return
        from backend.services.hyperframes_contract import resolve_gsap_js
        if not resolve_gsap_js(hf_dir).exists():
            cls._install_error = (
                "npm install finished but the GSAP runtime is missing — "
                "compositions cannot animate without it. Retry."
            )
            return
        cls._install_progress_pct = 70

    @classmethod
    def _heartbeat_npm(cls) -> None:
        """Nudge the progress bar 25→60 while npm works (no parseable %)."""
        cls._install_progress_pct = min(60, cls._install_progress_pct + 1)

    @classmethod
    async def _step_prune(cls) -> None:
        """Drop node_modules subtrees the render path never imports (~260 MB)."""
        cls._install_step = "Optimizing install…"
        nm = _hyperframes_dir() / "node_modules"
        for mod in _PRUNABLE_MODULES:
            target = nm / mod
            if target.exists():
                try:
                    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
                    logger.info("Motion Graphics: pruned %s", mod)
                except Exception as e:
                    # Non-fatal — a larger install still works.
                    logger.warning("Motion Graphics: prune of %s failed: %s", mod, e)
        cls._install_progress_pct = 80

    @classmethod
    async def _step_smoke_render(cls) -> None:
        """Render the bundled kinetic-hook template end to end before the marker
        is written, so a broken chain fails the INSTALL rather than the user's
        first real piece.

        This is the step that exercises everything the earlier ones only staged:
        portable Node, the CLI, the pruned node_modules, the staged GSAP copy,
        HyperFrames fetching its headless Chrome, and FFmpeg. On a first install
        it is also the slowest step for exactly that reason.
        """
        cls._install_step = "Verifying render (first run also fetches Chrome)…"
        try:
            # Imported here to avoid a circular import at module load.
            from backend.services.motion_render_service import MotionRenderService
            out = await MotionRenderService.render(
                template_id="kinetic_hook",
                variables={
                    "kicker": "MOTION GRAPHICS",
                    "title": "Install Verified",
                    "subtitle": "rendered locally",
                    "accent": "#ffd60a",
                    "bg": "#0b0b14",
                },
                aspect="9:16",
                skip_gate=True,   # plugin files exist; marker not written yet
                # A verification render, not a deliverable: draft quality and no
                # supersample keep it fast.
                quality="draft",
                supersample=False,
            )
            if not out.exists() or out.stat().st_size < 1024:
                cls._install_error = "Verify render produced no usable output — retry"
                return
            out.unlink(missing_ok=True)
        except Exception as e:
            cls._install_error = f"Verify render failed: {str(e)[:160]}"
            return
        cls._install_progress_pct = 95

    @classmethod
    def _reset_install_state(cls) -> None:
        """Clear in-memory install-progress fields. Called after uninstall so a
        stale error/step from a previous failed attempt doesn't keep surfacing
        in get_install_state() on a plugin that's now gone."""
        cls._install_progress_pct = 0
        cls._install_step = ""
        cls._install_error = None

    @classmethod
    async def uninstall_plugin(cls) -> dict:
        """Delete the entire motion/ dir (frees the plugin install).

        Also resets any leftover install-progress state so a prior error doesn't
        linger on a plugin that is now gone.
        """
        if cls._installing:
            return {"ok": False, "error": "Cannot uninstall while an install is in progress"}
        path = _motion_dir()
        if not path.exists():
            cls._reset_install_state()
            return {"ok": True, "message": "Already uninstalled"}
        try:
            await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
            cls._reset_install_state()
            logger.info("Motion Graphics: uninstalled (removed %s)", path)
            return {"ok": True, "message": "Uninstalled"}
        except Exception as e:
            logger.exception("Motion Graphics: uninstall failed")
            return {"ok": False, "error": str(e)[:200]}

    # ── Render plumbing (used by motion_render_service) ────────────────────────

    @classmethod
    def node_bin(cls) -> Path:
        return _node_bin()

    @classmethod
    def cli_js(cls) -> Path:
        return _cli_js()

    @classmethod
    def render_env(cls) -> dict:
        """Subprocess env for a render: pin the FFmpeg binaries to the ones on
        this machine and keep HyperFrames deterministic and quiet.

        HYPERFRAMES_BROWSER_PATH is deliberately left unset, so HyperFrames
        resolves (and, on first use, downloads) the exact headless Chrome build
        its renderer was tested against — the one thing it is genuinely version
        sensitive about. That download lands in _engine_cache_dir(), NOT under
        MOTION_DIR; see there for why we report it instead of relocating it.

        Setting HYPERFRAMES_BROWSER_PATH in the environment still wins, because
        base_env() passes os.environ through. Point it at a Chrome you already
        have if you would rather not keep a second copy — but note the engine
        hard-fails on a path that does not exist, rather than falling back.
        """
        env = base_env()
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg:
            env["HYPERFRAMES_FFMPEG_PATH"] = ffmpeg
        if ffprobe:
            env["HYPERFRAMES_FFPROBE_PATH"] = ffprobe
        return env
