# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The embedded Motion Graphics studio — a `hyperframes preview` server that
ViralMint runs on loopback and embeds in an iframe, re-skinned to the app theme
by injecting CSS into the studio SPA's own index.html.

The studio is the engine's, not ours: timeline, preview, asset import and
Export all belong to HyperFrames. What ViralMint owns is the surround —
starting and stopping the server, seeding the project, keeping previous
compositions, and pulling the studio's MP4 exports into the Library so a motion
piece ends up in the same place as everything else the app makes.

Serving it from the same host as the app is deliberate: the SPA keeps state in
browser storage, and a different origin would give it a different, empty store
every time the port moved.
"""
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings
from backend.core.exceptions import HyperFramesNotInstalledError
from backend.services.hyperframes_service import HyperFramesService
from backend.services.hyperframes_contract import (
    base_env, check_argv, preview_argv, preview_kill_argv,
)
from backend.services.motion_render_service import (
    MotionRenderService, _templates_src_dir,
)

logger = logging.getLogger(__name__)

# One port above the app's default. Deliberately NOT adjacent to the ports a
# developer is likely to be running a second backend on.
STUDIO_PORT = 16890

# `check` boots a real browser, where a plain `lint` is a static parse. The
# ceiling matters because callers may run it more than once.
CHECK_TIMEOUT = 90
_BLOCKING_CHECKS = ("lint", "runtime", "layout", "motion")


def _project_dir() -> Path:
    return settings.MOTION_DIR / "studio" / "project"


def _studio_index() -> Path:
    """The studio SPA's served index.html inside the installed plugin.
    Resolved via the contract (NOT cli path — 0.7.64 moved the bin entry to
    a wrapper outside dist/ while the SPA stayed at dist/studio/)."""
    from backend.services.hyperframes_contract import resolve_studio_index
    from backend.services.hyperframes_service import _hyperframes_dir
    return resolve_studio_index(_hyperframes_dir())


def _theme_css(mode: str = "dark") -> str:
    """The ViralMint re-skin CSS. Dark base always; light overrides appended
    (and thus winning) when the app is in light mode."""
    base_dir = _templates_src_dir().parent
    css = ""
    try:
        css = (base_dir / "studio-theme.css").read_text()
    except OSError:
        return ""
    if mode == "light":
        try:
            css += "\n" + (base_dir / "studio-theme-light.css").read_text()
        except OSError:
            pass
    return css


def _studio_js(mode: str) -> str:
    """Injected script: (1) hide the class-less HeyGen wordmark SVG (top-left,
    CSS can't target it), and (2) in LIGHT mode, recolor near-black INLINE
    backgrounds (the timeline canvas/lanes hardcode `background: rgb(10,10,11)`
    inline, which CSS classes can't reliably override) to the light surface. A
    MutationObserver (incl. style-attr changes) keeps both applied across the
    SPA's re-renders."""
    light = "true" if mode == "light" else "false"
    return (
        "(function(){var L=" + light + ";function f(){try{"
        # (1) hide the wordmark
        "document.querySelectorAll('svg').forEach(function(s){var r=s.getBoundingClientRect();"
        "if(r.top<48&&r.left<210&&r.width>58){s.style.display='none';}});"
        # (2) light-mode: recolor near-black inline backgrounds
        "if(L){document.querySelectorAll('[style]').forEach(function(el){"
        "var b=el.style.background||el.style.backgroundColor||'';"
        "var m=b.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);"
        "if(m){var r=+m[1],g=+m[2],bl=+m[3];"
        "if(r<32&&g<32&&bl<32){el.style.setProperty('background','#f1eadf','important');}}});}"
        "}catch(e){}}"
        "if(document.readyState!=='loading'){f();}else{document.addEventListener('DOMContentLoaded',f);}"
        "try{new MutationObserver(f).observe(document.documentElement,"
        "{childList:true,subtree:true,attributes:true,attributeFilter:['style']});}catch(e){}"
        "setInterval(f,1000);})();"
    )


def _inject_theme(mode: str = "dark") -> None:
    """Idempotently inject the ViralMint theme (for the given app mode) +
    brand-hide into the studio's index.html. Re-applied on every start so a
    plugin update can't drop it and an app mode-switch re-themes it."""
    idx = _studio_index()
    css = _theme_css(mode)
    if not idx.exists() or not css:
        return
    try:
        html = idx.read_text()
        html = re.sub(r'<style id="vm-theme">.*?</style>', "", html, flags=re.DOTALL)
        html = re.sub(r'<script id="vm-brand">.*?</script>', "", html, flags=re.DOTALL)
        block = (f'<style id="vm-theme">{css}</style>'
                 f'<script id="vm-brand">{_studio_js(mode)}</script>')
        if "</head>" in html:
            html = html.replace("</head>", block + "</head>", 1)
        else:
            html = block + html
        idx.write_text(html)
        logger.info("Motion Studio: ViralMint theme + brand-hide injected")
    except OSError as e:
        logger.warning("Motion Studio: theme injection failed: %s", e)


class StudioService:
    _proc: Optional[asyncio.subprocess.Process] = None
    _lock = asyncio.Lock()
    _import_lock = asyncio.Lock()   # serialize render→Library imports (poll can overlap)
    _author_lock = asyncio.Lock()   # serialize authoring (archive→write index.html is not atomic)
    _prewarm_task: Optional[asyncio.Task] = None  # background thumbnail pre-warm

    # Thumbnail pre-warm bounds: comps beyond the freshest N stay cold (their
    # first selection pays the capture burst). Each capture is ~1-2s in the
    # studio's capture browser; the 120s deadline in _prewarm_thumbnails is
    # the real cost bound, and freshest-comps-first means new compositions
    # (the ones a user is about to click) warm first. The element cap is a
    # runaway guard only — AI comps commonly have ~15-30 laned ids and the
    # SPA requests a thumbnail for every one on selection, so a tight cap
    # just moves the miss burst back to the user's click.
    PREWARM_MAX_COMPS = 6
    PREWARM_MAX_ELEMENTS = 40

    @classmethod
    def project_dir(cls) -> Path:
        return _project_dir()

    @classmethod
    def is_running(cls) -> bool:
        return cls._proc is not None and cls._proc.returncode is None

    @classmethod
    def _seed(cls) -> None:
        """Make sure the studio has a project to open.

        Also (re)stages the GSAP runtime on every call, not just on first seed:
        it is installed alongside the engine rather than committed here, so an
        engine reinstall replaces the copy the project links to. Staging it
        unconditionally is cheap and removes a "the studio opens but nothing
        animates" failure that is very hard to read from the UI.
        """
        proj = _project_dir()
        (proj / "assets").mkdir(parents=True, exist_ok=True)
        MotionRenderService._stage_gsap(proj)
        if (proj / "index.html").exists():
            return
        src = _templates_src_dir() / "kinetic_hook"
        shutil.copyfile(src / "index.html", proj / "index.html")
        if (src / "hyperframes.json").exists():
            shutil.copyfile(src / "hyperframes.json", proj / "hyperframes.json")

    # ── Archived compositions ─────────────────────────────────────────────────
    # Previous compositions live in a SUBDIRECTORY, never the project root.
    # HyperFrames' linter reports two root-level files carrying
    # data-composition-id as `multiple_root_compositions` ("the runtime may
    # discover both as entry points, causing duplicate audio playback"), which
    # also makes it impossible to lint/`check` the LIVE project directly. The
    # studio still lists archives (as "archive/comp_…" in its Comps panel), so
    # nothing is lost from the user's side.
    ARCHIVE_SUBDIR = "archive"

    @classmethod
    def archive_dir(cls) -> Path:
        return _project_dir() / cls.ARCHIVE_SUBDIR

    @classmethod
    def comp_path(cls, name: str) -> Path:
        """On-disk path for a project-relative composition filename. index.html
        sits at the project root; comp_*.html archives live in archive/, with a
        root fallback so a file that predates the migration stays addressable."""
        safe = cls._safe_name(name)
        proj = _project_dir()
        if not cls._COMP_RE.match(safe):
            return proj / safe
        archived = cls.archive_dir() / safe
        if archived.exists():
            return archived
        root = proj / safe
        return root if root.exists() else archived

    @classmethod
    def _archive_files(cls) -> list[Path]:
        """Every archived composition, newest first. Reads BOTH archive/ and the
        project root so a failed/partial migration still lists (and can clean up)
        the old location."""
        proj = _project_dir()
        seen, out = set(), []
        for d in (cls.archive_dir(), proj):
            try:
                found = list(d.glob("comp_*.html"))
            except OSError:
                continue
            for f in found:
                if cls._COMP_RE.match(f.name) and f.name not in seen:
                    seen.add(f.name)
                    out.append(f)
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def _archive_current(cls, proj: Path) -> Optional[str]:
        """Preserve the live index.html (and its plan sidecar) into archive/ and
        return the archive filename — None when there is nothing worth keeping
        (the seed composition). Callers hold _author_lock."""
        from uuid import uuid4
        idx = proj / "index.html"
        if not idx.exists():
            return None
        cur = idx.read_text()
        if 'data-composition-id="hook"' in cur:      # the seed — nothing to keep
            return None
        name = f"comp_{uuid4().hex[:8]}.html"
        dest = cls.archive_dir()
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text(cur)
        sidecar = proj / "index.plan.json"
        if sidecar.exists():                          # the plan travels with it
            try:
                sidecar.replace(dest / f"{Path(name).stem}.plan.json")
            except OSError:
                sidecar.unlink(missing_ok=True)
        return name

    @classmethod
    async def _responds(cls) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"http://127.0.0.1:{STUDIO_PORT}/")
                return r.status_code == 200
        except Exception:
            return False

    @classmethod
    async def ensure_running(cls, mode: str = "dark", force: bool = False) -> dict:
        """Seed + theme (for the app `mode`) + start the preview server.
        Idempotent — re-injects the theme each call so an app light/dark switch
        re-skins the studio. → {ok, port}.

        `force=True` always (re)starts the preview server even if one is
        running. A composition written to disk while no studio client was
        connected does not reliably reach a reused server's client — it can
        keep showing the previous composition — so anything that writes
        index.html behind the studio's back should force a restart.
        """
        if not HyperFramesService.is_installed():
            raise HyperFramesNotInstalledError("Motion Graphics isn't installed.")
        mode = "light" if str(mode).lower() == "light" else "dark"
        async with cls._lock:
            cls._seed()
            _inject_theme(mode)
            if not force and cls.is_running() and await cls._responds():
                cls._schedule_prewarm()
                return {"ok": True, "port": STUDIO_PORT, "mode": mode}
            await cls._kill_all()
            cmd = preview_argv(HyperFramesService.node_bin(), HyperFramesService.cli_js(),
                               _project_dir(), STUDIO_PORT)
            cls._proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                env=base_env())
            for _ in range(40):
                if await cls._responds():
                    logger.info("Motion Studio: preview up on :%d (%s)", STUDIO_PORT, mode)
                    cls._schedule_prewarm()
                    return {"ok": True, "port": STUDIO_PORT, "mode": mode}
                if cls._proc.returncode is not None:
                    break
                await asyncio.sleep(0.5)
            raise RuntimeError("Studio preview server did not start")

    # ── Thumbnail pre-warm ────────────────────────────────────────────────────
    # Why this exists: clicking
    # a comp in the Comps panel makes the SPA request a timeline-lane thumbnail
    # PER ELEMENT, and each cache-miss capture boots the whole composition in
    # the studio's capture Chrome (~1-2s each, serialized — 14 elements ≈ 25s
    # of CPU churn). The server caches by content-hash + mtime, so every
    # AI-composed/refined file is cold by construction — selection lags exactly
    # once per new composition ("sometimes"). Pre-warming fires the same
    # requests in the background right after the studio (re)starts, so the
    # burst happens right after the studio starts instead of on the first
    # click, which is the difference between "loading" and "broken".

    @classmethod
    def _schedule_prewarm(cls) -> None:
        if cls._prewarm_task and not cls._prewarm_task.done():
            return
        cls._prewarm_task = asyncio.create_task(cls._prewarm_thumbnails())

    @classmethod
    def _prewarm_targets(cls) -> list:
        """[(rel_path, seek_t, [element ids])] for the freshest comps. Mirrors
        what the studio SPA requests on selection: lane thumbnails use the
        comp's semantic element ids as `#id` selectors at t = duration/2, plus
        one selector-less card thumbnail."""
        import re as _re
        proj = _project_dir()
        # The thumbnail URL key is the project-RELATIVE path, so archives warm
        # under "archive/comp_….html" — exactly what the studio SPA requests.
        comps = [proj / "index.html"]
        comps += sorted(
            [*cls._archive_files(), *(proj / "compositions").glob("*.html")],
            key=lambda f: f.stat().st_mtime, reverse=True)
        out = []
        for f in comps[:cls.PREWARM_MAX_COMPS]:
            try:
                html = f.read_text()
            except OSError:
                continue
            # Duration lives in the (HTML-escaped) data-composition-variables
            # JSON as _vm_duration's default; authored comps default to 6s.
            dur = 6.0
            m = (_re.search(r'_vm_duration(?:[^}]*?)(?:&quot;|")default(?:&quot;|"):\s*([0-9.]+)', html))
            if m:
                try:
                    dur = float(m.group(1))
                except ValueError:
                    pass
            ids = []
            for mid in _re.finditer(r'\bid="([A-Za-z][\w-]*)"', html):
                el = mid.group(1)
                # hf-* are wrapper ids the studio doesn't lane; stage/scene are
                # the fixed composition frame.
                if el.startswith("hf-") or el in ("stage", "scene"):
                    continue
                if el not in ids:
                    ids.append(el)
            out.append((f.relative_to(proj).as_posix(), round(dur / 2, 2),
                        ids[:cls.PREWARM_MAX_ELEMENTS]))
        return out

    @classmethod
    async def _prewarm_thumbnails(cls) -> None:
        from urllib.parse import quote
        try:
            targets = await asyncio.to_thread(cls._prewarm_targets)
        except Exception as e:
            logger.debug("Motion Studio: prewarm target scan failed: %s", e)
            return
        warmed = 0
        deadline = asyncio.get_event_loop().time() + 120  # hard budget
        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                for rel, t, ids in targets:
                    for sel in [None] + [f"#{i}" for i in ids]:
                        if asyncio.get_event_loop().time() > deadline or not cls.is_running():
                            return
                        # `v=v3` mirrors the SPA's urlVersion param — it's part
                        # of the server's cache key, so it must match byte-for-
                        # byte or we'd warm keys the SPA never reads.
                        q = f"v=v3&t={t}"
                        if sel:
                            q += "&selector=" + quote(sel)
                        r = await c.get(
                            f"http://127.0.0.1:{STUDIO_PORT}/api/projects/project/thumbnail/{rel}?{q}")
                        warmed += r.status_code == 200
        except Exception as e:
            logger.debug("Motion Studio: thumbnail prewarm stopped: %s", e)
            return
        if warmed:
            logger.info("Motion Studio: pre-warmed %d thumbnails across %d comps",
                        warmed, len(targets))

    @classmethod
    async def _kill_all(cls) -> None:
        if cls._proc and cls._proc.returncode is None:
            cls._proc.kill()
            try:
                await asyncio.wait_for(cls._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        cls._proc = None
        try:
            kill = preview_kill_argv(HyperFramesService.node_bin(), HyperFramesService.cli_js())
            p = await asyncio.create_subprocess_exec(
                *kill,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                env=base_env())
            await asyncio.wait_for(p.wait(), timeout=10)
        except Exception:
            pass

    @classmethod
    async def stop(cls) -> dict:
        await cls._kill_all()
        return {"ok": True}

    @classmethod
    async def import_composition(cls, html: str) -> dict:
        """Set an externally-authored composition live as index.html.

        The caller validates first; this only writes. Archiving the current
        composition rather than overwriting it means a composition is never
        lost to a bad submission. → {file, archived}.
        """
        async with cls._author_lock:
            cls._seed()
            proj = _project_dir()
            archived = cls._archive_current(proj)
            (proj / "index.html").write_text(html)
            (proj / "index.plan.json").unlink(missing_ok=True)  # external comps carry no plan
            return {"file": "index.html", "archived": archived}

    @staticmethod
    def _composition_issues(html: str) -> list[str]:
        """Cheap structural checks for what BREAKS an offline render — NOT a full
        lint (we deliberately tolerate the linter's quality nits). Returns the
        hard-contract violations (empty = ok)."""
        issues = []
        if "data-composition-id" not in html:
            issues.append("missing the root data-composition-id stage element")
        if "__timelines" not in html:
            issues.append("no window.__timelines registration — nothing animates and the duration is 0")
        # External/CDN refs break the offline render. data: URIs (e.g. the grain
        # SVG, whose payload contains an xmlns http URL) are fine — only flag real
        # external resource loads. Media tags too (img/video/audio/source/iframe
        # + srcset): an externally-hosted image passed this check and rendered
        # blank offline — or tripped HyperFrames' coverage gate (audit 2026-08-18).
        if (re.search(r'<script[^>]+src=["\']https?://', html, re.I)
                or re.search(r'<link[^>]+href=["\']https?://', html, re.I)
                or re.search(r'@import\s+["\']?https?://', html, re.I)
                or re.search(r'url\(\s*["\']?https?://', html, re.I)
                or re.search(r'<(?:img|video|audio|source|iframe)[^>]+(?:src|srcset)=["\']https?://', html, re.I)):
            issues.append("references an external/CDN URL — the render is offline; "
                          "vendor everything (GSAP is assets/gsap.min.js, system fonts "
                          "only, media staged into assets/)")
        return issues

    @classmethod
    async def _lint_errors(cls, html: str, assets_dir: Optional[Path] = None) -> list[str]:
        """Run HyperFrames' own `check --json` (lint + runtime + layout + motion +
        contrast in one browser session) and return ERROR-severity findings
        (warnings/info are tolerated — same stance as render, which omits --strict
        so quality nits don't abort a good render). Returns [] when lint can't run
        (plugin absent, CLI error, timeout) so authoring is never blocked by a lint
        hiccup. This is what catches the render-aborting contract violations our
        cheap substring check can't — CSS transform on a GSAP-animated element,
        media missing an id, a timed element missing class="clip", a timeline key
        that doesn't match the composition id, etc.

        Staged in a FRESH temp dir holding ONLY this index.html plus the
        project's assets (symlinked): linting the live project directory would
        false-positive with `multiple_root_compositions` on the archived
        comp_*.html files, and a legitimate asset reference would trip
        `missing_local_asset` if assets were not present.
        """
        if not HyperFramesService.is_installed():
            return []
        import json as _json
        from uuid import uuid4
        tmp = settings.MOTION_DIR / "tmp" / f"lint_{uuid4().hex[:10]}"
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            (tmp / "index.html").write_text(html)
            # assets/ AND compositions/ are siblings inside a project. Both must
            # be present or the lint reports references the real project resolves
            # fine — a composition embedding a catalog block would otherwise come
            # back "missing", and the repair loop would burn passes chasing it.
            for sub in ("assets", "compositions"):
                src = (assets_dir.parent / sub) if assets_dir else None
                if not src or not src.is_dir():
                    continue
                try:
                    (tmp / sub).symlink_to(src, target_is_directory=True)
                except OSError:
                    await asyncio.to_thread(shutil.copytree, src, tmp / sub,
                                            dirs_exist_ok=True)
            cmd = check_argv(HyperFramesService.node_bin(),
                             HyperFramesService.cli_js(), tmp)
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(settings.MOTION_DIR), env=base_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(),
                                                timeout=CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                try:                       # reap so we don't leak a zombie/transport
                    await proc.wait()
                except Exception:
                    pass
                return []
            data = _json.loads(out.decode("utf-8", errors="replace"))
            # `check` reports per-section; flatten the BLOCKING ones. Contrast is
            # deliberately excluded: WCAG AA on a deliberately moody composition
            # is a design opinion, and a real piece routinely trips it — feeding
            # that to the repair loop would spin. It surfaces as QA advice
            # instead (motion_qa), which is where a judgement call belongs.
            findings = []
            for section in _BLOCKING_CHECKS:
                part = data.get(section)
                if isinstance(part, dict):
                    findings.extend(part.get("findings") or [])
            errors = []
            for f in findings:
                if f.get("severity") != "error":
                    continue
                msg = (f.get("message") or "").strip()
                sel = f.get("selector")
                hint = (f.get("fixHint") or "").strip()
                if sel:
                    msg = f"[{sel}] {msg}"
                if hint:
                    msg = f"{msg}  Fix: {hint}"
                if msg:
                    errors.append(msg)
            return errors
        except Exception as e:  # never block authoring on a lint hiccup
            logger.debug("Motion Studio: lint pass skipped (%s)", e)
            return []
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)

    # Leftovers that guarantee a WRONG export rather than a quality nit: a
    # missing local media file makes HyperFrames' frame-coverage gate abort the
    # render, and with that gate off it ships a blank clip instead. Telling the
    # user "it should still render" over one of these turns a composition they
    # were happy with into a mystery export failure.
    _FATAL_ISSUE_MARKERS = ("not found in the project", "references local file")

    @classmethod
    async def _all_issues(cls, html: str, assets_dir: Optional[Path] = None) -> list[str]:
        """Everything wrong with a composition, cheapest check first.

        The substring pass catches contract violations without booting anything;
        the engine's own `check` catches what a static parse cannot. Running the
        cheap one first matters because it is the one that still works when the
        plugin is mid-reinstall.
        """
        issues = cls._composition_issues(html)
        issues += await cls._lint_errors(html, assets_dir)
        return issues

    @classmethod
    def has_fatal_issue(cls, issues: Optional[list]) -> bool:
        """Do these unresolved issues mean the export CANNOT come out right?"""
        return any(any(m in str(i).lower() for m in cls._FATAL_ISSUE_MARKERS)
                   for i in (issues or []))

    @staticmethod
    def _safe_name(name: str) -> str:
        """A project-relative .html filename, no path escapes."""
        n = Path(name).name
        return n if n.endswith(".html") else f"{n}.html"

    # ── AI Compose helpers: staged assets + comp housekeeping ─────────────────

    ASSET_MAX_BYTES = 200 * 1024 * 1024
    _ASSET_EXTS = {
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
        "video": {".mp4", ".mov", ".webm", ".m4v"},
        "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg"},
    }
    _COMP_RE = re.compile(r"^comp_[0-9a-f]{8}\.html$")

    @classmethod
    def stage_asset_from_path(cls, src: Path, display_name: str | None = None) -> dict:
        """Stage an EXISTING local file (a Library asset) into the project's
        assets/ — same contract as stage_asset but without loading the bytes
        into RAM. The source is NEVER consumed: video/audio are hardlinked
        (read-only use) with a copy fallback across filesystems;
        images are always COPIED because _downscale_image rewrites in place
        and a hardlink would mutate the user's Library original.
        Returns {file, type, duration}."""
        from uuid import uuid4
        if not src.is_file():
            raise ValueError("Asset file missing on disk")
        ext = src.suffix.lower()
        kind = next((k for k, exts in cls._ASSET_EXTS.items() if ext in exts), None)
        if kind is None:
            raise ValueError(f"Unsupported asset type: {ext or 'no extension'}")
        if src.stat().st_size > cls.ASSET_MAX_BYTES:
            raise ValueError("Asset too large (max 200 MB)")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_",
                      Path(display_name or src.name).stem)[:40] or "asset"
        name = f"{stem}_{uuid4().hex[:6]}{ext}"
        dest_dir = _project_dir() / "assets"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if kind == "image":
            shutil.copyfile(src, dest)
            cls._downscale_image(dest)
        else:
            try:
                os.link(src, dest)
            except OSError:
                shutil.copyfile(src, dest)
        duration = None
        if kind in ("video", "audio"):
            try:
                from backend.services.video_utils import probe_duration
                duration = round(probe_duration(dest, 0.0), 1) or None
            except Exception:
                duration = None
        return {"file": name, "type": kind, "duration": duration}

    @classmethod
    def stage_asset(cls, filename: str, data: bytes) -> dict:
        """Store an uploaded file in the project's assets/ so the AI can use it
        in a composition. Returns {file, type, duration}."""
        from uuid import uuid4
        ext = Path(filename or "file").suffix.lower()
        kind = next((k for k, exts in cls._ASSET_EXTS.items() if ext in exts), None)
        if kind is None:
            raise ValueError(f"Unsupported asset type: {ext or 'no extension'}")
        if len(data) > cls.ASSET_MAX_BYTES:
            raise ValueError("Asset too large (max 200 MB)")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem)[:40] or "asset"
        name = f"{stem}_{uuid4().hex[:6]}{ext}"
        dest_dir = _project_dir() / "assets"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / name).write_bytes(data)
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            cls._downscale_image(dest_dir / name)
        duration = None
        if kind in ("video", "audio"):
            try:
                from backend.services.video_utils import probe_duration
                duration = round(probe_duration(dest_dir / name, 0.0), 1) or None
            except Exception:
                duration = None
        return {"file": name, "type": kind, "duration": duration}

    # HyperFrames guidance: source images at most ~2× canvas (Chrome decodes to
    # raw bitmaps — a 7000px photo stutters preview AND render). 3840px covers
    # 2× the largest dimension of every aspect we render (1080×1920 etc.).
    IMAGE_MAX_SIDE = 3840

    @classmethod
    def _downscale_image(cls, path: Path) -> None:
        """In-place downscale of an oversized staged image. Best-effort — any
        failure keeps the original (the render still works, just heavier)."""
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                w, h = im.size
                if max(w, h) <= cls.IMAGE_MAX_SIDE:
                    return
                scale = cls.IMAGE_MAX_SIDE / max(w, h)
                im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                               Image.LANCZOS)
                if path.suffix.lower() in (".jpg", ".jpeg"):
                    im = im.convert("RGB")
                    im.save(path, quality=90)
                else:
                    im.save(path)
                logger.info("Motion Studio: downscaled %s %dx%d → %dx%d",
                            path.name, w, h, *im.size)
        except Exception as e:  # noqa: BLE001
            logger.warning("Motion Studio: image downscale skipped (%s): %s",
                           path.name, e)

    @classmethod
    def list_comps(cls) -> list:
        """Previous compositions (the archives Generate creates). index.html —
        the live composition — is deliberately not listed; it can't be deleted."""
        out = []
        for f in cls._archive_files():
            st = f.stat()
            out.append({"file": f.name, "size": st.st_size, "modified": int(st.st_mtime)})
        return out

    @classmethod
    def cleanup_comps(cls, files: Optional[list] = None) -> dict:
        """Delete previous compositions (all comp_*.html, or just `files`), their
        cached thumbnails, and studio exports already imported into the Library.
        The live index.html and staged assets are never touched. → {removed, freed_mb}."""
        import json as _json
        proj = _project_dir()
        wanted = None if files is None else {Path(str(f)).name for f in files}
        removed, freed = [], 0
        for f in cls._archive_files():
            if wanted is not None and f.name not in wanted:
                continue
            freed += f.stat().st_size
            f.unlink(missing_ok=True)
            (f.parent / f"{f.stem}.plan.json").unlink(missing_ok=True)  # plan sidecar
            removed.append(f.name)
            for thumb in (proj / ".thumbnails").glob(f"*_{f.name}_*"):
                freed += thumb.stat().st_size
                thumb.unlink(missing_ok=True)
        # Studio exports (renders/*.mp4) already imported into the Library are
        # duplicates on disk — a full cleanup reclaims them too.
        if wanted is None:
            renders = proj / "renders"
            marker = renders / ".vm-imported.json"
            try:
                done = set(_json.loads(marker.read_text())) if marker.exists() else set()
            except Exception:
                done = set()
            for mp4 in list(renders.glob("*.mp4")) if renders.exists() else []:
                if mp4.name in done:
                    freed += mp4.stat().st_size
                    mp4.unlink(missing_ok=True)
                    (renders / f"{mp4.stem}.meta.json").unlink(missing_ok=True)
                    removed.append(f"renders/{mp4.name}")
        return {"removed": removed, "freed_mb": round(freed / (1024 * 1024), 1)}

    # ── Import the studio's own exports into the ViralMint Library ────────────

    @classmethod
    async def import_renders(cls) -> dict:
        """The studio's Export writes MP4s to project/renders/. Import any NEW
        ones into the Library as GeneratedVideo rows. Idempotent (tracks done
        filenames in a marker). → {imported, ids}."""
        async with cls._import_lock:
            return await cls._import_renders_locked()

    @classmethod
    async def _import_renders_locked(cls) -> dict:
        import json as _json
        renders = _project_dir() / "renders"
        if not renders.exists():
            return {"imported": 0, "ids": []}
        marker = renders / ".vm-imported.json"
        try:
            done = set(_json.loads(marker.read_text())) if marker.exists() else set()
        except Exception:
            done = set()
        new_ids = []
        dirty = False
        for mp4 in sorted(renders.glob("*.mp4")):
            if mp4.name in done:
                continue
            # The studio writes the .meta.json sidecar when the render finishes —
            # skip until it exists so we never import a mid-render partial file.
            if not (renders / f"{mp4.stem}.meta.json").exists():
                continue
            try:
                gid = await cls._import_one(mp4)
                if gid:
                    new_ids.append(gid)
            except Exception as e:
                logger.warning("Motion Studio: render import failed (%s): %s", mp4.name, e)
            done.add(mp4.name)  # mark even on failure so a bad file isn't retried forever
            dirty = True
        # Persist the done-set whenever it grew — INCLUDING a failure-only batch.
        # Gating this on new_ids (as it was) meant a permanently-bad export was
        # never recorded and got reprocessed (copy + ffprobe) on every poll,
        # defeating the "don't retry a bad file forever" intent above.
        if dirty:
            try:
                marker.write_text(_json.dumps(sorted(done)))
            except OSError as e:
                # The next poll will re-import these files and create duplicate
                # Library rows. Worth a warning: it usually means the disk is
                # full, which is exactly when large renders exist.
                logger.warning("Motion Studio: import marker not saved (%s) — "
                               "these renders may import again", e)
        return {"imported": len(new_ids), "ids": new_ids}

    @staticmethod
    async def _import_one(mp4: Path) -> Optional[str]:
        import asyncio
        from uuid import uuid4
        from backend.agents.generator_motion import MotionGeneratorAgent
        if mp4.stat().st_size < 1024:
            return None
        dest = settings.GENERATED_DIR / f"motion_{uuid4().hex[:12]}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, mp4, dest)
        # dims + duration in one ffprobe, via the shared helpers
        from backend.services.video_utils import aspect_from_dims, probe_media
        w, h, dur = await asyncio.to_thread(probe_media, dest)
        aspect = aspect_from_dims(w, h)
        thumb = await MotionGeneratorAgent._thumbnail(dest)
        return await MotionGeneratorAgent._save(
            user_id="local", title="Motion Studio video", variables={}, niche=None,
            video_path=dest, thumb_path=thumb, audio_path=None, aspect_ratio=aspect,
            duration_seconds=int(round(dur)) if dur else None,
            script=None, caption_status=None)
