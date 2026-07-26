# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Drift guard: the frontend's endpoint → job-type map must match the backend.

ToolRunner tags every job it starts with a job type derived from the endpoint
(`/api/tools/<slug>` → `tool:<slug_snake>`), with a small override table for the
handlers that predate that convention
(frontend/src/components/tools/toolJobType.js). That tag is the key ToolRunner
re-attaches on, so a wrong value fails SILENTLY: navigate away and back mid-run
and the page shows its empty dropzone while the job is still going, so the user
thinks their file is gone.

That is exactly what /tools/subtitles did — its handler creates
`tool:subtitle_export`, the frontend derived `tool:subtitles`, and nothing
complained. So we assert it mechanically: read the real `create_job(...)`
literal out of backend/api/tools.py for every endpoint a page actually posts to,
resolve the same endpoint through the JS table, and require them to agree.

Adding a tool whose handler names its job differently now fails here instead of
quietly costing re-attach.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS_API = REPO / "backend" / "api" / "tools.py"
JOB_TYPE_JS = REPO / "frontend" / "src" / "components" / "tools" / "toolJobType.js"
FRONTEND_SRC = REPO / "frontend" / "src"


# ── Backend: route path → the job types its handler can create ──────────────

def _backend_route_job_types() -> dict[str, set[str]]:
    """{"/captions": {"tool:captions"}, …} parsed from the router source.

    A handler with more than one `create_job` (branching) contributes all of
    them; the assertion below only requires the frontend's value to be one the
    handler can actually produce.
    """
    tree = ast.parse(TOOLS_API.read_text())
    routes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        path = None
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in ("post", "get")
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                path = dec.args[0].value
        if path is None:
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", "") == "create_job"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)):
                routes.setdefault(path, set()).add(sub.args[0].value)
    return routes


# ── Frontend: the override table + the endpoints pages actually use ─────────

_ENTRY_RE = re.compile(r'^\s*["\']?([\w-]+)["\']?\s*:\s*["\']([^"\'\n]+)["\']\s*,?\s*$')


def _js_overrides() -> dict[str, str]:
    """Parse JOB_TYPE_OVERRIDES out of toolJobType.js.

    Regex rather than a JS runtime on purpose — the table is a flat literal of
    string pairs, and requiring node in the Python suite would be worse. Parsed
    line-by-line with `//` comments stripped first: the prose around an entry
    mentions job-type strings, and a comment-blind pattern happily matches those
    instead. The non-empty assertion catches a shape change.
    """
    src = JOB_TYPE_JS.read_text()
    block = re.search(r"JOB_TYPE_OVERRIDES\s*=\s*\{(.*?)\n\}", src, re.S)
    assert block, "JOB_TYPE_OVERRIDES literal not found in toolJobType.js"
    pairs = {}
    for line in block.group(1).splitlines():
        line = line.split("//", 1)[0]
        m = _ENTRY_RE.match(line)
        if m:
            pairs[m.group(1)] = m.group(2)
    assert pairs, "JOB_TYPE_OVERRIDES parsed as empty — did its shape change?"
    return pairs


def _tool_job_type_for(slug: str, overrides: dict[str, str]) -> str:
    """Python mirror of toolJobTypeFor() in toolJobType.js."""
    return overrides.get(slug) or f"tool:{slug.replace('-', '_')}"


def _page_endpoints() -> set[str]:
    """Every `endpoint="/api/tools/…"` a page hands to ToolRunner."""
    found: set[str] = set()
    for f in FRONTEND_SRC.rglob("*.jsx"):
        found.update(re.findall(r'endpoint="(/api/tools/[^"]+)"', f.read_text()))
    return found


# ── Tests ───────────────────────────────────────────────────────────────────

def test_every_tool_page_endpoint_resolves_to_the_real_job_type():
    routes = _backend_route_job_types()
    overrides = _js_overrides()
    endpoints = _page_endpoints()
    assert endpoints, "no ToolRunner endpoints found — did the prop get renamed?"

    mismatches = []
    for endpoint in sorted(endpoints):
        slug = endpoint.split("/api/tools/")[1]
        actual = routes.get("/" + slug)
        if not actual:
            mismatches.append(f"{endpoint}: no create_job(...) found on its handler")
            continue
        resolved = _tool_job_type_for(slug, overrides)
        if resolved not in actual:
            mismatches.append(
                f"{endpoint}: frontend uses {resolved!r}, backend creates {sorted(actual)!r} "
                f"— add an entry to JOB_TYPE_OVERRIDES in toolJobType.js"
            )
    assert not mismatches, (
        "ToolRunner would tag these jobs with a type nothing matches, silently "
        "breaking mid-job re-attach:\n  " + "\n  ".join(mismatches)
    )


def test_no_stale_override_entries():
    """An override for a slug no page posts to is dead weight — and usually the
    fingerprint of a renamed endpoint whose real mapping is now unchecked."""
    overrides = _js_overrides()
    slugs = {e.split("/api/tools/")[1] for e in _page_endpoints()}
    stale = sorted(set(overrides) - slugs)
    assert not stale, f"JOB_TYPE_OVERRIDES entries for unused endpoints: {stale}"


@pytest.mark.parametrize("slug,expected", [
    ("captions", "tool:captions"),
    ("auto-chapters", "tool:auto_chapters"),
    ("music-visualizer", "tool:music_visualizer"),
    ("subtitles", "tool:subtitle_export"),
])
def test_resolution_rule(slug, expected):
    """Pin the derivation itself, so a change to the rule (not just the table)
    has to be deliberate."""
    assert _tool_job_type_for(slug, _js_overrides()) == expected


def test_the_subtitles_override_is_load_bearing():
    """Without the override, the derived type would not match the backend — this
    is the bug the table exists to prevent, pinned so nobody 'simplifies' it
    away."""
    derived = _tool_job_type_for("subtitles", {})
    real = _backend_route_job_types()["/subtitles"]
    assert derived not in real, (
        "the plain slug rule now matches /subtitles — if the handler was "
        "renamed, drop the override instead of keeping a no-op entry"
    )
