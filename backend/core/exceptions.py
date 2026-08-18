# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Custom exception hierarchy for ViralMint."""
import json
import logging as _logging


def safe_json_loads(raw, default=None, logger=None):
    """Parse JSON safely. Returns *default* on failure instead of raising."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        if logger:
            logger.warning("safe_json_loads failed: %s", exc)
        return default


class ViralMintError(Exception):
    """Base exception."""
    pass


# ── Scout errors ──────────────────────────────────────────────────────────────
class ScoutError(ViralMintError):
    pass

class PlatformUnavailableError(ScoutError):
    """Scout platform (TikTok, Douyin) is temporarily unavailable."""
    pass

class CookieExpiredError(ScoutError):
    """Session cookie is expired or invalid."""
    pass

class QuotaExceededError(ScoutError):
    """API quota (YouTube) exceeded."""
    pass


# ── Download errors ───────────────────────────────────────────────────────────
class DownloadError(ViralMintError):
    pass

class RateLimitError(DownloadError):
    """HTTP 429 — platform rate limiting."""
    pass

class VideoUnavailableError(DownloadError):
    """Video is private, deleted, or region-blocked."""
    pass


# ── Job lifecycle ─────────────────────────────────────────────────────────────
class JobCancelledError(ViralMintError):
    """The user cancelled the job while it was running.

    Cancellation flips the Job row to "cancelled" (backend/api/jobs.py) but
    does NOT interrupt the coroutine — long pipelines must poll
    `job_helper.job_cancelled()` at phase boundaries and raise this to stop.
    Runners catch it separately from real failures: no `job_failed` WS event
    for something the user did on purpose, and the row keeps its "cancelled"
    status instead of being overwritten by a late "success"/"failed" write.
    """
    pass


# ── Generation errors ─────────────────────────────────────────────────────────
class GenerationError(ViralMintError):
    pass

class VoiceGenerationError(GenerationError):
    pass

class VideoGenerationError(GenerationError):
    pass


# ── Upload errors ─────────────────────────────────────────────────────────────
class UploadError(ViralMintError):
    pass

class UploadAuthError(UploadError):
    """OAuth token missing or expired."""
    pass


# ── AI errors ─────────────────────────────────────────────────────────────────
class AIProviderError(ViralMintError):
    pass

class AIKeyMissingError(AIProviderError):
    pass


# ── Motion Graphics ───────────────────────────────────────────────────────────

class MotionGraphicsError(ViralMintError):
    """Base for the local motion-graphics (HyperFrames) render path."""


class HyperFramesNotInstalledError(MotionGraphicsError):
    """The on-demand HyperFrames plugin is missing.

    Raised by the install gate every motion endpoint pre-flights, so the caller
    can answer with an "install it from Settings" pointer instead of a stack
    trace. Distinct from MotionRenderError: nothing was attempted.

    ENVELOPE is what the API layer returns instead of a 500 — a structured
    answer the UI keys on to offer the install, rather than an error string
    someone has to read to understand. It is a normal 200 on purpose: "this
    feature needs a one-click install" is a state, not a failure.
    """

    ENVELOPE = {
        "ok": False,
        "error_code": "hyperframes_not_installed",
        "message": (
            "Motion Graphics isn't installed. "
            "Install it from Settings → Add-ons."
        ),
        "action_url": "/settings#motion-graphics",
    }


class MotionRenderError(MotionGraphicsError):
    """A render was attempted and did not produce a usable MP4."""
