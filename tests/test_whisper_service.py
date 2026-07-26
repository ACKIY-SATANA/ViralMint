# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""WhisperService model resolution, HF cache location, and idle eviction.

The bug these pin: transcribe() used to call load() with NO quality argument,
so it always re-resolved to "balanced". A caller that had deliberately
pre-loaded a bigger model (the analyzer does, for the user's whisper_quality
setting) had it thrown away and reloaded as "small" — the user's choice was
silently ignored AND the load was paid for twice. Quality is explicit now.

No real model is ever loaded here: faster_whisper.WhisperModel is stubbed.
"""
import asyncio
import sys
import types

import pytest

from backend.services import whisper_service as ws_mod
from backend.services.whisper_service import WhisperService, WHISPER_QUALITY_MAP


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel; records what was asked for."""

    def __init__(self, name, device=None, compute_type=None):
        self.name = name
        self.calls = []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        seg = types.SimpleNamespace(
            start=0.0, end=1.0, text=" hello ",
            words=[types.SimpleNamespace(word="hello", start=0.0, end=1.0, probability=0.9)],
        )
        info = types.SimpleNamespace(language="en", language_probability=0.99)
        return iter([seg]), info


@pytest.fixture(autouse=True)
def _stub_faster_whisper(monkeypatch):
    """Install a fake faster_whisper module and reset the singleton's state."""
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    WhisperService._model = None
    WhisperService._loaded_quality = None
    WhisperService._evict_task = None
    yield
    WhisperService._model = None
    WhisperService._loaded_quality = None
    WhisperService._evict_task = None


@pytest.fixture(autouse=True)
def _always_has_audio(monkeypatch):
    async def _yes(_path):
        return True
    monkeypatch.setattr("backend.services.ffmpeg_service.has_audio_stream", _yes)


# ── quality resolution ─────────────────────────────────────────────────────

def test_load_maps_quality_to_model_name():
    assert WhisperService.load("best").name == WHISPER_QUALITY_MAP["best"]
    assert WhisperService.load("fast").name == WHISPER_QUALITY_MAP["fast"]


def test_transcribe_honours_explicit_quality(tmp_path):
    """The regression: a pre-loaded "best" model must not be swapped for "small".

    Mirrors the analyzer's shape — pre-load the user's quality, then transcribe.
    """
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    WhisperService.load("best")
    assert WhisperService._loaded_quality == "best"

    asyncio.run(ws_mod.whisper_service.transcribe(audio, quality="best"))
    # Still large-v3 — no silent downgrade, and no second load.
    assert WhisperService._loaded_quality == "best"
    assert WhisperService._model.name == WHISPER_QUALITY_MAP["best"]


def test_transcribe_defaults_to_balanced(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio))
    assert WhisperService._loaded_quality == "balanced"


def test_timing_only_selects_greedy_and_vad(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio, timing_only=True))
    kw = WhisperService._model.calls[-1]
    assert kw["beam_size"] == 1 and kw["vad_filter"] is True


def test_default_decode_stays_accurate(tmp_path):
    # Read-facing transcripts must keep beam search and no VAD gating.
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio))
    kw = WhisperService._model.calls[-1]
    assert kw["beam_size"] == 5 and kw["vad_filter"] is False


def test_transcribe_rejects_audioless_input(tmp_path, monkeypatch):
    async def _no(_path):
        return False
    monkeypatch.setattr("backend.services.ffmpeg_service.has_audio_stream", _no)
    audio = tmp_path / "silent.mp4"
    audio.write_bytes(b"x")
    with pytest.raises(ValueError):
        asyncio.run(ws_mod.whisper_service.transcribe(audio))


# ── HF cache location ──────────────────────────────────────────────────────

def test_hub_dir_honours_hf_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "custom"))
    assert WhisperService._hub_dir() == tmp_path / "custom" / "hub"


def test_hub_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    assert WhisperService._hub_dir().parts[-3:] == (".cache", "huggingface", "hub")


def test_is_model_cached_reads_the_hf_home_tree(monkeypatch, tmp_path):
    """Reporting a present model as missing is what caused a second ~GB
    download, so this must follow HF_HOME rather than a hardcoded path."""
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert WhisperService.is_model_cached("balanced") is False
    (tmp_path / "hub" / f"models--Systran--faster-whisper-{WHISPER_QUALITY_MAP['balanced']}").mkdir(parents=True)
    assert WhisperService.is_model_cached("balanced") is True


def test_is_model_cached_false_while_still_downloading(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    blobs = (tmp_path / "hub"
             / f"models--Systran--faster-whisper-{WHISPER_QUALITY_MAP['balanced']}" / "blobs")
    blobs.mkdir(parents=True)
    (blobs / "part.incomplete").write_bytes(b"")
    assert WhisperService.is_model_cached("balanced") is False


# ── idle eviction ──────────────────────────────────────────────────────────

def test_unload_frees_a_resident_model():
    WhisperService.load("best")
    assert WhisperService.unload() is True
    assert WhisperService._model is None
    assert WhisperService.unload() is False  # idempotent


def test_evictor_drops_a_heavy_model_once_idle(monkeypatch):
    monkeypatch.setattr(ws_mod, "_IDLE_EVICT_SECONDS", 0)
    WhisperService.load("best")           # large-v3 → heavy
    asyncio.run(WhisperService._evict_when_idle())
    assert WhisperService._model is None


def test_evictor_leaves_light_models_resident(monkeypatch):
    monkeypatch.setattr(ws_mod, "_IDLE_EVICT_SECONDS", 0)
    WhisperService.load("fast")           # base → cheap to keep hot
    asyncio.run(WhisperService._evict_when_idle())
    assert WhisperService._model is not None


def test_arm_evictor_is_a_noop_without_a_running_loop():
    # Called from sync contexts too; must not raise there.
    WhisperService.load("best")
    WhisperService._arm_evictor()
    assert WhisperService._evict_task is None
