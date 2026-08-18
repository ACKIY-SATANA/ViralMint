# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""AI Compose: the authoring contract, the repair loop, and the rule that a
failed compose must never cost the user the composition they already had.

The model is stubbed throughout. That is the point of these tests — the parts
worth pinning are OUR parts: what the model is told, what happens when it gets
it wrong, and what is left on disk afterwards. Whether a given model writes
good HTML is a property of the model, not of this code.

Note on the prompt tests: they assert that specific rules are PRESENT, which
normally smells like testing a constant. They earn their place because each one
prevents a silent renderer failure rather than a stylistic wobble — a
composition that animates on a timer, or links a font from a CDN, previews
perfectly in a browser and then renders blank. Losing a line from the prompt
would not fail anything else in this suite.
"""
from __future__ import annotations

import pytest

from backend.services import composition_author as author


VALID = ('<!doctype html><html data-composition-variables=\'[]\'><body>'
         '<div id="stage" data-composition-id="scene" data-width="1080" data-height="1920" data-start="0"></div>'
         '<script src="assets/gsap.min.js"></script>'
         '<script>window.__timelines={};</script></body></html>')


class StubAI:
    """An ai_client with the OSS chat() signature. Returns queued replies and
    records what it was asked, so the repair loop can be observed."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, system=None, max_tokens=2048):
        self.calls.append({"user": messages[0]["content"], "system": system,
                           "max_tokens": max_tokens})
        return self.replies.pop(0) if self.replies else VALID


# ── What the model is told ────────────────────────────────────────────────────

@pytest.mark.parametrize("rule", [
    "setTimeout", "requestAnimationFrame", "Math.random",      # wall-clock motion
    "window.__timelines",                                       # registration
    "data-composition-id",                                      # the stage
    "assets/gsap.min.js",                                       # vendored runtime
    "NEVER reference a CDN",                                    # offline render
    "repeat:-1",                                                # infinite tween
])
def test_the_prompt_states_every_silent_render_breaker(rule):
    """Each of these renders blank or aborts while previewing perfectly. They
    are the difference between a composition and a wasted render."""
    assert rule in author.AUTHORING_SYSTEM_PROMPT


def test_the_prompt_carries_the_overlays_it_asks_for_verbatim():
    """It requires grain and vignette on every composition, so it has to hand
    over the actual markup. A rule that says "add grain" and leaves the model to
    invent it produces a different look every time."""
    assert "feTurbulence" in author.AUTHORING_SYSTEM_PROMPT
    assert "radial-gradient(ellipse at center" in author.AUTHORING_SYSTEM_PROMPT
    assert "__GRAIN__" not in author.AUTHORING_SYSTEM_PROMPT, "placeholder never substituted"


def test_the_prompt_forbids_the_flat_default_models_drift_to():
    assert "NEVER ship a flat single-color" in author.AUTHORING_SYSTEM_PROMPT \
        or "NEVER ship a flat single-colour" in author.AUTHORING_SYSTEM_PROMPT


def test_the_brief_carries_real_pixels_not_an_aspect_name():
    """The contract needs data-width/data-height as numbers. Handing over "9:16"
    and hoping makes the model guess a canvas."""
    msg = author.build_brief_message({"aspect_ratio": "16:9", "duration_seconds": 10})
    assert "1920x1080" in msg and "16:9" in msg


def test_an_out_of_range_duration_is_clamped_not_rejected():
    """An older client sending an unsupported length should still get a video."""
    assert "3 seconds" in author.build_brief_message({"duration_seconds": 1})
    long = author.build_brief_message({"duration_seconds": 9999})
    from backend.agents.generator_motion import MOTION_DURATION_MAX
    assert f"{MOTION_DURATION_MAX} seconds" in long


def test_asset_filenames_reach_the_model_untouched():
    """A tidied name is a name that is not on disk, and the export aborts on it.
    The staged name is deliberately ugly (a uniqueness suffix), so the temptation
    to prettify it is real."""
    msg = author.build_brief_message(
        {"topic": "x", "assets": [{"file": "my_clip_a1b2c3.mp4", "type": "video", "duration": 4.0}]})
    assert "my_clip_a1b2c3.mp4" in msg


# ── Getting HTML back out ─────────────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    VALID,
    f"```html\n{VALID}\n```",
    f"```\n{VALID}\n```",
    f"Sure! Here's the composition:\n\n{VALID}",
])
def test_html_survives_however_the_model_wraps_it(reply):
    """The prompt asks for bare HTML and models fence it anyway. Treating a
    fenced reply as a failure would throw away good work."""
    assert author.extract_html(reply) == VALID


async def test_a_reply_with_no_document_fails_with_a_useful_reason():
    """Usually means the configured model is too small. Saying so beats a
    validation error about a missing stage element."""
    with pytest.raises(ValueError, match="too small|did not return"):
        await author.author_composition({"topic": "x"}, StubAI("I'd be happy to help!"))


async def test_authoring_asks_for_enough_room_to_finish():
    """A budget that truncates the document mid-tag fails validation in a way
    that reads as the model's fault."""
    ai = StubAI(VALID)
    await author.author_composition({"topic": "x"}, ai)
    assert ai.calls[0]["max_tokens"] == author.AUTHOR_MAX_TOKENS >= 8000


async def test_a_repair_shows_the_model_its_own_errors_and_its_own_output():
    """"Try again" reproduces the same mistake. Naming the fault, and pairing it
    with what was actually written, is what makes the second pass converge."""
    ai = StubAI(VALID)
    await author.author_composition(
        {"topic": "x"}, ai,
        repair_error="- no window.__timelines registration",
        previous_html="<html>broken</html>")
    sent = ai.calls[0]["user"]
    assert "no window.__timelines registration" in sent
    assert "<html>broken</html>" in sent
    assert "REJECTED" in sent
    # The brief must survive the repair framing, or the retry drifts off-topic.
    assert "TOPIC: x" in sent


async def test_the_contract_is_byte_identical_across_calls():
    """The system prompt is the largest, most repeated part of the request.
    Varying it per call defeats any provider-side caching for no benefit."""
    ai = StubAI(VALID, VALID)
    await author.author_composition({"topic": "a"}, ai)
    await author.author_composition({"topic": "b"}, ai, repair_error="- x")
    assert ai.calls[0]["system"] == ai.calls[1]["system"] == author.AUTHORING_SYSTEM_PROMPT


# ── The job: repair, then refuse ──────────────────────────────────────────────

@pytest.fixture
def compose_job(tmp_path, monkeypatch):
    """A studio project plus a settled-on stub AI, wired into the runner."""
    import backend.services.studio_service as svc
    from backend.services.studio_service import StudioService

    proj = tmp_path / "project"
    (proj / "assets").mkdir(parents=True)
    proj.joinpath("index.html").write_text(
        '<html><div data-composition-id="my-existing-work"></div></html>')
    monkeypatch.setattr(svc, "_project_dir", lambda: proj)
    # _seed re-stages GSAP from the install, which is not present under test.
    monkeypatch.setattr(StudioService, "_seed", classmethod(lambda cls: None))
    # The engine's own `check` needs the plugin; the structural pass does not.
    monkeypatch.setattr(StudioService, "_lint_errors",
                        classmethod(lambda cls, html, assets_dir=None: _empty()))
    return proj


async def _empty():
    return []


async def _run(monkeypatch, ai, job_id="job1234"):
    """Drive run_studio_author with a stubbed model and capture the job's fate."""
    from backend.core import task_runner
    states = []

    async def fake_update(jid, status, **kw):
        states.append({"status": status, **kw})

    monkeypatch.setattr("backend.agents.job_helper.update_job_status", fake_update)
    monkeypatch.setattr("backend.agents.job_helper.job_cancelled",
                        lambda jid: _false())
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda *a, **k: ai)

    class _WS:
        async def send(self, *a, **k): pass
    monkeypatch.setattr("backend.core.ws_manager.ws_manager", _WS())

    await task_runner.run_studio_author(job_id, {"topic": "a hook", "aspect_ratio": "9:16"})
    return states


async def _false():
    return False


async def test_a_good_composition_goes_live_first_time(compose_job, monkeypatch):
    ai = StubAI(VALID)
    states = await _run(monkeypatch, ai)
    assert states[-1]["status"] == "success"
    assert len(ai.calls) == 1, "no repair pass should have been needed"
    assert "data-composition-id=\"scene\"" in compose_job.joinpath("index.html").read_text()


async def test_an_invalid_composition_is_repaired_and_then_goes_live(compose_job, monkeypatch):
    broken = "<!doctype html><html><body><div>nothing here</div></body></html>"
    ai = StubAI(broken, VALID)
    states = await _run(monkeypatch, ai)
    assert states[-1]["status"] == "success"
    assert len(ai.calls) == 2, "the second call is the repair"
    assert "REJECTED" in ai.calls[1]["user"]
    assert "data-composition-id" in compose_job.joinpath("index.html").read_text()


async def test_a_composition_that_stays_broken_never_replaces_working_work(
        compose_job, monkeypatch):
    """The property that matters most here.

    index.html is a single mutable file. Setting an unrenderable composition
    live would cost the user something that worked in exchange for something
    that does not — so a compose that cannot be fixed fails the JOB and leaves
    the disk exactly as it was.
    """
    broken = "<!doctype html><html><body><div>nothing here</div></body></html>"
    ai = StubAI(broken, broken)
    states = await _run(monkeypatch, ai)

    assert states[-1]["status"] == "failed"
    assert "my-existing-work" in compose_job.joinpath("index.html").read_text()
    assert not list(StudioServiceArchive(compose_job)), "nothing should have been archived"


def StudioServiceArchive(proj):
    d = proj / "archive"
    return list(d.glob("comp_*.html")) if d.exists() else []


async def test_the_failure_names_what_was_wrong(compose_job, monkeypatch):
    """"Compose failed" sends someone to the logs. The validator already knows
    which element and which fix — passing that through is free."""
    broken = "<!doctype html><html><body><div>nothing here</div></body></html>"
    states = await _run(monkeypatch, StubAI(broken, broken))
    msg = states[-1].get("error_message", "")
    assert "__timelines" in msg or "data-composition-id" in msg
