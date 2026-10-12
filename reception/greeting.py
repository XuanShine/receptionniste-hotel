"""Annonce française de la v1 et contrôle du fichier WAV."""

from __future__ import annotations

import json
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from reception.errors import ConfigError

GREETING_TEXT = "Bonjour, je suis le réceptionniste de l'Hôtel Panorama"

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GREETING_PATH = PACKAGE_ROOT / "assets" / "greeting_fr.wav"

_MIN_DURATION_S = 0.8
_MAX_DURATION_S = 15.0
_MIN_RMS = 50.0


@dataclass(frozen=True)
class Greeting:
    """Fichier WAV PCM mono prêt à être joué sur l'appel."""

    path: Path
    text: str
    duration_s: float
    sample_rate: int
    channels: int
    sample_width: int


def load_greeting(path: Path, text: str = GREETING_TEXT) -> Greeting:
    """Charge et valide le WAV. Lève ConfigError si le fichier est inutilisable."""

    if not path.is_file():
        raise ConfigError(f"Fichier d'annonce introuvable : {path}")

    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            frames = handle.readframes(frame_count)
    except wave.Error as exc:
        raise ConfigError(f"Fichier audio illisible ({path}) : {exc}") from exc

    if channels != 1:
        raise ConfigError(f"L'annonce doit être mono (canaux trouvés : {channels}).")
    if sample_width != 2:
        raise ConfigError(
            "L'annonce doit être du PCM 16 bits "
            f"(largeur d'échantillon trouvée : {sample_width})."
        )
    if sample_rate <= 0 or frame_count <= 0:
        raise ConfigError("L'annonce WAV est vide.")

    duration_s = frame_count / float(sample_rate)
    if not _MIN_DURATION_S <= duration_s <= _MAX_DURATION_S:
        raise ConfigError(
            f"Durée de l'annonce hors plage ({duration_s:.2f} s, "
            f"attendu entre {_MIN_DURATION_S:.1f} et {_MAX_DURATION_S:.0f} s)."
        )
    if _pcm16_rms(frames) < _MIN_RMS:
        raise ConfigError("L'annonce WAV est silencieuse.")

    _check_manifest(path, text)
    return Greeting(
        path=path,
        text=text,
        duration_s=duration_s,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
    )


def _check_manifest(path: Path, text: str) -> None:
    manifest_path = path.with_suffix(".json")
    if not manifest_path.is_file():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Manifeste audio illisible ({manifest_path}) : {exc}") from exc
    manifest_text = payload.get("text")
    if manifest_text != text:
        raise ConfigError(
            "Le manifeste audio ne correspond pas à la phrase v1 "
            f"({manifest_text!r})."
        )


def _pcm16_rms(frames: bytes) -> float:
    if len(frames) < 2:
        return 0.0
    usable = len(frames) - (len(frames) % 2)
    samples = struct.unpack("<" + "h" * (usable // 2), frames[:usable])
    if not samples:
        return 0.0
    square_mean = sum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(square_mean)
