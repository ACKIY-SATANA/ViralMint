# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""HyperFrames CLI / runtime contract — the SINGLE source of how ViralMint talks
to the pinned `hyperframes` npm package.

Install (hyperframes_service) and render (motion_render_service) both reference
THIS module, so a version bump is one diff and the call sites can't drift on a
flag, preset or env name.

═══ VERSION-BUMP REGRESSION CHECKLIST ═══
When you change HYPERFRAMES_NPM_VERSION, re-verify EACH point below against the
new version. The install smoke-render (hyperframes_service._step_smoke_render)
exercises most of the render path automatically; check the rest by hand by
probing the installed CLI with `node <cli.js> <cmd> --help`:

  1. CLI entry — package.json "bin".hyperframes still resolves (resolve_cli_js).
  2. render command + flags — `render <dir> --output --resolution --fps
     --quality --variables` (render_argv).
  3. preview command + flags — `preview <dir> --no-open --port` and
     `preview --kill-all` (preview_argv / preview_kill_argv).
  4. --resolution preset names — ASPECT_RESOLUTION values
     (portrait / landscape / square / portrait-4k / landscape-4k / square-4k).
  5. env knobs still honored — HYPERFRAMES_BROWSER_PATH (+ NO_AUTO_INSTALL),
     _FFMPEG_PATH / _FFPROBE_PATH, _NO_TELEMETRY / _NO_UPDATE_CHECK,
     _FONT_CACHE_DIR / _EXTRACT_CACHE_DIR, _PREVIEW_HOST.
  6. composition HTML contract — window.__timelines,
     window.__hyperframes.getVariables, data-composition-id, GSAP-only.
  7. studio SPA path — <pkg>/dist/studio/index.html (resolve_studio_index).
  8. prune safety — render still works without onnxruntime-node
     (hyperframes_service._PRUNABLE_MODULES).
  9. preview bind — defaults to loopback; the _PREVIEW_HOST pin is still honored.
 10. On a NODE_VERSION bump: nodejs.org still publishes
     dist/v<ver>/SHASUMS256.txt in `<sha256>  <filename>` format. The install
     integrity gate (hyperframes_service._fetch_node_sha256) verifies the
     downloaded archive against it; a mismatch hard-fails the install.

Last verified against hyperframes 0.7.111 / Node 22.14.0 / GSAP 3.14.2.

⚠️ GSAP is NOT vendored into this repository. HyperFrames compositions are
GSAP-driven, but GSAP ships under GreenSock's own Standard License, which is
not a free-software licence and does not belong in an AGPL source tree. It is
therefore installed alongside HyperFrames as a normal npm dependency on the
user's machine (GSAP_NPM_VERSION below) and copied into each staged project at
render time. Renders stay fully offline after the one-time plugin install; the
only thing that changed is who ships the bytes.
"""
import json
import os
from pathlib import Path

from backend.config import settings

# ── Pins (the ONE place to bump; reproducible renders — see checklist above) ───
HYPERFRAMES_NPM_VERSION = "0.7.111"
NODE_VERSION = "22.14.0"
# GSAP is the animation runtime every HyperFrames composition drives. It is NOT
# a dependency of the hyperframes package (compositions are expected to carry
# their own copy), so we install it explicitly and stage it per project.
GSAP_NPM_VERSION = "3.14.2"

# Loopback host for the embedded studio preview server. The CLI has no --host
# flag, but the server reads HYPERFRAMES_PREVIEW_HOST (and already defaults to
# 127.0.0.1). We pin it explicitly so a future default change to 0.0.0.0 can't
# silently expose the studio beyond the machine — note that unlike the rest of
# ViralMint this pin is NOT relaxed under a LAN-mode HOST=0.0.0.0: the studio
# preview is an authoring surface with no auth of its own.
STUDIO_HOST = "127.0.0.1"

# ViralMint aspect → HyperFrames `--resolution` preset.
ASPECT_RESOLUTION = {
    "9:16": "portrait",
    "16:9": "landscape",
    "1:1": "square",
    "9:16-4k": "portrait-4k",
    "16:9-4k": "landscape-4k",
    "1:1-4k": "square-4k",
}


def resolve_cli_js(hyperframes_root: Path) -> Path:
    """Resolve the HyperFrames CLI entry from the installed package's package.json
    `bin` field, so a future version that moves its entry (e.g. dist/cli.js →
    bin/hyperframes.mjs) doesn't silently break is_installed() forever after a
    clean npm install. Falls back to the historical dist/cli.js path.

    `hyperframes_root` is the npm-install target (…/motion/hyperframes), which
    contains node_modules/hyperframes.
    """
    pkg_dir = hyperframes_root / "node_modules" / "hyperframes"
    fallback = pkg_dir / "dist" / "cli.js"
    try:
        meta = json.loads((pkg_dir / "package.json").read_text())
        bin_field = meta.get("bin")
        rel = None
        if isinstance(bin_field, str):
            rel = bin_field
        elif isinstance(bin_field, dict):
            rel = bin_field.get("hyperframes") or next(iter(bin_field.values()), None)
        if rel:
            cand = (pkg_dir / rel).resolve()
            if cand.exists():
                return cand
    except (OSError, ValueError, StopIteration):
        pass
    return fallback


def resolve_studio_index(hyperframes_root: Path) -> Path:
    """Resolve the studio SPA's index.html INDEPENDENTLY of the CLI entry.

    Deriving it as cli_js().parent/studio/index.html breaks the moment the bin
    entry moves out of dist/ (0.7.64 did exactly that — the SPA stayed at
    dist/studio/ while the derivation followed the bin). Anchor on the package
    root's canonical dist/studio path, falling back to the CLI-relative
    derivation for any exotic future layout where dist/ itself moves.
    """
    pkg_dir = hyperframes_root / "node_modules" / "hyperframes"
    canonical = pkg_dir / "dist" / "studio" / "index.html"
    if canonical.exists():
        return canonical
    return resolve_cli_js(hyperframes_root).parent / "studio" / "index.html"


def resolve_gsap_js(hyperframes_root: Path) -> Path:
    """Resolve the installed GSAP UMD build that staged projects link against.

    See the module docstring for why this is an installed dependency rather
    than a file in this repository.
    """
    return hyperframes_root / "node_modules" / "gsap" / "dist" / "gsap.min.js"


def base_env() -> dict:
    """Shared subprocess env for every HyperFrames invocation: telemetry/update
    checks off, deterministic on-disk caches under motion/, and the loopback
    studio pin. render_env() (hyperframes_service) extends this with the FFmpeg
    binary pins; the studio uses it as-is (preview needs no binaries)."""
    return {
        **os.environ,
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "HYPERFRAMES_NO_UPDATE_CHECK": "1",
        "HYPERFRAMES_PREVIEW_HOST": STUDIO_HOST,
        "HYPERFRAMES_FONT_CACHE_DIR": str(settings.MOTION_DIR / "fonts"),
        "HYPERFRAMES_EXTRACT_CACHE_DIR": str(settings.MOTION_DIR / "cache"),
    }


def render_argv(node_bin: Path, cli_js: Path, project_dir: Path, output: Path,
                resolution: str, fps: int, quality: str, variables_json: str) -> list[str]:
    """argv for a one-shot render of a staged project → MP4.

    Deliberately NOT --strict: the linter flags quality/editability nits
    (overlapping tweens, missing Studio-edit ids) alongside real errors; we guard
    real breakage with ffprobe validation instead so lint nits don't abort an
    otherwise-good render.
    """
    return [
        str(node_bin), str(cli_js),
        "render", str(project_dir),
        "--output", str(output),
        "--resolution", resolution,
        "--fps", str(fps),
        "--quality", quality,
        "--variables", variables_json,
    ]


def preview_argv(node_bin: Path, cli_js: Path, project_dir: Path, port: int) -> list[str]:
    """argv for the embedded studio preview server (no auto-open browser)."""
    return [
        str(node_bin), str(cli_js),
        "preview", str(project_dir),
        "--no-open", "--port", str(port),
    ]


def preview_kill_argv(node_bin: Path, cli_js: Path) -> list[str]:
    """argv to kill all running preview servers (cleanup before a fresh start)."""
    return [str(node_bin), str(cli_js), "preview", "--kill-all"]


def check_argv(node_bin: Path, cli_js: Path, project_dir: Path,
               samples: int = 5) -> list[str]:
    """argv for the ONE-browser-session verification pass: lint + runtime +
    layout + motion + contrast, as machine-readable JSON.

    Supersedes plain `lint` as the authoring gate — it catches what a static
    parse cannot (console errors, elements overflowing the frame, motion
    problems). Costs a browser boot (~10s vs ~3s for lint), so `samples` is
    trimmed from the CLI default of 9: five evenly-spaced probes already cover
    every beat of a piece this short.
    """
    return [
        str(node_bin), str(cli_js),
        "check", str(project_dir), "--json", "--samples", str(samples),
    ]


def snapshot_at_argv(node_bin: Path, cli_js: Path, project_dir: Path,
                     out_dir: Path, at_seconds: list[float]) -> list[str]:
    """argv to capture frames at EXACT timestamps (not evenly spaced).

    `--describe false` stays load-bearing for the same reason as snapshot_argv.
    """
    return [
        str(node_bin), str(cli_js),
        "snapshot", str(project_dir),
        "--output", str(out_dir),
        "--at", ",".join(f"{max(0.0, float(t)):.3f}" for t in at_seconds),
        "--describe", "false",
    ]


def snapshot_argv(node_bin: Path, cli_js: Path, project_dir: Path,
                  out_dir: Path, frames: int = 4) -> list[str]:
    """argv to capture evenly-spaced key frames as PNGs.

    `--describe false` is REQUIRED: the CLI otherwise runs its own Gemini
    analysis whenever GEMINI_API_KEY happens to be in the environment, which
    would send frames to a provider the user never chose.
    """
    return [
        str(node_bin), str(cli_js),
        "snapshot", str(project_dir),
        "--output", str(out_dir),
        "--frames", str(frames),
        "--describe", "false",
    ]


def catalog_argv(node_bin: Path, cli_js: Path) -> list[str]:
    """argv to dump the bundled block/component registry as JSON."""
    return [str(node_bin), str(cli_js), "catalog", "--json"]


def add_argv(node_bin: Path, cli_js: Path, name: str) -> list[str]:
    """argv to install one registry item into the project at `cwd`.

    Blocks land in compositions/<name>.html; components in
    compositions/components/<name>.html. The CLI resolves the project from the
    working directory, so callers pass cwd=<project_dir>.
    """
    return [str(node_bin), str(cli_js), "add", str(name)]
