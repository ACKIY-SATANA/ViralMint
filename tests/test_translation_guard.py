# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The browser page-translation guard — wiring, and the behaviour itself.

Chrome/Edge/Safari "translate this page" rewraps text nodes in <font>
elements, re-parenting nodes React still tracks. React's next update then
calls removeChild/insertBefore against a parent that is no longer the node's
parent, the DOM throws NotFoundError, and the whole router unmounts into the
ErrorBoundary. Chat is the worst case — streaming tokens mount and unmount
text nodes continuously — and index.html says lang="en", so every non-English
visitor is offered the translation on first load.

There is no JS test runner in this repo, so the behavioural half runs the real
module under node against a stand-in prototype. It is skipped when node isn't
on PATH; the wiring half always runs.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
GUARD = FRONTEND / "utils" / "translationGuard.js"
MAIN = FRONTEND / "main.jsx"


class TestWiring:
    def test_guard_module_exists(self) -> None:
        assert GUARD.exists(), "translationGuard.js is missing"

    def test_guard_patches_both_methods(self) -> None:
        """Only patching removeChild leaves the insertBefore crash live."""
        src = GUARD.read_text()
        assert "removeChild" in src
        assert "insertBefore" in src

    def test_installed_before_the_first_render(self) -> None:
        """The whole point is to patch BEFORE React mounts anything — installing
        after createRoot would leave the first render unprotected."""
        src = MAIN.read_text()
        assert "installTranslationGuard" in src, "guard never installed"
        install_at = src.index("installTranslationGuard()")
        render_at = src.index("createRoot")
        assert install_at < render_at, "guard installs after the first render"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
class TestBehaviour:
    """Drive the real module under node with a stand-in Node prototype."""

    def _run(self) -> dict:
        script = f"""
        import {{ installTranslationGuard }} from {json.dumps(str(GUARD))}

        // A minimal stand-in for the DOM's Node: the two methods the guard
        // patches, each throwing the way a browser does on a foreign child.
        class FakeNode {{
          constructor(name) {{ this.name = name; this.parentNode = null; this.children = [] }}
          removeChild(child) {{
            if (child.parentNode !== this) throw new Error("NotFoundError")
            this.children = this.children.filter((c) => c !== child)
            child.parentNode = null
            return child
          }}
          insertBefore(node, ref) {{
            if (ref && ref.parentNode !== this) throw new Error("NotFoundError")
            const at = ref ? this.children.indexOf(ref) : this.children.length
            this.children.splice(at, 0, node)
            node.parentNode = this
            return node
          }}
        }}

        installTranslationGuard(FakeNode)

        const out = {{}}
        const parent = new FakeNode("parent")
        const owned = new FakeNode("owned")
        parent.insertBefore(owned, null)

        // 1. Normal operation is untouched.
        out.normal_remove_works = parent.removeChild(owned) === owned
        out.normal_remove_detached = owned.parentNode === null

        // 2. A node the translation layer re-parented: React asks the OLD
        //    parent to remove it. Must not throw — the removal has in effect
        //    already happened.
        const moved = new FakeNode("moved")
        const newParent = new FakeNode("newParent")
        newParent.insertBefore(moved, null)
        try {{
          parent.removeChild(moved)
          out.foreign_remove_survives = true
        }} catch (e) {{ out.foreign_remove_survives = false }}

        // 3. An anchor that moved: insertBefore must append rather than throw.
        const anchor = new FakeNode("anchor")
        newParent.insertBefore(anchor, null)
        const fresh = new FakeNode("fresh")
        try {{
          parent.insertBefore(fresh, anchor)
          out.foreign_anchor_survives = true
          out.fresh_was_appended = parent.children.includes(fresh)
        }} catch (e) {{ out.foreign_anchor_survives = false }}

        console.log(JSON.stringify(out))
        """
        res = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=60,
        )
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_normal_operation_is_untouched(self) -> None:
        out = self._run()
        assert out["normal_remove_works"] is True
        assert out["normal_remove_detached"] is True

    def test_removing_an_externally_moved_node_does_not_throw(self) -> None:
        out = self._run()
        assert out["foreign_remove_survives"] is True

    def test_a_moved_anchor_appends_instead_of_throwing(self) -> None:
        out = self._run()
        assert out["foreign_anchor_survives"] is True
        assert out["fresh_was_appended"] is True
