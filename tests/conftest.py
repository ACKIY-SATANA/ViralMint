# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Shared test configuration — runs before any test module imports."""
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

# ── The suite must never write into the developer's own database ────────────
#
# `backend.config._resolve_data_dir()` falls back to `Path.cwd()` when
# VIRALMINT_DATA_DIR is unset, and pytest runs from the repo root — the SAME
# cwd `python run.py` uses. So the whole suite shared one `./viralmint.db`
# with the running dev app: measured on this checkout, 2,335 untitled job rows
# and `generated_videos` rows pointing at `pytest-of-*` tmpdirs, which surface
# in the Library as tiles whose stream 403s (the path is outside STORAGE_ROOT).
#
# This must be set BEFORE any `backend.*` import, because DATA_DIR is resolved
# once at module import time. conftest.py is the earliest hook pytest offers.
# The on-demand plugin trees are large, read-only and expensive to install, so
# they are SYMLINKED into the isolated dir rather than isolated away —
# otherwise the tests that exercise an installed plugin silently skip on a
# machine that has it, and the isolation costs real local coverage.
#
# Set VIRALMINT_DATA_DIR yourself to opt out of all of this.
_READ_ONLY_PLUGIN_TREES = ("motion", "voxcpm", "whisper-cache", "models")

if not os.environ.get("VIRALMINT_DATA_DIR"):
    _dev_dir = Path.cwd()
    _tmp = Path(tempfile.mkdtemp(prefix="viralmint-tests-"))
    for _name in _READ_ONLY_PLUGIN_TREES:
        _src = _dev_dir / _name
        if _src.is_dir():
            try:
                (_tmp / _name).symlink_to(_src, target_is_directory=True)
            except OSError:  # a symlink is a convenience, never a requirement
                pass
    os.environ["VIRALMINT_DATA_DIR"] = str(_tmp)

# Generate a valid Fernet key for all tests
_TEST_KEY = Fernet.generate_key().decode()
os.environ.setdefault("ENCRYPTION_KEY", _TEST_KEY)
