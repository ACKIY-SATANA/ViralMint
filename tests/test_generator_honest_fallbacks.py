# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""A render that quietly substitutes something must say so.

Two silent substitutions in the Smart Video pipeline, plus the bed level that
made background music inaudible.

* `select_music` matches genre by filename glob and, when nothing matches,
  falls back to ANY track. The user picked "lofi" and got an upbeat sample
  with no signal that anything had been swapped.
* `_generate_voice` falls back to free Edge TTS when the selected provider has
  no key or fails mid-generation. The dropdown said one voice and the video
  spoke in another — the class of bug that makes users stop trusting every
  setting on the page.
* The -20dB music bed moved a real render's mean volume by 0.1dB. Users
  reported it as "no background music", because there effectively wasn't any.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.generator import GeneratorAgent


@pytest.fixture()
def warnings(monkeypatch):
    """Capture every constraint_warning the pipeline emits."""
    seen: list[dict] = []

    async def fake(constraint, message, severity="warning", wizard_id=None,
                   user_id="local"):
        seen.append({"constraint": constraint, "message": message,
                     "severity": severity})

    from backend.core.ws_manager import ws_manager
    monkeypatch.setattr(ws_manager, "send_constraint_warning", fake)
    return seen


# ── the music bed you can actually hear ─────────────────────────────────────

class TestMusicBedLevel:
    def test_the_default_is_the_audible_bed_everywhere(self):
        """One number, four places — a stale copy is what made this inaudible
        in the first place."""
        import inspect

        from backend.api.settings import SettingsResponse
        from backend.models.user_settings import UserSettings
        from backend.services.music_service import mix_audio

        assert SettingsResponse.model_fields["music_volume_db"].default == -14.0
        assert UserSettings.__table__.c.music_volume_db.default.arg == -14.0
        assert inspect.signature(mix_audio).parameters["music_volume_db"].default == -14.0

    def _resolve(self, stored):
        settings_row = SimpleNamespace(
            tts_provider="edge_tts", preferred_tts_voice=None,
            caption_style=None, caption_enabled=True, caption_emoji_style=None,
            music_enabled=True, music_genre="lofi", music_volume_db=stored,
            auto_zoom_enabled=False,
        )
        return GeneratorAgent()._resolve_options(
            settings_row, None, None, None, None, None, None,
        )["music_volume_db"]

    def test_a_stored_legacy_minus_20_is_upgraded(self):
        """-20 is the old COLUMN DEFAULT and no UI has ever written this
        field, so a stored -20 is not a user's choice — it's the setting that
        made the bed inaudible."""
        assert self._resolve(-20.0) == -14.0

    def test_a_deliberate_setting_is_left_alone(self):
        for stored in (-10.0, -18.0, -25.0):
            assert self._resolve(stored) == stored

    def test_an_unset_column_gets_the_audible_default(self):
        assert self._resolve(None) == -14.0


# ── a substituted genre is announced ────────────────────────────────────────

class TestMusicGenreFallback:
    def _mix(self, picked: str, chosen_file: str, warnings):
        agent = GeneratorAgent()
        with patch("backend.services.music_service.select_music",
                   new=AsyncMock(return_value=Path(f"/music/{chosen_file}"))), \
             patch("backend.services.music_service.mix_audio",
                   new=AsyncMock(return_value=Path("/tmp/mixed.mp3"))):
            asyncio.run(agent._mix_music(
                Path("/tmp/voice.mp3"),
                {"music_genre": picked, "music_volume_db": -14.0},
            ))
        return warnings

    def test_a_substituted_track_warns_and_names_it(self, warnings):
        self._mix("lofi", "upbeat_energy.mp3", warnings)
        assert len(warnings) == 1
        w = warnings[0]
        assert w["constraint"] == "music_genre_fallback"
        assert "lofi" in w["message"]
        assert "upbeat_energy" in w["message"], "the user must know WHAT they got"

    def test_a_genre_match_says_nothing(self, warnings):
        self._mix("lofi", "lofi_chill_beat.mp3", warnings)
        assert warnings == []

    def test_the_match_is_case_insensitive(self, warnings):
        self._mix("Lofi", "LOFI_Chill.mp3", warnings)
        assert warnings == []

    def test_no_track_at_all_is_not_a_substitution(self, warnings):
        """Nothing was swapped — the render just has no music."""
        agent = GeneratorAgent()
        with patch("backend.services.music_service.select_music",
                   new=AsyncMock(return_value=None)):
            out = asyncio.run(agent._mix_music(
                Path("/tmp/voice.mp3"), {"music_genre": "lofi"}))
        assert out == Path("/tmp/voice.mp3")
        assert warnings == []


# ── a substituted voice is announced ────────────────────────────────────────

class TestVoiceFallback:
    def _voice(self, *, has_key: bool, tts_raises: bool):
        from backend.services.tts_service import TTSProvider

        agent = GeneratorAgent()
        opts = {
            "tts_provider": TTSProvider.OPENAI_TTS,
            "tts_voice": "alloy",
            "tts_label": "OpenAI TTS",
        }
        calls: list = []

        async def fake_tts(text, provider, voice_id=None, api_key=""):
            calls.append(provider)
            if tts_raises and provider != TTSProvider.EDGE_TTS:
                raise RuntimeError("provider exploded")
            return Path("/tmp/voice.mp3")

        with patch("backend.services.tts_service.generate_tts", new=fake_tts), \
             patch("backend.config.settings.OPENAI_API_KEY",
                   "sk-test" if has_key else ""):
            asyncio.run(agent._generate_voice("hello", opts, None))
        return calls

    def test_a_missing_key_warns_before_narrating_in_another_voice(self, warnings):
        self._voice(has_key=False, tts_raises=False)
        assert len(warnings) == 1
        assert warnings[0]["constraint"] == "tts_fallback"
        assert "OpenAI TTS" in warnings[0]["message"]
        assert "Edge" in warnings[0]["message"]

    def test_a_mid_generation_failure_warns_too(self, warnings):
        from backend.services.tts_service import TTSProvider
        calls = self._voice(has_key=True, tts_raises=True)
        assert TTSProvider.EDGE_TTS in calls, "it must still deliver a video"
        assert len(warnings) == 1
        assert warnings[0]["constraint"] == "tts_fallback"
        assert "failed" in warnings[0]["message"]

    def test_the_happy_path_stays_quiet(self, warnings):
        self._voice(has_key=True, tts_raises=False)
        assert warnings == []

    def test_a_ws_failure_never_breaks_the_render(self, monkeypatch):
        """The warning is best-effort — a dead socket must not lose the video."""
        from backend.core.ws_manager import ws_manager

        async def boom(*a, **k):
            raise RuntimeError("socket gone")

        monkeypatch.setattr(ws_manager, "send_constraint_warning", boom)
        assert self._voice(has_key=False, tts_raises=False)
