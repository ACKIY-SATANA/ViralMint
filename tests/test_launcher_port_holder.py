# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""launcher._terminate_port_holder must wait for the holder to EXIT, not just to
release the port (the restart-handoff race — see database.sweep_stale_jobs).

POSIX-only: the Windows path is a synchronous hard kill (taskkill /F) with no
drain concept, and os.kill(pid, 0) terminates rather than probes there.
"""
import platform
import socket
import subprocess
import sys
import textwrap
import time

import pytest

import launcher

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows", reason="drain-wait is POSIX-only by design"
)

def _free_port() -> int:
    """Claim an ephemeral port from the OS, then release it.

    Deliberately NOT a hardcoded constant: _terminate_port_holder SIGTERMs
    whatever owns the port, so a fixed number risks signalling an unrelated
    process (a stale holder from an earlier run, another checkout's suite) and
    makes the test fail for reasons that have nothing to do with the drain-wait.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# A holder that mimics uvicorn's shutdown shape: on SIGTERM it CLOSES the
# listening socket immediately (port free) but keeps the process alive ~1.5s
# (the drain), then exits. The old implementation returned as soon as SIGTERM
# was delivered; the fix must not return until the process is actually gone.
def _holder_src(port: int) -> str:
    return textwrap.dedent(f"""
        import signal, socket, sys, time
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", {port}))
        s.listen(1)
        def bye(signum, frame):
            s.close()            # port frees NOW...
            time.sleep(1.5)      # ...but the process drains on
            sys.exit(0)
        signal.signal(signal.SIGTERM, bye)
        print("ready", flush=True)
        while True:
            time.sleep(0.2)
    """)


def _wait_port_bound(port: int, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.1)
    return False


def test_waits_for_holder_process_exit():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", _holder_src(port)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready", "holder failed to start"
        assert _wait_port_bound(port), "holder never bound the test port"

        start = time.time()
        launcher._terminate_port_holder(port=port, wait_seconds=10)
        elapsed = time.time() - start

        # Must have outlived the 1.5s drain — i.e. actually waited for exit,
        # not merely for SIGTERM delivery / port release.
        assert elapsed >= 1.2, f"returned after {elapsed:.2f}s — did not wait for exit"
        assert proc.poll() is not None, "holder still alive after _terminate_port_holder"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_no_holder_returns_fast():
    port = _free_port()  # nothing is listening on it
    start = time.time()
    launcher._terminate_port_holder(port=port, wait_seconds=10)
    assert time.time() - start < 2.0
