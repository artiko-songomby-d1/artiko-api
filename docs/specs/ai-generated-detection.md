# Spec: ai-generated-detection (Sightengine `genai`, free plan)

Branch: `feat/ai-generated-detection` (from `develop` at `156f568`). Status: **approved by the user on 2026-11-22** (threshold set to 0.8). **Amended 2026-11-22 after the three reviews** (AC24 to AC30, AC14, AC19 and AC22 adjusted; rate limit 10 and 30 a minute, EXIF documented, Render free plan confirmed by the user). Written 2026-11-22.

This replaces the abandoned content-moderation feature (nudity, hate, violence, self-harm). That work is kept as a backup on the local branch `feat/content-moderation` and is not part of this spec.

## Goal

An artist can publish an AI-generated image or video as if it were human-made. The API already has `ai_declared` and a `human_proven` badge (`app/features/works/models.py:46-47`), but nothing checks the file. This feature asks Sightengine's `genai` model whether the main file of a work is AI-generated, **before** it is stored, and refuses it with a 400 when the artist did not declare it. It runs on the Sightengine free plan and never blocks publishing when Sightengine is unavailable.

## User story

As the Artiko team, I want undeclared AI-generated images and videos refused at publish time, so that the museum and the Hall of Fame show works the artists really made or openly declared as AI.

## Decisions already taken by the user (2026-11-22)

- Outcome when AI is detected: **refuse with a 400**. No stored score, no flag, no review queue.
- Only the **main file** is checked (image, or MP4 as 3 frames). Proof files are never sent.
- 3D works are **not** checked: no GLB, no poster, no `model_hd`.
- Threshold `0.8`: a score below 0.8 is approved, a score of 0.8 or more is refused. It is higher than the 0.5 of `../devfest/sightengine-service` to make false positives rarer, because a wrongly refused artist has no way to contest.
- No badge, flag, score or review: detection only approves or refuses, and nothing about `human_proven` changes.
- Fail-open when Sightengine is down or the quota is gone (from the moderation decisions, kept).
- Free plan only. No audio. MP4 only for video, 3 frames, through the image endpoint. WebM refused when a check is needed.
- Start from `develop`; the moderation branch is a backup and nothing is cherry-picked from it.
- Images over 12 MB are shrunk before the check (AC8). The free-plan capacity (about 400 images or 133 videos a month) is enough for launch.

## Acceptance criteria

### Provider and configuration
- **AC1 (off by default).** Given `AI_DETECTION_PROVIDER=none` (the default), when a work is published, then no HTTP request leaves the process and the behavior is exactly today's.
- **AC2 (credentials required).** Given `AI_DETECTION_PROVIDER=sightengine` and an empty `SIGHTENGINE_USER` or `SIGHTENGINE_SECRET`, when the app starts, then it refuses to start with a message that names the missing variable (never a value).
- **AC3 (unknown provider).** Given any other value of `AI_DETECTION_PROVIDER`, then the app refuses to start.

### Images
- **AC4 (image request).** Given provider `sightengine`, a main file that is an image and `ai_declared=false`, when the work is published, then exactly one request goes to `check.json` with `models=genai` and the file bytes as `media`, and the AI score is `type.ai_generated` of the response.
- **AC5 (refusal).** Given a score at or above `AI_DETECTION_THRESHOLD` (default 0.8, so exactly 0.8 is refused), then the response is 400 `ai_generated_content` with `field: "file"`. The message tells the artist to declare the work as AI-generated (`ai_declared`) if it is. Nothing is stored: no file, no `Work` row, no `ReviewCase`.
- **AC6 (accepted).** Given a score below the threshold (below 0.8 by default, for example 0.68 or 0.79), then the work is published as today.
- **AC7 (declared AI skips the check).** Given `ai_declared=true`, then no request is sent and the work is published as today (it never gets `human_proven`, by the existing rule).
- **AC8 (large image is shrunk).** Given an image over 12 MB (12 x 1024 x 1024 bytes, the free plan limit), then a copy is re-encoded as JPEG with Pillow, its longest side reduced by a quarter at each step, at most 6 steps, until it fits; that copy is then checked as in AC4. The stored file is always the original, never the copy. If the image cannot be decoded or is still over 12 MB after 6 steps, it is accepted unchecked and one WARNING is logged.

### Video (MP4 only, 3 frames)
- **AC9 (3 frames).** Given a main file that is an MP4 and `ai_declared=false`, then the service reads its duration with ffprobe, extracts one JPEG frame (at most 1280 px wide) at 30%, 50% and 70% of the duration, and sends each to `check.json` with `models=genai`, one after another. The work is refused when the highest frame score is at or above the threshold (AC5 response). `video/check-sync.json` is never called.
- **AC10 (WebM refused).** Given `AI_DETECTION_PROVIDER=sightengine`, a WebM main file and `ai_declared=false`, then the response is 400 `unsupported_video_format` ("Only MP4 videos are accepted") and nothing is stored. With provider `none`, or with `ai_declared=true`, WebM is accepted as today.
- **AC11 (unreadable MP4).** Given an MP4 that ffprobe cannot read, with no video stream, or with a duration that is not a number above 0, then the response is 400 `unreadable_video` and nothing is stored.
- **AC12 (frame fallback).** Given a position that yields no frame (a very short video), then the extraction is retried once at 0 s; frames with the same effective position are checked once.
- **AC13 (ffmpeg missing).** Given ffmpeg or ffprobe cannot be started, then the video is accepted (fail-open) and an error is logged.
- **AC14 (ffmpeg hardening).** Given any ffprobe or ffmpeg process: it is started without a shell, with an argument list that never contains an uploaded file name; its environment contains only `PATH`, `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` (constants: no variable of the service, no secret); it runs single-threaded, with `-threads 1` on the input and, for ffmpeg, `-filter_threads 1 -threads 1` on the output; the input is a file in a private temporary directory (mode 0700) removed in every outcome; it carries `-protocol_whitelist file`, `-format_whitelist mov,mp4,m4a,3gp,3g2,mj2` and `-max_alloc 268435456`; it runs under `prlimit --data=<AC27 memory cap>`; it is killed after 30 s; the whole video check, including the wait for a video slot, stops after 60 s (AC26). At most `AI_DETECTION_MAX_VIDEO_CHECKS` (default 1) video checks run at once (the others wait inside that deadline).

### Not checked
- **AC15 (3D, audio, proofs).** Given a GLB work (with or without `poster` and `model_hd`), an audio file, or any proof file, then nothing is sent and nothing is refused. They behave as today.

### Fail-open
- **AC16 (Sightengine failures accept).** Given a network error, a timeout (30 s per call), an HTTP 5xx, a body that is not JSON, or a Sightengine `failure` of any type (`usage_limit`, `credentials_error`, `plan_error`, `argument_error`, `media_error`), then the work is accepted and one WARNING is logged with the kind of file and the Sightengine error code only.
- **AC17 (partial results).** Given a video where some frames were checked and others failed, then the work is refused only if a successfully checked frame is at or above the threshold.
- **AC18 (rate limiter).** Given any Sightengine requests, then one process-wide limiter makes any two requests start at least 1.1 s apart (the free plan allows 1 request per second). A request that waits more than `AI_DETECTION_WAIT_SECONDS` (default 30) for its turn counts as a failure (AC16).

### Contract, order, privacy
- **AC19 (order).** Given `publish_work`, then the check runs after the artist, exposition, proof-count, proof-claims and duplicate-file checks (every cheap validation, so a bad request spends no operation) and before the embedding, the copy-detection checks, the proof reading and any `storage.save`. A refused file opens no review case.
- **AC20 (error body).** Given a refusal, then the body is `{"error": {"code": "ai_generated_content", "message": "...", "field": "file"}}` with HTTP 400. The score is never returned.
- **AC21 (openapi).** `openapi.yml` documents the 400 `ai_generated_content`, `unsupported_video_format` and `unreadable_video` responses of `POST /works`, and `tests/test_contract.py` still passes.
- **AC22 (what leaves).** Given any request, then only the main file of an image work, or the frames cut from the main file of an MP4 work, are sent to Sightengine. No title, story, text, proof file, 3D file or artist data is ever sent. An image is sent as the file it is, so its embedded metadata (EXIF: GPS, camera, author) is sent too; the README privacy note says so.
- **AC23 (logs).** Given any outcome, then logs never contain credentials, file bytes or scores. A refusal is logged at INFO with `field` only.

### Hardening added after the review of 2026-11-22
- **AC24 (publish rate limit).** Given `AI_DETECTION_PROVIDER=sightengine`, when `POST /works` is called, then each authenticated user may send at most `WORKS_RATE_LIMIT_PER_MINUTE` (default 10) and all users together at most `WORKS_GLOBAL_RATE_LIMIT_PER_MINUTE` (default 30) requests per minute; beyond that the response is 429 with `Retry-After`, using the project's existing limiter (as `proof_limiter`, `app/features/works/router.py`). With provider `none` nothing changes (AC1).
- **AC25 (robust answer parsing).** Given any error while reading or parsing the Sightengine answer (a body that is not JSON, a deeply nested body that raises `RecursionError`, a body over 1 MiB, an unexpected shape), then the work is accepted (AC16) and never a 500. The answer is read with a 1 MiB cap. An upstream error code is logged only when it is a short alphanumeric string or an integer (at most 20 characters), otherwise `unknown`.
- **AC26 (bounded waits).** Given a video check, then the wait for a video slot counts inside the 60 s deadline; when it expires the work is accepted unchecked with a WARNING.
- **AC27 (memory).** Given an image over 12 MB, then a copy is built only when `width x height` is at most 20,000,000 pixels (above it: accepted unchecked with a WARNING) and at most one such copy is built at a time. Given ffmpeg and ffprobe, then the `prlimit --data` cap is configurable (`AI_DETECTION_FFMPEG_MEMORY_MB`) and its default is the smallest value with which the 1080p and the noisy 3840x2160 MP4 test files both give 3 frames, plus a margin of at least 64 MiB, and never above 512: **272 MiB** (measured values under Technical constraints). This fits a 512 MB instance (Render free plan; the RAM figure is unverified).
- **AC28 (settings validation).** Given the settings, then the app refuses to start (naming the variable) when `AI_DETECTION_THRESHOLD` is not a finite number in (0, 1], `AI_DETECTION_WAIT_SECONDS` is negative, a limit or the video slot count is below 1, or a Sightengine credential is empty after trimming spaces.
- **AC29 (timeouts and missing frames accept).** Given ffprobe or ffmpeg killed by its 30 s timeout, then the work is accepted unchecked with a WARNING, not refused as `unreadable_video`. Given a video that ffprobe reads (a video stream and a positive duration) but from which ffmpeg extracts no frame at any position (for example `Cannot allocate memory` under the AC27 cap), then the work is accepted unchecked with a WARNING (`ffmpeg extracted no frame`, no file content), not refused. Given fewer than 3 frames extracted and at least one scored, then the decision uses the scored frames and a WARNING gives the frame count. A file that ffprobe rejects quickly (non-zero exit), has no video stream or has no duration stays a 400 `unreadable_video` (AC11); it is the only video refused as unreadable.
- **AC30 (limiter fairness).** Given a request cancelled while waiting for its turn (client disconnect), then its slot is released and does not delay later requests.

## Out of scope

- Content moderation (nudity, hate, violence, self-harm): see the backup branch `feat/content-moderation`.
- Storing the score, showing a badge or warning, any change to `human_proven`, any human review.
- Text, audio (including AI music and speech detection), 3D works, proof files.
- Sightengine's video endpoints, deepfake detection and any paid-plan feature.
- Re-checking works that are already published.
- Two artists publishing the same file at the same instant hanging `POST /works` (reported by a reviewer, unverified on `develop`, not part of this feature).
- The Dockerfile running as root (existing, no `USER` line).
- Stripping image metadata before sending (documented in the README instead, by decision).
- Showing the refusal in artiko-web and artiko-mobile (follow-up: both must display the `message` of a 400 `ai_generated_content`, and offer the `ai_declared` choice).

## Edge cases and errors

| Case | Expected behavior |
| --- | --- |
| False positive on a human-made work | Refused (AC5) if its score is 0.8 or more. There is no contest path (no review queue, by decision). The 0.8 threshold makes this rarer but not impossible. The artist can still publish by declaring the work as AI (`ai_declared`), which gives it no `human_proven`, as today. |
| Score between 0.5 and 0.8 | Accepted (AC6). A work that is probably AI but not certain gets through. Accepted by decision. |
| Screenshot or photo of an AI image | Whatever Sightengine scores. Unverified. |
| Image between 12 MB and 20 MB | A shrunk copy is checked (AC8). The API accepts up to 20 MB (`max_upload_mb`, `app/platform/config.py`). |
| Free quota spent (500 a day, 2,000 a month) | `usage_limit`: accepted unchecked (AC16) until the quota resets. |
| Video where only some frames look AI | Refused when the highest frame reaches the threshold (AC9). Observed: one frame at 0.001, one at 0.99, one at 0.68: refused because of the 0.99 frame. Without it, 0.68 alone would be accepted. |
| Many MP4 publishes at once | Each MP4 needs 3 Sightengine requests, 1.1 s apart, so about 54 requests a minute fit. Past that, requests wait up to 30 s and the excess is accepted unchecked (fail-open, by decision). The AC24 limits make this rare. |
| Two publishes at the same time | The limiter serializes requests (AC18); the second may wait up to 30 s, then is accepted unchecked. |
| Client disconnects during a video check | The check stops and the temporary directory is removed (AC14). Nothing is stored. |
| 4K MP4 | Frames are scaled to 1280 px wide, so every frame stays small. |
| MP4 that ffprobe reads but ffmpeg cannot cut (memory cap hit, decoder error) | Accepted unchecked with a WARNING (AC29). A file built to exhaust ffmpeg's memory therefore skips the check (fail-open, by decision). |
| MP4 that gives only 1 or 2 frames | Checked on those frames; a WARNING gives the count (AC29). |
| Refusal after the file was read | Nothing was stored yet (AC5, AC19). |

## Technical constraints

- **Stack.** Python `>=3.11`, FastAPI `>=0.165.0`, `httpx>=0.28.1` (`pyproject.toml`); Dockerfile base `python:3.14-slim`. No new Python dependency.
- **Sightengine free plan** ([pricing](https://sightengine.com/pricing)): 2,000 operations a month, 500 a day, 1 request per second, image up to 12 MB. `genai` costs **5 operations** per image ([what is an operation](https://sightengine.com/faq/what-is-an-operation)). Capacity on the free plan: about **100 images or 33 videos a day, and about 400 images or 133 videos a month**. After that the check is skipped (fail-open).
- **Verified live on the free plan, 2026-11-22.** `genai` works through `check.json` and costs 5 operations per request. A real painting (Monet) scored 0.001. Of the two images in `../devfest/images_test`, `QCL-1185_….webp` scored 0.001 and `media_15865fd3….jpg` scored 0.99. The 24 s video `multitrack-hero-ai.mp4`, cut at 30%, 50% and 70%, scored 0.001, 0.99 and 0.68. The "AI" ground truth of these samples comes from the file names only (unverified). The video endpoint is refused on the free plan (error 3701, observed the same day and in `../devfest`).
- **Shrinking keeps the signal on one sample (2026-11-22).** The 600x600 image that scored 0.99 still scored 0.99 at 300x300 (JPEG quality 85) and 0.9 at 150x150 (quality 70). One sample only; the effect on other images is unverified. Pillow is already a dependency (`pyproject.toml`: `pillow>=13.0.0`).
- **Threshold is unverified.** 0.8 is chosen by the user (the reference service used 0.5, `sightengine.service.ts:21`). In the samples, scores were 0.001, 0.68 or 0.9 and above, so 0.8 separates them (0.68 accepted, 0.9 and above refused), but four images and three frames are not an accuracy test: it needs real artworks before launch. A shrunk copy scored 0.9 against 0.99 for the original, so shrinking can lower a score and let a borderline image through.
- **Existing patterns.** Provider switch like `embed_provider` (`app/platform/config.py:71`, `app/platform/embed.py:44`); tests turn providers off in `tests/conftest.py:25`; errors through `ApiError` (`app/platform/errors.py:16`), whose `details` are merged into the `error` object; import rule in `tests/test_architecture.py:42`.
- **Reference code to port** (from `../devfest/sightengine-service`, not a dependency): `sightengine.service.ts:28` (`detectImage`), `:71` (`threshold`), `:78` (`call`), `:123` (`mapError`); on branch `feat/video-middle-clip`: `media/video-detection.service.ts:28`, `media/ffmpeg-frame-extractor.ts:29-41` (flags), `sightengine/sightengine-rate-limiter.ts:4-5` (1 s spacing).
- **Security.** Secrets only in `.env`, never logged; `.env.example` and `render.yaml` updated; ruff `S` rules apply to the subprocess calls (`# noqa: S603` with the reason). The main file of every undeclared work is sent to a third party: note it in the privacy statement.
- **Latency.** Checks run inside the request: an image about 1.5 s, an MP4 about 4 to 5 s.
- **Shrink memory, measured 2026-11-22** (Pillow 13, a PNG decoded and shrunk in one process, peak above the 47 MiB baseline): 20,000,000 pixels (5000x4000, RGB or RGBA) peak about 254 MiB; 40,000,000 pixels (8000x5000) peaked about 507 MiB, which does not fit a 512 MB instance, so the cap is 20,000,000 (user decision). An image over 20 million pixels and 12 MB, such as a large camera photo, is accepted unchecked. The total memory of the app on Render (CLIP model, Python, one shrink and one ffmpeg at a time) is unverified.
- **ffmpeg memory, measured 2026-11-22** (`prlimit --data`, the AC14 flags and environment, 2 s MP4 files: 1920x1080 `testsrc`, and 3840x2160 `testsrc2` with `noise=alls=30:allf=t+u`, 14 MB). Smallest cap giving 3 frames: development machine (Debian 12, ffmpeg 5.1.9, 4 CPUs) 68 MiB for 1080p and 134 MiB for 4K; `python:3.14-slim` with the apt `ffmpeg` (Debian 13, ffmpeg 7.1.5, reference BLAS, 4 CPUs) 124 MiB for 1080p and 202 MiB for 4K. The larger, 202 MiB, plus a margin gives the 272 MiB default. Cause of the earlier failures: the development machine's ffmpeg links OpenBLAS, which starts one worker per CPU at start-up, each reserving about 128 MiB; under a lower cap that allocation never succeeds and ffmpeg hangs until the 30 s kill (`ffprobe -version` alone needed 448 MiB). `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` stop that. Without them and with default threads, the noisy 4K file needed between 769 and 1024 MiB there. The values depend on the CPU count and the ffmpeg build; the Render instance is unverified.
- **System package.** `ffmpeg` and `ffprobe` must exist in the image: the `Dockerfile` has none, so an `apt-get install` step is added (to validate). `prlimit` comes from `util-linux`. Both exist on the development machine (`/usr/bin/ffmpeg`, checked 2026-11-22).

## Architecture impact

- **Infrastructure (`app/platform/`).** `sightengine.py` (httpx client, 1.1 s limiter, timeouts, errors mapped to "unavailable"); `video_frames.py` (ffprobe and ffmpeg runner with the AC14 limits); `ai_detection.py` (policy and one entry point called by the works service; it raises `BadRequest("ai_generated_content", ...)` and returns normally in every other case); `config.py` (`ai_detection_provider`, `sightengine_user`, `sightengine_secret`, `ai_detection_threshold`, `ai_detection_wait_seconds`, with startup validation for AC2 and AC3). Everything stays in `platform/`, so `ALLOWED` in `tests/test_architecture.py` does not change.
- **Application.** `app/features/works/service.py:publish_work`: one call after the duplicate-file check, before the embedding (AC19). No other feature changes.
- **Interface.** `openapi.yml` (AC21). No new route.
- **Data.** No migration, no new column (nothing is stored).
- **Config and deploy.** `.env.example`, `render.yaml` (`AI_DETECTION_PROVIDER`, `SIGHTENGINE_USER`, `SIGHTENGINE_SECRET`, not synced), `Dockerfile` (ffmpeg), `README.md` (variables, operation costs, free-plan capacity, fail-open behavior).
- **New dependencies to validate.** System: `ffmpeg` in the Docker image. Python: none.

## Test plan

Unit tests use `httpx.MockTransport`: no test calls the network. `tests/conftest.py` sets `AI_DETECTION_PROVIDER=none` for all existing tests.

| AC | Automated test |
| --- | --- |
| AC1 | `test_ai_detection_is_off_by_default` (no outgoing request, existing works tests unchanged) |
| AC2, AC3 | `test_startup_refuses_missing_credentials_or_unknown_provider` |
| AC4 | `test_image_is_sent_to_genai` (one request, `models=genai`) |
| AC5 | `test_ai_image_is_refused_and_nothing_is_stored` (no file, no `Work`, no `ReviewCase`) |
| AC6 | `test_human_image_is_accepted` |
| AC7 | `test_declared_ai_skips_the_check` |
| AC8 | `test_image_over_12_mb_is_shrunk_and_the_original_is_stored`, `test_image_that_cannot_be_shrunk_is_accepted_unchecked` |
| AC9 | `test_mp4_three_frames_use_the_image_endpoint_and_max_score_decides` (tiny real MP4 built with ffmpeg) |
| AC10 | `test_webm_refused_when_a_check_is_needed`, `test_webm_accepted_when_declared_or_provider_off` |
| AC11 | `test_unreadable_mp4_is_refused` |
| AC12 | `test_short_video_falls_back_to_the_first_frame` |
| AC13 | `test_missing_ffmpeg_is_fail_open` |
| AC14 | `test_ffmpeg_arguments_environment_and_cleanup` (environment, thread options), `test_ffmpeg_timeout_kills_the_process`, `test_video_checks_past_the_slot_count_wait` |
| AC15 | `test_glb_audio_and_proofs_are_never_sent` |
| AC16 | `test_every_sightengine_failure_is_fail_open` |
| AC17 | `test_partial_frame_failure` |
| AC18 | `test_requests_are_spaced_and_a_long_wait_is_fail_open` |
| AC19 | `test_check_runs_after_cheap_checks_and_before_storage` (including `invalid_proof_claims`) |
| AC20, AC21 | `test_refusal_body_shape`, existing `test_contract.py` |
| AC22 | `test_only_the_main_file_or_its_frames_leave` |
| AC23 | `test_logs_hold_no_secret_bytes_or_score` (caplog) |
| AC24 | `test_publishing_is_rate_limited_per_user_and_overall`, `test_publishing_is_not_rate_limited_when_detection_is_off`, `test_an_anonymous_publish_is_refused_before_the_rate_limit` |
| AC25 | `test_a_hostile_answer_is_fail_open`, `test_an_answer_over_1_mib_is_not_read_to_the_end`, `test_an_answer_of_exactly_1_mib_is_read`, `test_only_a_short_plain_error_code_is_logged` |
| AC26 | `test_the_wait_for_a_video_slot_counts_in_the_deadline`, `test_video_check_stops_at_its_deadline`, `test_one_detector_serves_one_event_loop_after_another` |
| AC27 | `test_an_image_over_20_m_pixels_is_accepted_without_a_copy`, `test_the_pixel_cap_is_inclusive`, `test_one_shrunk_copy_is_built_at_a_time`, `test_three_frames_fit_under_the_default_memory_cap` (1080p and noisy 4K) |
| AC28 | `test_startup_refuses_invalid_ai_detection_settings`, `test_credentials_are_trimmed_and_edge_values_are_accepted` |
| AC29 | `test_a_tool_killed_at_its_timeout_accepts_the_video`, `test_a_probe_that_fails_quickly_refuses_the_video`, `test_a_probed_video_ffmpeg_cannot_cut_is_accepted_unchecked`, `test_a_video_with_fewer_than_three_frames_is_checked_with_a_warning`, `test_a_probed_video_without_any_frame_gives_an_empty_list`, `test_frames_found_at_some_positions_only_are_kept` |
| AC30 | `test_a_waiter_cancelled_before_its_turn_releases_its_slot`, `test_a_slot_freed_between_two_taken_ones_is_reused` |

Also: `tests/test_architecture.py` still passes; `task lint` clean. The suite uses the local database `artiko_test` (`TEST_DATABASE_URL=postgresql://artiko:artiko@127.0.0.1:5432/artiko_test`); no migration mentions pgvector.

Manual checks (human, real keys, free plan):
1. Publish a real painting: accepted.
2. Publish `../devfest/images_test/media_15865fd3….jpg` (scored 0.99): 400 `ai_generated_content`. Publish it again with `ai_declared=true`: accepted, no `human_proven`.
3. Publish `multitrack-hero-ai.mp4` (12 MB): 400. Publish a real MP4: accepted.
4. Publish a WebM: 400 `unsupported_video_format`. Publish a GLB with a poster: accepted unchecked.
5. Use a wrong secret: the work is accepted and one WARNING appears.
6. Compare the operations used in the Sightengine dashboard with 5 per image and 15 per MP4.

## Default choices applied

- Frames are JPEG, scaled to at most 1280 px wide (the reference service used PNG with a downscale retry).
- The decision for a video is the highest frame score, as in the reference service.
- Provider default is `none`, so a missing setting never blocks start-up or tests. Production must set `sightengine` explicitly.
- The unreadable-MP4 refusal, the WebM refusal and the ffmpeg hardening are carried over from the moderation spec you approved.
- The refusal message points to `ai_declared`, the existing way to publish AI work.

## Open questions

None. Resolved by the user on 2026-11-22:

1. Images over 12 MB are shrunk before the check (AC8).
2. The free-plan capacity (about 400 images or 133 videos a month) is enough.
3. False positives: threshold 0.8 (below approved, 0.8 or more refused), no contest path, no badge, no review.
