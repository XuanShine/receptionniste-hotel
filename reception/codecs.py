"""Priorité des codecs négociés avec 3CX.

La roue ``pjsua2-python`` 2.15.1 ne contient pas Opus (``PJ_ENOTSUP``).
La v1 force donc les codecs téléphoniques que 3CX sait transporter sans
extension : PCMA (loi A, usage en France), puis PCMU, puis G.722.
Tout le reste est désactivé pour éviter un accord sur Speex ou iLBC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

# Priorité PJSUA : 0 désactive le codec, 255 est le maximum.
PREFERRED_CODECS: dict[str, int] = {
    "PCMA/8000/1": 255,
    "PCMU/8000/1": 254,
    "G722/16000/1": 200,
}


def codec_priority(codec_id: str) -> int:
    """Priorité v1 d'un codec. Opus et les codecs non listés valent 0."""

    return PREFERRED_CODECS.get(codec_id, 0)


def apply_codec_policy(
    set_priority: Callable[[str, int], None],
    codec_ids: Iterable[str],
) -> list[tuple[str, int]]:
    """Applique la politique et renvoie les priorités écrites."""

    applied: list[tuple[str, int]] = []
    for codec_id in codec_ids:
        priority = codec_priority(codec_id)
        set_priority(codec_id, priority)
        applied.append((codec_id, priority))
    return applied
