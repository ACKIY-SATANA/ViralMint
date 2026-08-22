# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""One parser for the `Range` header, for every surface that serves media.

Before this, no media route here honoured `Range` at all: `/api/videos/{id}/
stream`, `/api/downloaded/{id}/stream`, `/api/library/asset/{job_id}` and
`/api/library/track/{filename}` each answered with a plain `FileResponse`.
That works for a download and is wrong for a scrubber — depending on the
Starlette version underneath, a `<video>` asking for the middle of a 150 MB
podcast either gets the whole file or gets whatever that version decided to
do with the header. The cutting bench is built on seeking, so the behaviour
has to be ours and it has to be one implementation (four copies of a range
parser is four chances to disagree).

RFC 9110 semantics, with the two departures worth naming:

  * The suffix form. `Range: bytes=-500` asks for the LAST 500 bytes, which
    is how a player finds an MP4's trailing `moov` atom. The obvious parse —
    split on "-", read field 0 as the start — yields 0 and answers with the
    FIRST 501 bytes under a `Content-Range` claiming the request was
    satisfied. Not an error, not a 416: the wrong bytes presented as the
    right ones.
  * A syntactically invalid Range SHOULD be ignored (serve 200). We raise
    416 instead — a client of ours that sends one has a bug worth seeing —
    but the 416 carries the `Content-Range: bytes */size` the spec requires.
  * Multipart ranges (`bytes=0-99,200-299`) are refused rather than
    assembled. Nothing here asks for them and a wrong multipart body is
    worse than a clean refusal.
"""
from __future__ import annotations

# How much of an open-ended `bytes=N-` we actually answer.
#
# Answering it literally means streaming from N to EOF, which looks harmless
# and is the worst thing you can do to a scrubbing surface: Starlette runs a
# sync body generator through `iterate_in_threadpool`, so each such response
# parks an anyio worker until the browser drains it — minutes, for a 154 MB
# source somebody is only scrubbing. `asyncio.to_thread` draws from that SAME
# pool, so the cutting bench's filmstrip ffmpeg calls would queue behind idle
# video streams, and with three media elements each holding one open a new
# seek could not get served at all.
#
# A bounded window is what nginx's slice module and every CDN do. A 206 may
# return less than was asked for; the player requests the next window when it
# needs it, and each response completes in milliseconds and frees its thread.
OPEN_RANGE_WINDOW_BYTES = 4 * 1024 * 1024


class RangeNotSatisfiable(ValueError):
    """The header parsed but cannot be served against this file.

    Carries the `Content-Range: bytes */size` a 416 is required to include, so
    callers cannot forget it.
    """

    def __init__(self, detail: str, file_size: int):
        super().__init__(detail)
        self.detail = detail
        self.file_size = file_size

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Range": f"bytes */{self.file_size}"}


def parse_range(
    header: str | None,
    file_size: int,
    *,
    window: int | None = OPEN_RANGE_WINDOW_BYTES,
) -> tuple[int, int] | None:
    """Resolve a `Range` header to inclusive `(start, end)` byte offsets.

    Args:
        header: the raw header value, or None/"" when the client sent none.
        file_size: size of the file on disk, in bytes.
        window: cap applied ONLY to the open-ended `bytes=N-` form — the one
            every `<video>` sends. Pass None to answer it to EOF. An explicit
            `bytes=N-M` is always honoured exactly: a client that names its
            end is asking for a specific thing.

    Returns:
        `(start, end)` inclusive, or None when there is no Range to honour and
        the caller should serve the whole file.

    Raises:
        RangeNotSatisfiable: the header is malformed, names a start at or past
            EOF, or ends before it starts.
    """
    if not header:
        return None
    spec = header.strip()
    if spec.lower().startswith("bytes="):
        spec = spec[len("bytes="):]
    spec = spec.strip()
    if not spec:
        raise RangeNotSatisfiable("Malformed Range header", file_size)
    if "," in spec:
        raise RangeNotSatisfiable("Multipart ranges are not supported", file_size)

    first, sep, last = spec.partition("-")
    if not sep:
        raise RangeNotSatisfiable("Malformed Range header", file_size)
    first, last = first.strip(), last.strip()

    try:
        if not first:
            # Suffix form: `bytes=-N` is the LAST n bytes, not the first n.
            n = int(last)
            if n <= 0:
                raise RangeNotSatisfiable("Malformed Range header", file_size)
            if file_size == 0:
                raise RangeNotSatisfiable("Range beyond end of file", file_size)
            return (max(0, file_size - n), file_size - 1)
        start = int(first)
        explicit_end = int(last) if last else None
    except ValueError:
        raise RangeNotSatisfiable("Malformed Range header", file_size) from None

    if start < 0:
        raise RangeNotSatisfiable("Malformed Range header", file_size)
    if start >= file_size:
        raise RangeNotSatisfiable("Range beyond end of file", file_size)

    if explicit_end is None:
        end = file_size - 1 if window is None else min(start + window - 1, file_size - 1)
    else:
        end = min(explicit_end, file_size - 1)
    if end < start:
        raise RangeNotSatisfiable("Range end precedes start", file_size)
    return (start, end)
