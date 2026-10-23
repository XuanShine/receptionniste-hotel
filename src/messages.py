"""Messages pris par la réceptionniste : un fichier texte par message."""

import re
from datetime import datetime
from pathlib import Path


def message_filename(when: datetime, phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone) or "inconnu"
    return f"{when:%Y-%m-%d %Hh%M} - {digits}.txt"


def save_message(
    folder: Path, when: datetime, name: str, phone: str, reason: str
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / message_filename(when, phone)
    stem, n = path.stem, 2
    while path.exists():
        path = folder / f"{stem} ({n}).txt"
        n += 1
    path.write_text(
        f"Date : {when:%d/%m/%Y %H:%M}\nNom : {name}\nTéléphone : {phone}\nMotif : {reason}\n",
        encoding="utf-8",
    )
    return path
