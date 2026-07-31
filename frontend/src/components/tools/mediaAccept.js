// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Accept lists shared by the tool pages.
 *
 * VIDEO_ACCEPT mirrors backend/api/tools.py VIDEO_EXTS; MEDIA_ACCEPT mirrors
 * MEDIA_EXTS — the video-or-audio set the Audio-group cleanup tools (Enhance
 * Audio / Silence Remover) take. Keeping them in one module means a page can't
 * advertise an extension the endpoint rejects (both audio tools used to offer
 * video only, so a podcast mp3 was refused with a 400 after the upload).
 */
export const VIDEO_ACCEPT = ".mp4,.mov,.mkv,.webm,.m4v"
export const AUDIO_ACCEPT = ".mp3,.wav,.m4a,.aac,.ogg,.flac"
export const MEDIA_ACCEPT = `${VIDEO_ACCEPT},${AUDIO_ACCEPT}`
