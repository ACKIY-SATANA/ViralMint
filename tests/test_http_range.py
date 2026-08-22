# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The one Range parser, and the four routes that now share it.

The parser's job is small and every one of these cases was a real way to get
it wrong: the suffix form silently serving the wrong bytes, an open-ended
range pinning a worker thread until a 150 MB file drains, a 416 without the
Content-Range it is required to carry.
"""
import pathlib

import pytest

from backend.core.http_range import (
    OPEN_RANGE_WINDOW_BYTES,
    RangeNotSatisfiable,
    parse_range,
)

SIZE = 1000


def test_no_header_means_serve_the_whole_file():
    assert parse_range(None, SIZE) is None
    assert parse_range("", SIZE) is None


def test_explicit_range_is_honoured_exactly():
    # A client that names its end is asking for a specific thing — the window
    # must not truncate it.
    assert parse_range("bytes=10-99", SIZE) == (10, 99)
    big = 50 * 1024 * 1024
    assert parse_range(f"bytes=0-{big - 1}", big) == (0, big - 1)


def test_explicit_end_past_eof_clamps():
    assert parse_range("bytes=990-99999", SIZE) == (990, 999)


def test_open_ended_range_is_bounded_by_the_window():
    """`bytes=N-` is what every <video> sends, and answering it to EOF parks a
    worker thread for the whole download."""
    big = 50 * 1024 * 1024
    start, end = parse_range("bytes=0-", big)
    assert start == 0
    assert end == OPEN_RANGE_WINDOW_BYTES - 1

    # Smaller than the window → the file's own end, not a phantom offset.
    assert parse_range("bytes=0-", SIZE) == (0, 999)


def test_open_ended_can_be_answered_to_eof_when_asked():
    big = 50 * 1024 * 1024
    assert parse_range("bytes=0-", big, window=None) == (0, big - 1)


def test_suffix_form_is_the_LAST_n_bytes():
    """`bytes=-500` asks for the tail — that is how a player finds an MP4's
    trailing moov atom. Reading field 0 as the start answers with the FIRST
    501 bytes under a Content-Range claiming success."""
    assert parse_range("bytes=-500", SIZE) == (500, 999)
    # Longer than the file → the whole file, not a negative offset.
    assert parse_range("bytes=-99999", SIZE) == (0, 999)


@pytest.mark.parametrize("header", [
    "bytes=", "bytes=abc", "bytes=1", "bytes=-0", "bytes=-abc", "bytes=-1-2",
])
def test_malformed_headers_are_416(header):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, SIZE)


def test_start_at_or_past_eof_is_416():
    with pytest.raises(RangeNotSatisfiable):
        parse_range(f"bytes={SIZE}-", SIZE)


def test_end_before_start_is_416():
    with pytest.raises(RangeNotSatisfiable):
        parse_range("bytes=500-100", SIZE)


def test_multipart_is_refused_not_assembled():
    with pytest.raises(RangeNotSatisfiable):
        parse_range("bytes=0-99,200-299", SIZE)


def test_416_carries_the_content_range_the_spec_requires():
    try:
        parse_range("bytes=99999-", SIZE)
    except RangeNotSatisfiable as e:
        assert e.headers == {"Content-Range": f"bytes */{SIZE}"}
    else:  # pragma: no cover
        pytest.fail("expected RangeNotSatisfiable")


def test_bare_bytes_prefix_is_optional_and_whitespace_tolerated():
    assert parse_range("  bytes = 10-20 ".replace(" = ", "="), SIZE) == (10, 20)
    assert parse_range("10-20", SIZE) == (10, 20)


# ── The wiring ────────────────────────────────────────────────────────────
# A parser nobody calls fixes nothing; assert the routes actually reach it.

def _fake_request(header=None):
    class _R:
        headers = {} if header is None else {"range": header}
    return _R()


def test_serve_media_with_range_returns_206_and_the_right_slice(tmp_path):
    from backend.api.videos import serve_media_with_range

    f = tmp_path / "clip.mp4"
    f.write_bytes(bytes(range(256)) * 8)      # 2048 bytes
    resp = serve_media_with_range(f, _fake_request("bytes=100-199"))
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 100-199/2048"
    assert resp.headers["content-length"] == "100"
    assert resp.media_type == "video/mp4"


def test_serve_media_with_range_without_a_range_serves_the_whole_file(tmp_path):
    from fastapi.responses import FileResponse

    from backend.api.videos import serve_media_with_range

    f = tmp_path / "track.mp3"
    f.write_bytes(b"x" * 64)
    resp = serve_media_with_range(f, _fake_request())
    assert isinstance(resp, FileResponse)
    assert resp.media_type == "audio/mpeg"
    assert resp.headers["accept-ranges"] == "bytes"


def test_serve_media_with_range_maps_non_media_suffixes(tmp_path):
    """Tool outputs include .srt / .txt / .json — the Library asset route
    serves those through this same helper."""
    from backend.api.videos import serve_media_with_range

    f = tmp_path / "captions.srt"
    f.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    assert serve_media_with_range(f, _fake_request()).media_type == "text/plain"


def test_serve_media_with_range_raises_416_with_headers(tmp_path):
    from fastapi import HTTPException

    from backend.api.videos import serve_media_with_range

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 10)
    with pytest.raises(HTTPException) as ei:
        serve_media_with_range(f, _fake_request("bytes=999-"))
    assert ei.value.status_code == 416
    assert ei.value.headers == {"Content-Range": "bytes */10"}


def test_the_tool_download_route_is_range_aware_too():
    """ToolRunner mounts /api/tools/download/{id} as its result preview's
    `<video src>`, so it is scrubbed as often as it is saved — it belongs on
    the shared streamer with the other four. This was missed on the first pass
    and found by asking which OTHER routes a `<video>` points at."""
    src = pathlib.Path("backend/api/tools.py").read_text()
    assert "serve_media_with_range" in src
    assert "request: Request" in src


def test_every_route_a_player_points_at_shares_one_streamer():
    """The whole point of the shared helper is that there is no fifth copy.

    A route that returns a bare FileResponse for scrubbable media is the bug
    this replaced; the ones left are downloads and images, where Range buys
    nothing.
    """
    import re
    players = {
        "backend/api/videos.py": 2,      # /videos/{id}/stream + /stream/{aspect}
        "backend/api/downloaded.py": 1,  # /downloaded/{id}/stream
        "backend/api/library.py": 2,     # /library/asset + /library/track
        "backend/api/tools.py": 1,       # /tools/download/{job_id}
    }
    for path, expected in players.items():
        src = pathlib.Path(path).read_text()
        n = len(re.findall(r"serve_media_with_range\(", src))
        # videos.py also DEFINES it, so allow the definition line there.
        n -= 1 if path.endswith("videos.py") else 0
        assert n >= expected, f"{path}: {n} call(s), expected at least {expected}"
