# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The WS client must keep exactly ONE live socket.

connect() is called on every Layout mount, and React StrictMode deliberately
double-invokes that effect in development. The old guard only skipped when the
existing socket was already OPEN — so the second call landed while the first
was still CONNECTING and built a SECOND WebSocket without closing the first.
Both then fed the same listener set, so every server event was handled twice:
duplicate chat bubbles, doubled streaming text, duplicate job cards.

There is no JS test runner in this repo, so this drives the real module under
node against a fake WebSocket. Skipped when node isn't on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WS_MODULE = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "websocket.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _run() -> dict:
    script = f"""
    // Sockets built during this run, so the test can count them and drive
    // their lifecycle by hand.
    const built = []
    class FakeWebSocket {{
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      constructor(url) {{
        this.url = url
        this.readyState = FakeWebSocket.CONNECTING
        this.sent = []
        this.closed = false
        built.push(this)
      }}
      send(data) {{ this.sent.push(data) }}
      close() {{ this.closed = true; this.readyState = FakeWebSocket.CLOSED
                 if (this.onclose) this.onclose() }}
      _open() {{ this.readyState = FakeWebSocket.OPEN; if (this.onopen) this.onopen() }}
      _emit(obj) {{ if (this.onmessage) this.onmessage({{ data: JSON.stringify(obj) }}) }}
    }}
    globalThis.WebSocket = FakeWebSocket
    globalThis.window = {{ location: {{ protocol: "http:", host: "127.0.0.1:16888" }} }}

    const mod = await import({json.dumps(str(WS_MODULE))})
    const ws = mod.default || mod.ws || mod.viralmintWS
    const out = {{}}

    // StrictMode: connect() twice, the second while the first is CONNECTING.
    ws.connect()
    ws.connect()
    out.sockets_built = built.length

    // Deliver one server event and count how many times a listener sees it.
    let seen = 0
    ws.on("chat_token", () => {{ seen += 1 }})
    built[0]._open()
    built[0]._emit({{ type: "chat_token", token: "hi" }})
    out.handler_calls_for_one_event = seen

    // A socket the client has SUPERSEDED must go silent even though its
    // handlers are still wired up — that is what stops a lingering half-open
    // socket from double-delivering. Supersede socket #1 by hand (a reconnect
    // does exactly this) and check both paths.
    const stale = built[0]
    ws.ws = new FakeWebSocket("ws://current")
    const before = seen
    stale._emit({{ type: "chat_token", token: "dupe" }})
    out.stale_delivers = seen > before

    stale.readyState = FakeWebSocket.CONNECTING
    stale._open()                       // superseded-while-connecting path
    out.stale_closed_itself = stale.closed

    console.log(JSON.stringify(out))
    """
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_double_connect_builds_only_one_socket() -> None:
    """The StrictMode double-mount must not leave two live sockets."""
    assert _run()["sockets_built"] == 1


def test_one_server_event_is_handled_once() -> None:
    assert _run()["handler_calls_for_one_event"] == 1


def test_a_superseded_socket_goes_silent() -> None:
    """A socket that is no longer `this.ws` must drop events even while its
    handlers are still wired up — that's what stops a lingering half-open
    socket from double-delivering after a reconnect."""
    assert _run()["stale_delivers"] is False


def test_a_socket_superseded_while_connecting_closes_itself() -> None:
    """Otherwise it opens behind the current one and quietly doubles traffic."""
    assert _run()["stale_closed_itself"] is True
