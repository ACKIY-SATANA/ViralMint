# Changelog

All notable changes to ViralMint will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Batch download refuses non-http links.** `POST /api/downloaded/batch-download`
  accepted any string as a URL and handed it to yt-dlp, so a `file:///etc/passwd`
  entry started a job. Every URL must now be a full `http(s)` link, checked at
  the endpoint itself rather than at one of its callers.
- **Subtitle language codes are shape-checked.** yt-dlp treats the subtitle
  language list as regexes, so a `.*` would pull every track a video has — the
  same fan-out the explicit "all" rejection exists to prevent. Entries must now
  look like real language codes.
- **SPA path-traversal fix.** The frontend catch-all route served any file
  resolved under the `dist` directory without a containment check, so a
  `../`-laden request could read files outside the built bundle. The handler
  now confirms the resolved path stays inside the bundle before serving.
- **CSRF origin check.** Non-safe-method requests must now carry an
  allowlisted `Origin`/`Referer` (no-Origin CLI/non-browser calls still pass),
  hardening the loopback surface against a malicious page POSTing to
  `127.0.0.1:16888`. Skipped when you opt into `HOST=0.0.0.0` LAN mode.
- **Encryption-key validation.** A placeholder or malformed `ENCRYPTION_KEY`
  used to slip through and crash every encrypt/decrypt at first use; it's now
  validated (and regenerated if invalid) at startup.

### Changed
- **Chat replies render smoothly.** Every streamed token wrote to the store and
  re-parsed the whole partial reply, so long answers got progressively jankier.
  Tokens are now batched to at most one update per frame — same output, a
  fraction of the work.
- **Background job polling backs off.** The jobs list was re-fetched every 5s
  forever, including in hidden tabs. It now pauses while the tab is hidden,
  slows to 30s when nothing is running, skips redundant re-renders when the
  payload hasn't changed, and catches up immediately when you return to the tab.
- **Faster startup.** Every boot fired an `ALTER TABLE … ADD COLUMN` for each
  migrated column and swallowed the resulting "duplicate column" error — a pile
  of throwaway failed statements on an already-current database. Startup now
  reads each table's columns once and skips those ALTERs entirely.

### Added
- **Motion Graphics — render designed video, locally.** A new kind of output
  alongside stock footage and clips: motion-graphics pieces built out of type,
  shapes and animation rather than filmed footage. Kinetic typography hooks,
  stat cards, lower thirds — the sort of thing that normally means opening After
  Effects.

  It renders entirely on your machine. There is no model in the loop, so the
  same inputs give the same video every time, and you can render it a hundred
  times without spending anything.

  The engine is an optional add-on rather than part of the download. Open
  Settings → Add-ons and install it once: ViralMint fetches a portable Node
  runtime and the HyperFrames engine into your data folder, verifies the
  archive against the checksums nodejs.org publishes, and then renders a real
  video before it will call the install good — so a broken setup fails at
  install time instead of the first time you need it. "Remove" puts the disk
  back. A "Test render" button re-proves the whole chain whenever you want it.

  Because compositions are GSAP-driven and GSAP ships under a licence that is
  not free software, it is not committed to this repository. It is installed
  alongside the engine on your machine and copied into each render instead.

- **The Motion Graphics studio.** A new page in the sidebar embeds a full
  compositing studio: timeline, live preview, layers and variables, an asset
  library and Export. Design a piece, scrub it, adjust it, render it.

  It runs on your machine and is served from the app's own address, so it keeps
  its state between visits, and it is re-skinned to match whichever theme you
  are using.

  Exports land in your Library on their own — the page notices new renders
  while it is open and imports them, so a finished piece is in the same place
  as everything else the app makes, ready to caption, reframe or export like
  any other video. Every composition you replace is archived rather than
  overwritten, and a Comps list lets you clear the ones you no longer want.

- **Video Download.** A new tool page for the thing the app could always do but
  never let you choose about: paste up to 20 links and pull the videos down
  from YouTube, TikTok, Bilibili, X and the 1,800+ other sites yt-dlp supports.
  Pick a maximum resolution, keep subtitles as a separate `.srt` or embed them
  as a selectable track, choose MP4 or MKV, and optionally embed cover art,
  tags and chapter markers. Every option degrades instead of failing — a source
  with nothing at the size you asked for gives you the closest available rather
  than an error, and a container that the codecs can't stream-copy into comes
  out as MKV instead of a failed merge. The result then tells you what you
  actually got: the delivered resolution and container per video, which
  subtitle files were kept, and whether the embed extras had to be dropped.
  Unlike the other download paths this one skips transcription — it was asked
  for the files, and Library can analyze on demand.
- **Compress Video.** A new tool for getting a file under an email, chat or
  upload limit. Two independent dials — a target resolution and how hard to
  squeeze at that size — with the output dimensions stated before you run it.
  Asking for a resolution larger than the source keeps the source size instead
  of upscaling, and a file that comes out bigger than it went in says so rather
  than being handed back silently. Local FFmpeg; nothing leaves your machine.
- **Crop Video.** Drag a box over the frame and keep just that part — the
  manual counterpart to Reframe, which picks the framing for you. Free-form or
  snapped to 9:16, 1:1 or 16:9, with the exact pixel crop shown as you drag.
  The audio track is copied across untouched.
- **Remove Audio.** Strip a video's sound entirely, as its own operation on the
  Transform tool and its own card in the Tools hub. This is not the old "Mute"
  volume preset, which re-encoded the audio to silence and left the (silent)
  track in the file; this removes the track. A video that never had audio still
  gets its file back, with a note explaining why nothing appears to have
  changed.
- **A live preview of the file you're about to process.** Selecting a video on
  any tool page now mounts the actual video instead of showing a filename and a
  byte count, and the tool's own controls can read its real size — which is
  what makes the crop box possible.
- **A job can no longer report success on a broken artifact.** Every
  file-producing tool now passes its output through one gate before the job is
  marked successful: the file must exist, be non-empty, be readable by ffprobe,
  carry the kind of stream its own extension promises, and have a duration.
  Tools that know more assert more — reframe now declares that its output must
  be portrait, which is the class of failure an exit code cannot see. Broken
  output fails the job with a sentence naming what's wrong instead of handing
  you a download that doesn't play.

### Fixed
- **Deleting a downloaded video sweeps its subtitle files.** Subtitles kept
  alongside a video have no database record of their own, so they used to
  survive the row that owned them and accumulate on disk forever. Delete and
  the stale-record cleanup both sweep them now.
- **Sound effects no longer make the voice ramp up in volume.** The mixer let
  FFmpeg average the tracks instead of summing them, and every effect counted
  as "playing" from the very start of the video — so the narration began
  roughly ten times too quiet and grew louder as each effect finished.
  Measured on a 20-second clip with 8 effects: it climbed 16.6 dB from start to
  end. It now holds a steady level, with a limiter so the mix can't clip.
- **The "heavy" sound-effect style stopped dropping a third of its effects.**
  It planned up to 25 and the mixer quietly stopped at 15, while still
  reporting the planned number back to you.
- **A news article that only has a headline is now scored instead of
  discarded.** Google News links go through a redirector whose target often
  can't be fetched, leaving a title and no body — which the analyzer treated as
  an empty article and dropped. On some searches that was most of the results.
- **Chat messages no longer arrive twice.** Under some mounting orders the app
  opened a second WebSocket without closing the first, and both delivered every
  server event to the same handlers — duplicate replies, doubled streaming
  text, duplicate job cards.
- **Progress cards stopped showing internal job names.** A tool job announced
  itself as "tool:gif complete"; it now reads "Gif complete".
- **Merging a silent clip no longer throws away the other clips' audio.**
  Merge Clips normalizes every input before stitching, but it did not normalize
  the stream layout — a clip with no audio came out video-only, and the
  stitcher takes its layout from the first file. A silent intro card in front
  of a talking video therefore produced a merge with no sound at all, reported
  as a success. Every clip now reaches the stitch with an audio track, silent
  or not.
- **The app survives browser page-translation.** Chrome, Edge, Safari and the
  Baidu/QQ equivalents rewrap text into elements React is still tracking, and
  React's next update then crashed the whole interface to an error screen —
  most easily in Chat, where streaming text changes constantly. Since the page
  declares itself as English, this was offered to every non-English speaker on
  first load. It no longer crashes. (This stops the crash; it does not
  translate the app.)
- **Captions stop being burned under the platform's own UI.** Caption margins
  were pixel offsets tuned for a 1920-tall frame and nothing ever checked them
  against the interface TikTok/Reels/Shorts draw *on top of* the video. Three
  styles (Classic, Minimal, Karaoke) sat inside the band covered by the
  username, caption and music ticker — unreadable, and impossible to fix after
  export. A platform safe zone is now applied as a **floor**: styles that
  already clear the chrome are untouched, only the unsafe ones move, and the
  insets are fractions of the frame so they hold on a square, a 4:5 or a 4K
  render. The hook overlay gets the same treatment against the top bar.
- **Square videos got vertical caption geometry.** The tool runners' aspect
  probe was binary ("is it landscape?"), so a 1080x1080 file was treated as
  9:16 — wrong margins, wrong font size, and the vertical chrome inset a
  square feed post doesn't need.
- **Imported and translated subtitle cues no longer merge or drift.** Cues
  written back-to-back rendered as one long caption line showing text long
  before its own cue time, and overlapping cues (rolling auto-captions overlap
  by construction) pushed the timeline further out with every cue — a 200-cue
  import produced a caption track roughly twice the video's length, so the
  back half never rendered and everything before it desynced.
- **The caption style list is served from the render engine.** `/api/captions/
  styles` was a hand-maintained copy and had drifted: 7 of 10 styles, with four
  of them advertising a centre alignment the renderer never produces. Custom
  and AI-generated styles are also forced onto a bottom alignment, without
  which the safe-zone floor silently does nothing.
- **Cancelling a clip extraction actually cancels it.** Cancel only flipped the
  database row; the pipeline kept burning Whisper, the AI call and N parallel
  ffmpeg re-encodes, saved every clip, and overwrote "cancelled" with
  "success". It now stops at the next phase boundary, deletes the clips it had
  already produced, and stays cancelled.
- **Clip files orphaned by a crash are swept.** Clips are written straight into
  the generated-media directory under their final names, so a backend killed
  mid-run leaked them forever. A boot-time sweep reclaims `clip_*` files older
  than 24h that no library row references — including, deliberately, cached
  16:9 exports, which are themselves `clip_*` files referenced by a different
  column.
- **A manual cut with captions off no longer transcribes the whole source.**
  Hand-picked ranges need no transcript unless captions or silence-removal
  consume it, but Whisper ran anyway — on the first cut of a newly imported
  video that meant minutes of work to produce a seven-second trim.
- **"Remove silence & filler words" works for hand-picked ranges.** The option
  was gated out of manual mode on the theory that it would shift the user's
  chosen timing. It doesn't: silence is removed *inside* each already-cut clip,
  so the picked boundaries are untouched. The gate only made hand-picked clips
  the one place pacing couldn't be tightened.
- **Audio enhancement stopped degrading already-clean audio.** Single-pass
  loudness normalisation runs a different algorithm that rides the gain
  continuously — audible pumping, and it crushed loudness range a little more
  on every pass. Enhancement is now measure-then-apply with a single fixed
  gain, and audio already at target is returned untouched instead of being
  re-processed.
- **Background music you can actually hear.** The music bed sat 20dB down,
  which measured as a 0.1dB change to the finished mix — reported as "no
  background music", because there effectively wasn't any.
- **Silent substitutions now announce themselves.** Picking a music genre with
  no matching track quietly used any track at all, and a voice provider
  without a key quietly narrated in a different voice. Both now say so.
- **Aspect conversion stops re-encoding audio.** Audio is untouched by a
  geometry change, but every reframe/export paid a lossy generation for it,
  and chained tools stacked them.
- **Downloads use whatever JavaScript runtime the machine has.** yt-dlp was
  handed a hardcoded `node`, and passing that option *replaces* yt-dlp's own
  default — so a machine with deno but not Node ended up with no runtime at
  all, and a machine with neither got no warning. Without a runtime YouTube's
  n-signature challenges go unsolved and formats go missing or 403. Node and
  deno are now discovered, and a machine with neither gets one clear warning
  naming the consequence.
- **An HTTP 403 caused by cookies is now retried without them.** Supplying any
  cookie makes yt-dlp skip every player client that can't carry one — which are
  exactly the token-free ones — so a 403 was retried with the same losing
  configuration until the attempts ran out.
- **curl-cffi floor raised to 0.15.** 0.14 resets a libcurl handle from a
  done-callback while the calling thread is still reading it, which aborts the
  whole backend process — no traceback, just a dead app. TikTok forces
  impersonation, so it was reachable from every TikTok probe and download.
- **A translation that fails on every line is an error, not a silent
  passthrough.** Degrading a few captions to their source text is the point of
  the retry ladder; degrading all of them would burn a full render to hand back
  the original captions under a "translated" label.
- **Translating a long video no longer dies on one bad batch.** Translation
  sent every segment in a single AI call and enforced a strict 1:1 count by
  raising, so a long video overflowed the token budget and a model that merged
  two lines threw away the Whisper pass and everything already translated.
  Segments now go in batches of 20, a batch that comes back the wrong length is
  split and retried in halves down to single lines, and a line that fails even
  alone keeps its source text instead of sliding every later caption off its
  timestamp.
- **Burning captions onto a long video no longer times out at the last step.**
  The burn is a full re-encode on what was a flat 10-minute cap, so a 17-minute
  video failed after transcription (and, in the Translate tool, translation)
  had already run. The budget now scales with the source.
- **Metadata and Auto Chapters show their result inline again.** Both previews
  read a field the store never wrote, so both always fell through to "click
  Download instead" — the copy-to-clipboard preview, which is the whole point
  of a titles/tags/chapters tool, was unreachable code. They now read the job's
  own output, which also means the preview survives a page reload.
- **The Captions tool offers every caption style.** The renderer has ten; the
  endpoint validated three and the picker listed three, so neon, karaoke, glow,
  Bold Urban, Warm Glow, Monochrome and Minimal were unreachable from the tool
  built to apply them (and posting one came back 422). The accepted list is now
  derived from the engine, with a test pinning the API, both pickers and the
  Smart Video config list to it.
- **Reframe's description matches what it does.** The page advertised
  MediaPipe face-tracking with a center-crop fallback; the tool is a blur-fill
  fit and this build ships no face detection at all.
- **The Audio tools accept audio.** Enhance Audio and Silence Remover validated
  uploads against the video extensions only, so a podcast mp3 was rejected with
  a 400 *after* the upload — on the two tools whose whole job is the audio
  track. Both now take video or mp3/wav/m4a/aac/ogg/flac, and audio in means
  mp3 out.
- **Enhance Audio works on WebM, and never reports success over an empty file.**
  `-c:v copy` into an .mp4 is invalid for VP8/VP9 — and .webm is what most
  screen recorders export — so the job failed with a raw ffmpeg codec-tag
  error. It now retries with an H.264 re-encode, refuses to finish over a
  missing/0-byte output, and says plainly when a file has no audio track
  instead of surfacing a filtergraph error.
- **Silence removal is budgeted against the source length.** The ffmpeg pass
  re-encodes the whole timeline but carried a flat cap sized for 30-second
  clipper clips, so a long recording died on a raw `TimeoutExpired` with the
  entire command line in the message. The budget now scales with the source
  (measured, not "where the last word ends"), with a floor, a 30-minute
  ceiling, and a readable message if it is ever hit.
- **The Library no longer returns a short (or empty) page after self-healing.**
  The prune that removes rows whose file is gone ran inside the fetched page
  and returned what was left, so a library with enough stale rows could answer
  "no videos" next to a total of several hundred. It now re-queries after
  committing the deletes, so you get a full page.
- **Clip Studio asks the backend for clips.** It pulled an unfiltered page of
  100 videos and filtered in the browser, so enough recent non-clip rows
  rendered "No clips yet" over a library full of clips. `GET /api/videos` takes
  a `source_type` filter now.
- **A video's niche is a keyword again, not a paragraph.** The generator stored
  the analyzer's `topic_angle` — prose by design — in the niche column. It now
  resolves a real short niche (the source's own, else the scout query that
  found it) or stores nothing.
- **Transcription shows live progress instead of looking hung.** Whisper's
  segment generator was consumed in one gulp, so a 40-minute audio sat at a
  frozen 7% / 15% for the entire 15–30 minute transcription — indistinguishable
  from a hang, and reported as one. Analyze and Clip Studio now show a real
  percentage as the decode advances (the analyzer writes it to the DB too, so
  the polled jobs list moves as well as the live socket). Two *long*
  transcriptions also now queue explicitly instead of thrashing the machine
  invisibly inside ctranslate2; short ones still run concurrently, so a caption
  pass never waits behind a batch job.
- **Caption style "None" really means no captions.** Clip Studio's "none" chip
  (which even greys out AutoEmoji when you pick it) was never treated as a
  sentinel backend-side: the ASS builder falls back to the "viral" preset for
  any style name it doesn't recognise, so every "no captions" extraction came
  back with fully burned-in yellow word-by-word subtitles. "None" now skips the
  burn entirely — including the hook overlay, which rides the same file.
- **Extracted clips are labelled with their real shape and length.** Every clip
  row was written as 9:16 with the requested window's duration. Extraction only
  reframes a *landscape* source, so square and 4:5 sources kept their own shape
  and were mislabelled — the Library sizes each tile from that column — and
  "Remove silence" cuts content out, so the stored duration overstated the file.
  Both are now probed from the finished clip, which also stops the thumbnail
  timestamp landing past the end of a heavily-trimmed clip.
- **AI metadata can no longer overwrite a clip's own fields.** The model's
  metadata was merged into the clip record last and unfiltered, so any key it
  invented won — including `video_path`, the file we persist and serve. Only
  the four requested fields are kept now.
- **Exporting a vertical video to 16:9 no longer shrinks the picture.** The
  export hardcoded the blur-fill look, which FITS the whole source inside the
  target frame. Going the other way (16:9 → 9:16) that is exactly right and it
  is how every short is built — but widening a 9:16 short left the content as a
  narrow strip with blur either side, and because ViralMint shorts are
  themselves blur-fill composites the export nested a second box and the
  picture landed at about a third of the frame. Exports now default to `auto`:
  crop when widening, blur-fill when narrowing. An explicit `method` still wins.
- **Reframe to Vertical works on square and 4:5 sources.** The "already
  vertical" short-circuit tested `width <= height`, so a 1080x1080 or 1080x1350
  clip came back byte-identical with an "already vertical" notice instead of
  being cropped to 9:16. Only 9:16-or-narrower has nothing to crop.
- **Removed a dead duplicate export route.** A second
  `POST /api/videos/{id}/export` handler had been shadowed by the first since
  the day it was added; its one useful behaviour (caching the 16:9 render for
  later streaming) now lives in the live handler.
- **TikTok channels show their full video list.** The My Channels grid capped
  TikTok at 20 videos while the YouTube side fetched 200. Both are 200 now; the
  TikTok scrape is a single request either way.
- **Clip Studio: two videos no longer play at once.** Opening the source-video
  preview while a clip was playing left both running and both audible.
- **Best-posting-time recommendation no longer crashes.** As soon as you had
  upload history the endpoint raised a `TypeError` — it rounded a list of view
  counts instead of the per-day average it had already computed — so the whole
  feature was unreachable on its success path.
- **Subtitles tool no longer loses your job when you navigate away.** The
  Subtitles page tagged its job `tool:subtitles` while the backend creates
  `tool:subtitle_export`, so leaving the page mid-run and coming back showed the
  empty upload state as if the file had never been submitted. The endpoint →
  job-type mapping now lives in one place, checked against the real backend
  handlers by a test.
- **Whisper quality setting is actually used.** Picking "accurate" or "best"
  loaded that model and then immediately threw it away and re-loaded "small",
  so analysis silently ran at the default quality while paying for two model
  loads. Transcription quality is now passed explicitly at every call site.
- **No more duplicate Whisper model downloads.** The "is this model already
  downloaded?" check hardcoded `~/.cache/huggingface` and ignored `HF_HOME`, so
  on any setup that relocates the cache an already-present model looked missing
  and a second full copy (up to ~3 GB) was fetched.
- **Heavy Whisper models no longer pin memory forever.** `medium`/`large-v3`
  stayed resident for the life of the process — ~3 GB of RSS held by an app that
  otherwise sits idle in the tray. They're now evicted after 10 minutes unused.
- **Restart no longer fails a job that is still running.** A tray Restart (or
  any port takeover) booted a second backend whose startup sweep marked every
  in-flight job "Server restarted — job did not complete" — but uvicorn frees
  its port at the *start* of graceful shutdown, so the old process was often
  still draining and went on to finish the job. You saw a failure toast for a
  video that actually landed in the Library, and a retry redid all the work.
  Jobs now carry a heartbeat (`jobs.updated_at`, refreshed by every progress
  tick); the boot sweep fails only jobs whose heartbeat has gone stale, and a
  background watcher re-checks the survivors until they finish or go stale. The
  launcher also waits for the old process to actually exit, not just to release
  the port. A late progress tick can no longer resurrect a finished job.
- **Job progress survives a page refresh.** Progress steps were broadcast over
  the WebSocket but never written to the jobs table, so reloading mid-job (or a
  WebSocket reconnect) showed the stale "Loading source data..." baseline for
  the rest of the run.
- **Scout hardening ported from the hosted variant.** Fixes a timezone crash
  in virality scoring (tz-aware feed dates), makes outlier enrichment
  non-fatal with an `author_url` None-guard, shows every scouted result on a
  repeat scout (not just net-new rows), adds a 60s ceiling + extract fallback
  to the yt-dlp search path, retries empty news-RSS passes, caps/de-dupes the
  platform list, and surfaces the cross-post fallback as a constraint warning.
- **Static-asset caching + upgrade refresh.** Content-hashed assets are served
  `immutable` (no revalidation) while `index.html` is `no-cache`, so normal
  loads are fast and an app upgrade refreshes on first reload.
- **Schema-drift warning.** Startup now logs a loud warning if a model column
  is missing from the live DB, catching a forgotten migration early.
- **`VIRALMINT_DATA_DIR`.** The DB, storage, and `.env` location now honor
  `VIRALMINT_DATA_DIR` (falling back to the working directory when unset).

### Added
- **Clip Studio — structured scoring + control knobs ported from the hosted variant.**
  Clips now get a hook score + hook type and a flow/value/trend/shareability
  score breakdown (new `clip_hook_score` / `clip_hook_type` /
  `clip_score_breakdown_json` columns, auto-migrated). The extract dialog gains
  a free-form "describe the clips you want" query, target-platform and genre
  bias, an emoji-style control, a remove-silence toggle, and a manual mode for
  extracting explicit time ranges. Extraction options are consolidated into a
  single `ExtractOptions` object; each clip gets a descriptive title and an
  optional on-screen hook overlay.
- **Chat — rich cards now persist across reloads.** The backend became the
  single writer of rich cards (scout results, channel analysis, …) and
  job-complete rows at WS-emit time, so they survive a page reload and are
  saved even when a job finishes with no tab open (previously they were
  in-memory only and lost).
- **Chat — quick-reply chips** and a composer-lock fix: when the assistant asks
  a follow-up question (e.g. "which platform?"), the input no longer stays
  locked, and suggested answers render as clickable chips.
- **Clip Studio — selection-quality improvements ported from the hosted variant.**
  Sentence-snap (clips no longer cut mid-word), silent-gap backfill, topic
  dedup (drops re-told stories), a short-video fast-path (sources under 20s
  emit the whole clip; the blanket <30s reject is gone), and batched clip
  metadata (one AI call for N clips instead of N). No-speech sources now yield
  duration-based clips instead of erroring.
- **Captions — CJK homophone correction.** When the narration script is
  CJK-dominant, the burned captions now use the true script text (keeping
  Whisper's timings) instead of ASR homophone substitutions. Fail-open for
  non-CJK content.

### Fixed
- **Clip Studio — extraction hardening ported from the hosted variant (7 bugfixes).**
  Fixes a `time_offset` double-count that silently dropped almost every clip
  past the first chunk on long videos; a clip-count estimator that assumed 40s
  clips (collapsing "3×15s from a 63s video" to 1); Whisper failures that
  silently downgraded to random duration-based clips instead of failing loudly;
  single-bound (min-only / max-only) duration overrides being ignored; the
  retry cascade widening past user-pinned bounds; and two caption/exception
  leaks into the output path. Adds `backend/core/concurrency.py` to cap
  parallel ffmpeg work.
- **Analyzer — chunked AI transcript correction.** The old single-call
  correction on `raw_text[:6000]` silently discarded everything past 6000 chars
  on long videos; now sentence-aligned chunking corrects the whole transcript
  with a per-chunk sanity guard (never loses content). Plus a `has_audio_stream`
  ffprobe preflight so a video-only/silent file raises a clear error instead of
  faster-whisper's opaque "tuple index out of range".
- **Captions — placement, flashing, and non-Latin fixes.** `alignment=5`
  (frame-center, ignores margins) → `alignment=2` (bottom-anchored) with
  per-aspect margins; phrase-aware line grouping with continuous-hold events so
  captions no longer blank out during Whisper's inter-word gaps; script-aware
  font fallback so CJK/Arabic/Thai captions stop burning as tofu boxes; libass
  preflight; concurrency-safe temp file; new `brainrot`/`urban`/`warm`/`mono`
  styles.
- **Music mix — voiceover level.** `amix` defaulted `normalize=1`, halving the
  voiceover to −6 dB; add `normalize=0` + an `alimiter` peak guard so the voice
  stays full-level with music as a true −20 dB bed.
- **Messaging — concurrent channel start.** `start_all()` now launches every
  channel in parallel with per-channel failure isolation, so the slowest
  channel no longer gates the rest.
- **Download hardening — pinned yt-dlp floor + TLS impersonation.**
  `requirements.txt` now pins `yt-dlp>=2026.7.4`: an unbounded `yt-dlp` on an
  old Python (macOS's system `python3` is 3.9) silently resolves to an ancient
  2025.10 release that fails on modern YouTube — the floor turns that into a
  loud install error instead of a broken downloader. Added
  `curl-cffi>=0.10,<0.15` and wired Chrome TLS impersonation into every
  yt-dlp call (`ytdlp_service`), so TLS-fingerprinting bot defenses
  (Cloudflare/Akamai) can't block downloads by handshake; degrades cleanly to
  urllib's fingerprint if curl-cffi is missing or incompatible.
- **Download reliability port from the hosted variant** (`ytdlp_service`):
  original-audio `format_sort` with `lang` leading (multi-language YouTube
  videos no longer download a dubbed audio track), exponential
  `retry_sleep_functions` per retry-pool, a 100 KB/s `throttledratelimit`
  guard that re-extracts stale signed URLs, and per-extractor args —
  PO-token-aware YouTube `player_client` ordering (token-free clients lead),
  `youtubetab` authcheck skip for public channel extraction, TikTok
  genuine-device-id flow, Twitter syndication API, Instagram/Reddit retry
  bumps. The pip self-update is now version-bounded (`yt-dlp>=2026.7.4`) so
  an outdated Python can't silently downgrade the downloader.

### Added
- **Tools page** — 18 single-purpose utilities (captions, reframe, audio-enhance, watermark, remove-silence, merge-clips, GIF, speed, trim, subtitles, auto-zoom, transform, music-visualizer, voice-over via Edge TTS, plus AI helpers: translate, metadata, hook-analysis, auto-chapters). The 13 ffmpeg/Whisper tools run fully locally with no API key; the AI helpers and the ✨ Enhance-prompt button use the user's own key (BYOK). Each tool has an inline result preview. New `/api/tools/*` router + `backend/core/tool_runners.py`. (AI media generators — image/music/video — are intentionally not in the OSS build.)
- **Proactive assistant** — the planner now reads live pipeline state (downloaded-not-clipped, generated-not-uploaded, scouted-not-downloaded) and surfaces the single highest-value next step. Backed by behavior-event instrumentation so the personalization engine learns from every completed job.

### Fixed
- Library self-heals — generated-video rows whose rendered file has been deleted are now pruned on list, so dead/broken tiles no longer linger.

### Security
- Bump `aiohttp` to `>=3.14.0,<4` — closes CVE-2026-34993 and CVE-2026-47265 (pip-audit). The frontend's `vite`/`esbuild` dev-server advisories are intentionally left for a future `vite` major bump: they affect only `npm run dev`, not the bundled app users ship, and the fix is a breaking change.
- Bump `cryptography` to `>=46.0.6,<47` — closes PYSEC-2026-35, GHSA-h4gh-qq45-vh27, CVE-2024-12797, CVE-2026-26007 (4 CVEs in the 43.x line).
- Bump `Pillow` to `>=12.2.0,<13.0` — closes CVE-2026-25990, 40192, 42308, 42309, 42310, 42311 (6 OOB / hang / memory-corruption issues affecting the thumbnail and ffmpeg image-processing paths). The `Image.ANTIALIAS` / `BICUBIC` monkeypatch in `backend/main.py` continues to work against Pillow 12.x.

### Changed
- Bump `openai` floor from `1.55` to `1.109.1` (still `<2.0`).
- Bump `playwright` floor from `1.58` to `1.59`.
- Bump 12 grouped Python minor/patch deps (dependabot `python-minor-patch` group).
- Bump `@mui/icons-material` 7.3.9→7.3.11, `axios`, `lucide-react` (dependabot `js-minor-patch` group).
- Bump CI actions — `actions/checkout` v4→v6 plus `setup-python`, `setup-node`, `codeql-action` (dependabot `ci-actions` group).

### Docs
- README — added an above-the-fold "Two ways to use ViralMint" callout clarifying the OSS variant (BYOK, Uploader agent, AGPL-3.0) vs the hosted SaaS at viralmint.net (prepaid credits, no auto-upload, closed-source). Helps new visitors pick the right variant without scrolling.

## [1.1.0] — 2026-05-07

### Added
- **OpenRouter as a third BYOK provider** — alongside Anthropic and OpenAI direct, a single OpenRouter API key now opens access to 300+ models (Claude, GPT, Gemini, Mixtral, Llama, etc.) through one credential. Configurable via `.env` or per-user in Settings → API Keys. See `backend/core/ai_provider.py`.

### Changed
- **Dependabot config** — minor/patch dependency updates are now batched into three groups (`python-minor-patch`, `js-minor-patch`, `ci-actions`) instead of arriving one PR at a time. Major framework versions (FastAPI / React / Pillow majors etc.) stay outside the groups so they always get explicit review.

## [1.0.0] — 2026-05-07

Initial open-source release.

### Added
- **Scout** — multi-platform trend discovery across YouTube, TikTok, Douyin, and Google Trends, with virality scoring and 3×–20× channel-baseline outlier detection.
- **Analyze** — local Whisper transcription plus AI insight extraction (hook, structure, tone, retention risks) per downloaded video.
- **Generate** — full pipeline: AI script → TTS voice → Pexels stock footage → word-by-word ASS captions → background music → finished mp4.
- **Clip Studio** — extract publishable 30–60s shorts from a long-form source; AI picks the best moments and burns captions.
- **Publish** — direct upload to YouTube (OAuth) and TikTok (OAuth or session cookie) with platform-optimized titles, descriptions, tags, and thumbnails.
- **Chat** — streaming WebSocket chat with the planner agent; action blocks dispatch background jobs (scout / download / analyze / generate / upload).
- **Messaging** — two-way chat over Telegram, WhatsApp, Discord, and Slack — same agent, different transport.
- **BYOK** — Anthropic / OpenAI / YouTube / Pexels / TikHub keys settable per-user in the UI or via `.env`. Per-user keys are AES-256 encrypted at rest.
- **Edge TTS** — 400+ free voices in 70+ languages; the default voiceover provider.
- **Universal downloader** — yt-dlp under the hood (1000+ sites supported).
- 92-test pytest suite covering crypto, scout scoring, captions, exception handling, HTTP utilities, and the async task runner.
- AGPL-3.0 license, SPDX headers on every Python source file.

### Security
- API binds to `127.0.0.1` (loopback) by default. Users who want LAN access can set `HOST=0.0.0.0` in `.env` knowingly.
- All third-party credentials encrypted with Fernet (AES-256) before being written to SQLite.
- No telemetry. No analytics. No cloud backend in the middle — keys go directly from your machine to the provider.

[Unreleased]: https://github.com/openclaw-easy/ViralMint/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/openclaw-easy/ViralMint/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/openclaw-easy/ViralMint/releases/tag/v1.0.0
