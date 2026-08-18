# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Shared resumable downloader for the on-demand plugin installers.

Motion Graphics (a portable Node archive) and any future on-demand dependency
fetch a large artifact over a link the user's network may drop mid-stream. This
module is the ONE implementation of that fetch: Range-resume, the
partial-content edge cases, atomic rename, and streaming sha256 — so the next
installer inherits the edge cases rather than rediscovering them.

Contract: the artifact is streamed to `<dest>.part` and only renamed onto
`dest` once the bytes are complete AND (when a checksum was supplied) verified.
A caller therefore never observes a half-written `dest`.
  - network failure  → `.part` is KEPT; it's the resume point for the next try.
  - checksum failure → `.part` is DELETED; its bytes are proven bad, so the
    next attempt must start clean rather than resume onto a bad base.
"""
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_CHUNK = 1 << 18  # 256 KB


class DownloadFailed(Exception):
    """Transport-level failure (network, HTTP status, disk write). The partial
    file is retained so a retry resumes instead of restarting."""


class ChecksumMismatch(Exception):
    """The completed download did not match the expected sha256. The partial
    file has been removed — a retry starts clean."""


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file (chunked, so a 545 MB GGUF never loads into
    memory). Sync — call via asyncio.to_thread from async code."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def download_with_resume(
    url: str,
    dest: Path,
    *,
    expected_sha256: Optional[str] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    on_verify: Optional[Callable[[], None]] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    chunk_size: int = _DEFAULT_CHUNK,
) -> None:
    """Download `url` to `dest`, resuming a previous partial attempt if present.

    Args:
        url: the artifact URL (redirects are followed — HF and nodejs.org both
            redirect to a CDN).
        dest: final path. Its parent is created if missing. Streamed via
            `<dest>.part` and renamed only on success.
        expected_sha256: hex digest to verify before the rename. None skips
            verification here (for callers that resolve the digest only after
            the download — they must verify `dest` themselves).
        on_progress: called with a 0.0–1.0 fraction as bytes arrive. Callers map
            it onto their own progress-bar segment.
        on_verify: called once, immediately before the (potentially multi-second)
            checksum pass, so the UI can show a "Verifying…" step.
        timeout: per-request httpx timeout.
        chunk_size: stream chunk size.

    Raises:
        DownloadFailed: transport/status/write failure. `.part` retained.
        ChecksumMismatch: digest mismatch. `.part` removed.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    # A previous attempt may have left bytes on disk — resume from that offset
    # rather than re-pulling the whole artifact (a 90%-done 545 MB download used
    # to restart from zero on any blip).
    resume_from = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    already_complete = False

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resume_from and resp.status_code == 416:
                    # Range unsatisfiable → the `.part` already spans the whole
                    # object (a crash landed between download-done and rename).
                    # Fall through to verification: a good part renames into
                    # place, a bad one is deleted so the NEXT attempt starts
                    # clean. Without this branch raise_for_status kept the part
                    # and every retry re-sent the same unsatisfiable Range — a
                    # permanent install wedge.
                    already_complete = True
                elif resume_from and resp.status_code != 206:
                    # Server ignored the Range (or the object changed underneath
                    # us) — restart rather than append onto a mismatched base.
                    part.unlink(missing_ok=True)
                    resume_from = 0
                    resp.raise_for_status()
                else:
                    resp.raise_for_status()

                if not already_complete:
                    total = resume_from + (int(resp.headers.get("content-length", 0)) or 1)
                    written = resume_from
                    if resume_from:
                        logger.info("Resuming download of %s at %d MB", dest.name, resume_from >> 20)
                    with part.open("ab" if resume_from else "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size):
                            f.write(chunk)
                            written += len(chunk)
                            if on_progress:
                                on_progress(min(1.0, written / total))
    except Exception as e:
        # Keep the `.part` — it is the resume point for the next attempt.
        raise DownloadFailed(str(e)[:160]) from e

    if expected_sha256:
        if on_verify:
            on_verify()
        actual = await asyncio.to_thread(sha256_file, part)
        if actual.lower() != expected_sha256.lower():
            # These bytes are proven bad; resuming onto them would never
            # converge. Delete so the retry starts from zero.
            part.unlink(missing_ok=True)
            raise ChecksumMismatch(f"{dest.name}: sha256 mismatch")

    part.replace(dest)
    if on_progress:
        on_progress(1.0)
