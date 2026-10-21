"""Transfert d'un appelant SIP vers le poste 10, par REFER."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from livekit import api, rtc
from livekit.agents.llm import ToolResult

logger = logging.getLogger("receptionniste.transfer")

TRANSFER_PHRASE = "Très bien, je vous transfère à mon collègue."
RECEPTION_PHONE = "04 93 36 80 80"
DEFAULT_EXTENSION = "10"
DEFAULT_PBX_HOST = "hotelpanorama.on3cx.fr"
RINGING_TIMEOUT = timedelta(seconds=60)

_EXPLICIT_URI = re.compile(r"^(?:sip|tel):\S+$")
_EXTENSION = re.compile(r"^[0-9]{1,6}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]+$")


class _Playable(Protocol):
    def wait_for_playout(self) -> Awaitable[None]: ...


class _SipLike(Protocol):
    kind: int
    identity: str


@dataclass(frozen=True)
class TransferOutcome:
    result: ToolResult | str
    connected: bool


def refer_target(
    *,
    explicit: str | None = None,
    extension: str = DEFAULT_EXTENSION,
    host: str = DEFAULT_PBX_HOST,
) -> str:
    """URI Refer-To. Un URI explicite gagne, sinon sip:<poste>@<pbx>."""
    if explicit:
        target = explicit.strip()
        if not _EXPLICIT_URI.fullmatch(target):
            raise ValueError("SIP_TRANSFER_TO doit être un URI sip: ou tel: sans espace")
        return target

    extension = extension.strip()
    host = host.strip()
    if not _EXTENSION.fullmatch(extension):
        raise ValueError("Le poste de transfert doit être un numéro")
    if not _HOST.fullmatch(host):
        raise ValueError("L'hôte SIP de transfert est invalide")
    return f"sip:{extension}@{host}"


def refer_target_from_env() -> str:
    explicit = os.environ.get("SIP_TRANSFER_TO", "").strip()
    return refer_target(
        explicit=explicit or None,
        extension=os.environ.get("SIP_TRANSFER_EXTENSION", DEFAULT_EXTENSION),
        host=os.environ.get("SIP_TRANSFER_HOST", DEFAULT_PBX_HOST),
    )


def dial_number(transfer_to: str, extension: str = DEFAULT_EXTENSION) -> str:
    """Numéro à composer pour le repli, dérivé de l'URI REFER."""
    if transfer_to.startswith("sip:"):
        user = transfer_to[4:].split("@", 1)[0].split(";", 1)[0]
        if user:
            return user
    if transfer_to.startswith("tel:"):
        number = transfer_to[4:]
        if number:
            return number
    return extension


def find_sip_caller(participants: Iterable[_SipLike]) -> str | None:
    for participant in participants:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP and participant.identity:
            return participant.identity
    return None


_HOTEL_NUMBER = "0033422480521"
_SCHEME = re.compile(r"^(?:sip|tel):", re.IGNORECASE)
_QUOTED_NAME = re.compile(r'"([^"]*)"')
_NUMBER_SOURCES = ("sip.rpid", "sip.pai", "sip.phoneNumber")


def caller_phone(participants: Iterable[object]) -> str | None:
    """Numéro du client, ou None s'il est absent, anonyme, ou celui de l'hôtel.

    Le display name de sip.rpid gagne, puis celui de sip.pai, puis sip.phoneNumber.
    """
    for participant in participants:
        if getattr(participant, "kind", None) != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            continue
        for raw in _caller_sources(participant):
            number = _usable_number(raw)
            if number is not None:
                return number
        return None
    return None


def _caller_sources(participant: object) -> tuple[str, str, str]:
    attributes = _attributes(participant)
    rpid, pai, phone = (_text(attributes.get(key, "")) for key in _NUMBER_SOURCES)
    return (_quoted_name(rpid), _quoted_name(pai), phone)


def _attributes(participant: object) -> Mapping[str, str]:
    attributes = getattr(participant, "attributes", None)
    if not isinstance(attributes, Mapping):
        return {}
    return attributes


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _quoted_name(header: str) -> str:
    match = _QUOTED_NAME.search(header)
    if match is None:
        return ""
    return match.group(1).strip()


def _presented_number(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    text = _SCHEME.sub("", text, count=1).strip()
    text = text.split("@", 1)[0].strip()
    if not text or not any(ch.isdigit() for ch in text):
        return None
    return text


def _usable_number(raw: str) -> str | None:
    number = _presented_number(raw)
    if number is None or number.casefold() == "anonymous" or _is_hotel(number):
        return None
    return number


def _is_hotel(number: str) -> bool:
    return _canonical(number) == _canonical(_HOTEL_NUMBER)


def _canonical(number: str) -> str:
    body = _SCHEME.sub("", number.strip(), count=1).split("@", 1)[0].strip()
    digits = "".join(ch for ch in body if ch.isdigit())
    if body.startswith("+") or digits.startswith("00"):
        if digits.startswith("00"):
            digits = digits[2:]
        return digits
    if len(digits) == 10 and digits.startswith("0"):
        return "33" + digits[1:]
    return digits


def outbound_trunk_id() -> str:
    return os.environ.get("SIP_OUTBOUND_TRUNK_ID", "").strip()


async def refer_sip_participant(
    lkapi: Any,
    *,
    room_name: str,
    participant_identity: str,
    transfer_to: str,
) -> None:
    """Envoie un SIP REFER. LiveKit attend la réponse du poste, ou lève."""
    await lkapi.sip.transfer_sip_participant(
        api.TransferSIPParticipantRequest(
            room_name=room_name,
            participant_identity=participant_identity,
            transfer_to=transfer_to,
            play_dialtone=True,
            ringing_timeout=RINGING_TIMEOUT,
        )
    )


async def bridge_sip_participant(
    lkapi: Any,
    *,
    room_name: str,
    trunk_id: str,
    dial: str,
) -> None:
    """Repli : un nouvel appel vers le poste rejoint la room de l'appelant."""
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=trunk_id,
            sip_call_to=dial,
            room_name=room_name,
            participant_identity="poste-10",
            participant_name="Réception",
            play_dialtone=True,
            wait_until_answered=True,
            ringing_timeout=RINGING_TIMEOUT,
        )
    )


def _failure(detail: str) -> str:
    logger.error("transfert vers le poste 10 échoué : %s", detail)
    return (
        "Le transfert vers le poste 10 a échoué. "
        "Dis au client, brièvement, que le transfert n'a pas abouti "
        f"et qu'il peut joindre la réception au {RECEPTION_PHONE}."
    )


def _silence(output: str) -> ToolResult:
    return ToolResult(output, reply_required=False)


async def perform_transfer(
    *,
    say: Callable[[str], _Playable],
    caller_identity: str | None,
    transfer_to: str,
    refer: Callable[[str, str], Awaitable[None]],
    bridge: Callable[[], Awaitable[None]] | None = None,
) -> TransferOutcome:
    """Dit la phrase, puis REFER. Si le REFER échoue, compose le poste si possible."""
    try:
        speech = say(TRANSFER_PHRASE)
        await speech.wait_for_playout()
    except Exception:
        logger.exception("phrase de transfert non jouée")

    if caller_identity is None:
        return TransferOutcome(
            result=(
                "Pas d'appelant SIP dans la room : le REFER est impossible. "
                f"Invite le client à appeler la réception au {RECEPTION_PHONE}."
            ),
            connected=False,
        )

    try:
        await refer(caller_identity, transfer_to)
    except Exception as exc:
        logger.exception("SIP REFER refusé")
        if bridge is None:
            return TransferOutcome(result=_failure(_error_text(exc)), connected=False)
        try:
            await bridge()
        except Exception as bridge_exc:
            logger.exception("repli vers le poste 10 échoué")
            return TransferOutcome(
                result=_failure(f"{_error_text(exc)} ; repli : {_error_text(bridge_exc)}"),
                connected=False,
            )
        return TransferOutcome(
            result=_silence(
                "Le poste 10 a décroché et rejoint l'appel. Ne dis plus rien."
            ),
            connected=True,
        )

    return TransferOutcome(
        result=_silence("Appel transféré au poste 10 par SIP REFER. Ne dis plus rien."),
        connected=True,
    )


def _error_text(exc: BaseException) -> str:
    code = getattr(exc, "sip_status_code", None)
    status = getattr(exc, "sip_status", None)
    if code:
        return f"SIP {code} {status or ''}".strip()
    return str(exc)
