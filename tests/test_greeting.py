"""Le WAV d'annonce, sans lecture SIP."""

import json
import wave
from pathlib import Path

import pytest

from reception.errors import ConfigError
from reception.greeting import DEFAULT_GREETING_PATH, GREETING_TEXT, load_greeting


def test_phrase_v1_exacte():
    assert GREETING_TEXT == "Bonjour, je suis le réceptionniste de l'Hôtel Panorama"


def test_committed_greeting_is_playable_pcm():
    greeting = load_greeting(DEFAULT_GREETING_PATH)
    assert greeting.text == GREETING_TEXT
    assert greeting.channels == 1
    assert greeting.sample_width == 2
    assert greeting.sample_rate == 16000
    assert 2.0 <= greeting.duration_s <= 8.0

    manifest = json.loads(DEFAULT_GREETING_PATH.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest["text"] == GREETING_TEXT
    assert manifest["engine"] == "gTTS"
    assert abs(manifest["duration_s"] - greeting.duration_s) < 0.05


def test_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_greeting(tmp_path / "absent.wav")


def test_rejects_stereo_and_silence(tmp_path: Path):
    stereo = tmp_path / "stereo.wav"
    _write_wav(stereo, channels=2, frames=16000)
    with pytest.raises(ConfigError, match="mono"):
        load_greeting(stereo)

    silence = tmp_path / "silence.wav"
    _write_wav(silence, channels=1, frames=16000, sample=0)
    with pytest.raises(ConfigError, match="silencieuse"):
        load_greeting(silence)


def test_manifest_phrase_mismatch(tmp_path: Path):
    path = tmp_path / "custom.wav"
    _write_wav(path, channels=1, frames=16000, sample=1000)
    path.with_suffix(".json").write_text(
        json.dumps({"text": "autre phrase"}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="manifeste"):
        load_greeting(path)


def _write_wav(path: Path, *, channels: int, frames: int, sample: int = 0) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        payload = (sample.to_bytes(2, "little", signed=True)) * channels
        handle.writeframes(payload * frames)
