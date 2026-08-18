# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""AI authoring for motion-graphics compositions.

Turns a short brief into ONE self-contained HTML file that the HyperFrames
renderer seeks frame by frame into a deterministic MP4. Your own model writes
it — this module supplies the contract it has to satisfy, and the loop that
checks whether it did.

Two ideas carry all of this:

**The contract is not style advice.** A composition that animates with
`setTimeout`, links a font from a CDN, or forgets to register its timeline
looks perfect in a browser and then renders blank or aborts. The renderer never
plays the composition — it seeks to a frame and screenshots it — so anything
driven by wall-clock time simply is not there when the picture is taken. Those
rules are stated as hard requirements because every one of them is a silent
failure, not a rough edge.

**A rejected composition is fed its own errors.** Authoring is one call, and
models get details wrong. Rather than accept a broken file or make the user
re-ask, a failed validation is handed back verbatim for one more attempt. That
is cheaper than a person debugging generated HTML, and it converges: the
checker names the element and the fix.

Deliberately NOT here, and not missing: multi-stage planning, a block catalog,
a curated style catalog, vision QA of rendered frames. Those are quality
amplifiers on top of a working composer, and this is the working composer.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Room for a real composition. The output is a whole HTML document, and the
# most common failure of a tight budget is a file truncated mid-tag — which
# fails validation in a way that looks like the model's fault.
AUTHOR_MAX_TOKENS = 8000

# One retry, fed the validator's own findings. A second retry rarely converges:
# if the model could not satisfy a named, quoted error once, it is usually
# disagreeing with the contract rather than mistyping it.
MAX_REPAIR_PASSES = 1

_GRAIN = '<div id="grain" style="position:absolute;inset:0;pointer-events:none;z-index:100;opacity:.10;mix-blend-mode:overlay;background-image:url(\'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22120%22 height=%22120%22><filter id=%22n%22><feTurbulence type=%22fractalNoise%22 baseFrequency=%220.9%22 numOctaves=%222%22/></filter><rect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23n)%22/></svg>\');background-size:240px 240px;"></div>'
_VIGNETTE = '<div id="vignette" style="position:absolute;inset:0;pointer-events:none;z-index:90;background:radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,.55) 100%);"></div>'

AUTHORING_SYSTEM_PROMPT = """\
You are an award-winning motion-graphics designer who writes HyperFrames video
compositions: ONE self-contained HTML file that a headless-Chrome renderer turns
into a deterministic MP4. You write production HTML/CSS/JS — not prose.

OUTPUT: Return ONLY the complete HTML document, starting with <!doctype html>.
No explanations, no markdown code fences. Keep the file TIGHT — target under
~250 lines / 10 KB: fewer, better-crafted elements beat sprawling markup, and
an overlong file gets cut off in transport and breaks entirely.

═══ HARD TECHNICAL CONTRACT (any violation breaks the render) ═══
1. Declare user-editable variables on the <html> root:
   <html data-composition-variables='[{"id":"x","type":"string","label":"X","default":"..."}]'>
   Types allowed: string | number | color | boolean | enum. Read them with:
   const v = window.__hyperframes.getVariables();
   Bind an element's text to a string variable with data-var-text="<id>" so
   studio edits of that variable update the composition live.
2. Root stage: <div id="stage" data-composition-id="scene" data-width="{W}"
   data-height="{H}" data-start="0">  — NEVER put data-duration on it.
3. GSAP only, and it is already vendored into the project:
   <script src="assets/gsap.min.js"></script>  (global `gsap`).
   NEVER reference a CDN or any external URL. System fonts only (Helvetica
   Neue, Arial Black, Georgia, Impact, Times) — no web fonts. CJK text is fully
   supported: write it directly in your chosen Latin family, and never add
   @font-face or a font link for it.
4. ALL motion goes through ONE paused timeline:
   const tl = gsap.timeline({paused:true});  ...build it...
   window.__timelines = window.__timelines || {}; window.__timelines["scene"] = tl;
   (the key MUST equal data-composition-id.)
5. The composition's duration IS the timeline's duration. Read the injected
   hold length and end the timeline with it:
   const DUR = Number(v._vm_duration) || <the brief's duration>;
   tl.set({}, {}, DUR);
6. DETERMINISTIC AND SEEKABLE — this is the rule that silently ruins renders.
   The renderer SEEKS to each frame and screenshots it; it never plays the
   piece. Anything driven by wall-clock time is simply not there when the
   picture is taken. FORBIDDEN at runtime: setTimeout, setInterval,
   requestAnimationFrame, Date.now(), performance.now(), Math.random(), and CSS
   @keyframes / transition on any animated element. Drive EVERYTHING with GSAP.
   Timeline construction must be SYNCHRONOUS — no async / await / fetch /
   promises anywhere; the timeline must exist complete at parse time.
7. Fully self-contained: all CSS inline in one <style>.

═══ PROVIDED ASSETS (only when the brief lists ASSETS) ═══
Real footage and imagery are the biggest quality lever — USE every asset listed.
Copy each filename from the brief's ASSETS list CHARACTER FOR CHARACTER, however
awkward it looks. Never tidy it, shorten it, or invent a friendlier one
(`hero.mp4`): a name that is not on that list does not exist on disk, and the
export ABORTS. EVERY media element (video/audio/img) MUST have a unique `id` —
the runtime keys media on it, and a missing id aborts the render.
- video → a FULL-BLEED BACKGROUND:
  <video id="bg" src="assets/FILE" data-start="0" data-duration="DUR" data-track-index="0" muted playsinline
   style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;"></video>
  NEVER put class="clip" on <video> (the framework manages it), and NEVER tween
  a <video> element itself — animating its size or position stalls frame
  capture. Wrap it in a <div> and animate the WRAPPER.
  Its data-duration IS THE WHOLE COMPOSITION. A background runs UNDER the piece;
  when it ends the element is gone and the frame goes BLACK. THE FRAME IS NEVER
  EMPTY: at every second some layer is carrying the piece. A stretch of black
  with a few glows is not a beat, it is a hole.
  When footage is the background, do not paint your own gradient over it — add
  a subtle dark scrim so text stays legible and design the type to sit OVER it.
- audio → <audio id="track" src="assets/FILE" data-start="0" data-duration="DUR" data-track-index="2" data-volume="0.3"></audio>
- image → an <img id="..." src="assets/FILE"> layer (object-fit:cover for
  full-bleed). An <img> given its own data-start/data-duration ALSO needs
  class="clip" — a timed non-media element without it aborts the render.

═══ VISUAL QUALITY BAR — the entire point ═══
NEVER ship a flat single-colour background with plain centred text. That is what
amateur output looks like, and it is the default a model drifts to. Every
composition should read like a premium short-form motion piece or a keynote
card. Required:
- RICH BACKGROUND: layered radial + linear gradients or mesh-gradient blobs with
  depth; optionally drift them slowly via GSAP. Dark, moody or vivid — never flat.
- CINEMATIC OVERLAYS (ALWAYS include BOTH, as direct children of #stage):
  GRAIN:    __GRAIN__
  VIGNETTE: __VIGNETTE__
- BOLD TYPE HIERARCHY: oversized weights (800–900), tight leading, an accent
  colour on the key word, generous scale contrast.
- COHESIVE COLOUR: 3–4 swatches built from the accent — accent, one tint, one
  shade, a near-black ground. No raw primaries, no clashing hues.
- TYPOGRAPHY: pair a heavy display weight with a lighter support weight; roughly
  3–4x scale contrast; tight leading and about -0.02em tracking on the big type.

═══ MOTION VOCABULARY (implement with this render-safe set) ═══
Entries:  slide_bottom/top/left/right  gsap.from({y:±150 or x:±200, opacity:0, ease:"power4.out"})
          scale_punch    from({scale:.6, opacity:0, ease:"back.out(2.2)"}) — impact
          scale_grow     from({scale:0, opacity:0, ease:"power2.out"}) — gentle
          word_reveal    per-word from({opacity:0, y:40}, stagger:.08–.15) — storytelling
          wave           per-letter from({y:30}, stagger:{each:.04}) — flowing
          slam           from({y:-300, ease:"power4.out"}) + a 3-tween x-shake on the parent
          mask_reveal    overflow:hidden wrapper + inner fromTo({yPercent:115},{yPercent:0})
                         — the premium text entrance
Emphasis: scale_pulse to({scale:1.1, yoyo:true, repeat:1, ease:"sine.inOut"}) ·
          shake (keyframed x: [-9, 9, 0]) · punch (to({scale:1.05}) and back)
Exits:    fade_out · slide_out (y/x off + fade) · scale_out to({scale:1.06, opacity:0}) ·
          mask exit (inner yPercent:-115)
Accents:  underline_sweep fromTo({scaleX:0},{scaleX:1}, transformOrigin:"left center") ·
          shimmer (a gradient band swept via xPercent) · color_shift to({color:"<accent>"})
Mood → ease: smooth=sine/power1 · snappy=power4.out · bouncy=back.out ·
dramatic=expo.out · dreamy=sine.inOut. Timing: fast≈0.2s, medium≈0.4s,
slow≈0.6s, luxurious≈1s+.
Every element follows enter → (emphasis) → settle → exit. Background and decor
drift slower than the foreground, which is what reads as depth. Vary the
primitives — repetition reads as template output.
PERFORMANCE: at most 2 backdrop-filter (glass blur) layers per region, blur
radii ≤ 40px. Heavy stacks stall the renderer.

═══ LAYOUT CONTRACT ═══
- Respect safe zones: keep text within x 6–94%, and put NOTHING below y=85% —
  platform UI covers it. In vertical formats the hook and the key claim sit
  centre or upper-centre; the lower half is for CTA, proof and lower-thirds.
- Give each beat ONE focal element, with supporting elements on their own cues.
  A frame where everything arrives at once is a poster, not a scene.
- Nothing ever fully stops. A frozen final second is the clearest tell of cheap
  motion — let the last beat settle into a slow drift rather than a hard stop.
- 1.5–4 seconds per idea. Fewer ideas, better executed.

═══ THINGS THAT LOOK FINE AND BREAK ═══
- ONLY GSAP is available. No Lottie / Three.js / Anime.js — their runtimes are
  not bundled and the render is offline.
- NEVER use repeat:-1 (an infinite tween has no finite duration). Loop a finite
  number of cycles inside DUR instead.
- Animated number count-ups: tween a proxy and write the DOM in onUpdate, which
  fires during the deterministic seek, so it renders:
  const c={v:0}; tl.to(c,{v:384,duration:1.2,onUpdate:()=>el.textContent=Math.round(c.v)}, 0.4);
- A fromTo whose destination omits opacity:1 can render INVISIBLE even though it
  previews correctly.

You receive a brief (topic, style, accent, dimensions, duration). Design a
composition that fits the topic and looks premium. Think about layout, rhythm
and hierarchy like a real designer. Return the HTML.
""".replace("__GRAIN__", _GRAIN).replace("__VIGNETTE__", _VIGNETTE)


def build_brief_message(brief: dict) -> str:
    """The user-side half of the request: what to make, at what size.

    Kept small and literal. Everything the model must NOT do lives in the
    system prompt, which stays byte-identical across calls.
    """
    from backend.agents.generator_motion import ASPECT_DIMS, MOTION_DURATION_MAX

    aspect = brief.get("aspect_ratio") or "9:16"
    w, h = ASPECT_DIMS.get(aspect, ASPECT_DIMS["9:16"])
    duration = brief.get("duration_seconds") or 6
    duration = max(3, min(int(duration), MOTION_DURATION_MAX))

    lines = [
        f"TOPIC: {(brief.get('topic') or '').strip() or 'a short brand moment'}",
        f"DIMENSIONS: {w}x{h} ({aspect})",
        f"DURATION: {duration} seconds",
        f"ACCENT COLOUR: {brief.get('accent') or '#ffd60a'}",
    ]
    if brief.get("style"):
        lines.append(f"STYLE: {brief['style']}")
    if brief.get("headline"):
        lines.append(f"HEADLINE (use this text verbatim): {brief['headline']}")
    if brief.get("instruction"):
        # A refinement of an existing composition rather than a fresh one.
        lines.append(f"CHANGE REQUESTED: {brief['instruction']}")
    assets = brief.get("assets") or []
    if assets:
        lines.append("ASSETS (use every one; copy the filename exactly):")
        for a in assets:
            desc = f"  - {a.get('file')} ({a.get('type')}"
            if a.get("duration"):
                desc += f", {a['duration']}s"
            lines.append(desc + ")")
    return "\n".join(lines)


_FENCE_RE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.I)


def extract_html(reply: str) -> str:
    """Pull the HTML document out of a model reply.

    The prompt asks for bare HTML, and models wrap it in a code fence anyway —
    often enough that treating a fenced reply as a failure would reject good
    work. Anything before <!doctype or <html is preamble and is dropped.
    """
    text = (reply or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    lowered = text.lower()
    # Slice to the EARLIEST marker present, not the first one we happen to look
    # for. A well-formed document contains both, with <!doctype at 0 and <html
    # just after it — so "the first marker found at a non-zero offset" picks
    # <html> and silently strips the doctype, which is what keeps the renderer
    # out of quirks mode. Take the minimum over the markers that exist at all,
    # and only cut when there is genuinely preamble in front of it.
    starts = [i for i in (lowered.find("<!doctype"), lowered.find("<html")) if i >= 0]
    if starts and min(starts) > 0:
        text = text[min(starts):]
    return text.strip()


async def author_composition(brief: dict, ai_client, *, repair_error: str = None,
                             previous_html: str = None) -> str:
    """One authoring call → the composition HTML.

    `repair_error` and `previous_html` drive the retry: the model is shown what
    it wrote and exactly what was wrong with it. Naming the fault beats asking
    again, because "try harder" reproduces the same mistake.
    """
    user = build_brief_message(brief)
    if repair_error:
        user = (
            "Your previous composition was REJECTED by the renderer's validator.\n"
            f"Problems found:\n{repair_error}\n\n"
            "Fix every one of them and return the COMPLETE corrected HTML "
            "document. Do not explain, do not return a diff.\n\n"
            f"Your previous attempt:\n{previous_html or '(not available)'}\n\n"
            f"The original brief:\n{user}"
        )
    reply = await ai_client.chat(
        messages=[{"role": "user", "content": user}],
        system=AUTHORING_SYSTEM_PROMPT,
        max_tokens=AUTHOR_MAX_TOKENS,
    )
    html = extract_html(reply)
    if not html or "<html" not in html.lower():
        raise ValueError(
            "The model did not return an HTML document. This usually means the "
            "configured model is too small for the task — try a stronger one."
        )
    return html
