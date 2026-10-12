#!/usr/bin/env python3
"""Régénère l'annonce v1 (WAV PCM 16 kHz mono).

Ordre des moteurs :
1. gTTS (bibliothèque Python, voix française de Google Translate) — nécessite
   le réseau et ffmpeg.
2. espeak-ng, si gTTS échoue — hors ligne, voix synthétique, ffmpeg ensuite.

Le processus téléphonique ne rappelle pas ces moteurs : il lit le WAV produit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reception.greeting import DEFAULT_GREETING_PATH, GREETING_TEXT  # noqa: E402

MANIFEST_PATH = DEFAULT_GREETING_PATH.with_suffix(".json")


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("ffmpeg est requis pour produire le WAV final.", file=sys.stderr)
        return 1

    destination = DEFAULT_GREETING_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw"
        engine, engine_note = _synthesize(raw)
        produced = _find_produced(raw)
        if produced is None:
            print("Aucun fichier audio n'a été produit.", file=sys.stderr)
            return 1
        _to_telephony_wav(produced, destination)

    duration_s = _duration(destination)
    manifest = {
        "text": GREETING_TEXT,
        "engine": engine,
        "lang": "fr",
        "format": "wav",
        "codec": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "sample_width_bytes": 2,
        "duration_s": round(duration_s, 3),
        "note": engine_note,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Annonce écrite : {destination} ({duration_s:.2f} s, {engine})")
    return 0


def _synthesize(raw_prefix: Path) -> tuple[str, str]:
    mp3_path = raw_prefix.with_suffix(".mp3")
    try:
        from gtts import gTTS

        gTTS(GREETING_TEXT, lang="fr").save(str(mp3_path))
    except Exception as exc:  # réseau, quota, ou bibliothèque absente
        print(f"gTTS indisponible ({exc}). Repli sur espeak-ng.", file=sys.stderr)
    else:
        return (
            "gTTS",
            "Généré par gTTS (français), converti en WAV PCM 16 kHz mono "
            "avec ffmpeg. Le processus téléphonique lit ce fichier et "
            "n'appelle pas le service TTS à chaque appel.",
        )

    wav_path = raw_prefix.with_suffix(".wav")
    if shutil.which("espeak-ng") is None:
        raise SystemExit("Ni gTTS ni espeak-ng ne sont disponibles.")
    subprocess.run(
        [
            "espeak-ng",
            "-v",
            "fr-fr",
            "-s",
            "140",
            "-w",
            str(wav_path),
            GREETING_TEXT,
        ],
        check=True,
    )
    return (
        "espeak-ng",
        "Généré hors ligne par espeak-ng (voix fr-fr), converti en WAV PCM "
        "16 kHz mono avec ffmpeg.",
    )


def _find_produced(raw_prefix: Path) -> Path | None:
    for suffix in (".mp3", ".wav"):
        candidate = raw_prefix.with_suffix(suffix)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _to_telephony_wav(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(destination),
        ],
        check=True,
    )


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


if __name__ == "__main__":
    raise SystemExit(main())
