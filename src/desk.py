"""Fiches lues dans record/ et messages/. Le dashboard n'écrit rien."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_TOOL = re.compile(r"^\d{2}:\d{2}:\d{2}  Outil (\S+) — (.*)$")
_RESUME = "Résumé : "


@dataclass(frozen=True)
class Reservation:
    code: str
    guest: str
    room: str


@dataclass(frozen=True)
class Transfer:
    unfinished: bool


@dataclass(frozen=True)
class Callback:
    pass


@dataclass(frozen=True)
class Quote:
    pass


@dataclass(frozen=True)
class ToolFailure:
    tool: str
    detail: str


Issue = Reservation | Transfer | Callback | Quote | ToolFailure


@dataclass(frozen=True)
class CallCard:
    path: Path
    started: datetime
    caller: str
    room: str
    console: bool
    resume: str | None
    transcript: str
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class DeskMessage:
    path: Path
    when: datetime
    name: str
    phone: str
    reason: str


@dataclass(frozen=True)
class Span:
    start: date
    end: date

    @staticmethod
    def of(start: date, end: date) -> Span:
        if start <= end:
            return Span(start, end)
        return Span(end, start)


@dataclass(frozen=True)
class DayDesk:
    span: Span
    calls: tuple[CallCard, ...]
    messages: tuple[DeskMessage, ...]

    @property
    def reservation_count(self) -> int:
        return sum(1 for call in self.calls if _has(call, Reservation))

    @property
    def transfer_count(self) -> int:
        return sum(1 for call in self.calls if _has(call, Transfer))

    @property
    def missing_resume_count(self) -> int:
        return sum(1 for call in self.calls if call.resume is None)


def read_document(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def save_document(path: Path, text: str) -> None:
    if not path.is_file():
        raise OSError("document absent")
    path.write_text(text, encoding="utf-8", newline="\n")


def record_root() -> Path:
    return Path(__file__).resolve().parent.parent / "record"


def messages_root() -> Path:
    configured = os.environ.get("MESSAGES_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "messages"


def load_day(
    span: Span,
    records: Path | None = None,
    messages: Path | None = None,
) -> DayDesk:
    record_dir = record_root() if records is None else records
    message_dir = messages_root() if messages is None else messages
    calls = tuple(
        sorted(
            (
                card
                for card in _read_calls(record_dir)
                if span.start <= card.started.date() <= span.end
            ),
            key=lambda card: card.started,
            reverse=True,
        )
    )
    notes = tuple(
        sorted(
            (
                note
                for note in _read_messages(message_dir)
                if span.start <= note.when.date() <= span.end
            ),
            key=lambda note: note.when,
            reverse=True,
        )
    )
    return DayDesk(span, calls, notes)


def parse_call(text: str, path: Path) -> CallCard | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "Hôtel Panorama":
        return None
    header: dict[str, str] = {}
    body_at = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line == "":
            body_at = index + 1
            break
        key, separator, value = line.partition(" : ")
        if separator:
            header[key] = value
    started = _clock(header.get("Début", ""), "%Y-%m-%d %H:%M:%S")
    if started is None:
        return None
    resume: str | None = None
    turns: list[str] = []
    for line in lines[body_at:]:
        if line.startswith(_RESUME):
            resume = line.removeprefix(_RESUME).strip() or None
            continue
        if line:
            turns.append(line)
    return CallCard(
        path=path,
        started=started,
        caller=header.get("Appelant") or "(aucun)",
        room=header.get("Salle", ""),
        console=header.get("Mode") == "console",
        resume=resume,
        transcript="\n".join(turns),
        issues=_issues(turns),
    )


def parse_message(text: str, path: Path) -> DeskMessage | None:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(" : ")
        if separator:
            fields[key] = value
    when = _clock(fields.get("Date", ""), "%d/%m/%Y %H:%M")
    if when is None or "Nom" not in fields or "Téléphone" not in fields:
        return None
    return DeskMessage(
        path=path,
        when=when,
        name=fields["Nom"],
        phone=fields["Téléphone"],
        reason=fields.get("Motif", ""),
    )


def _read_calls(folder: Path) -> list[CallCard]:
    cards: list[CallCard] = []
    if not folder.is_dir():
        return cards
    for path in folder.glob("*.txt"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        card = parse_call(text, path)
        if card is not None:
            cards.append(card)
    return cards


def _read_messages(folder: Path) -> list[DeskMessage]:
    notes: list[DeskMessage] = []
    if not folder.is_dir():
        return notes
    for path in folder.glob("*.txt"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        note = parse_message(text, path)
        if note is not None:
            notes.append(note)
    return notes


def _issues(turns: list[str]) -> tuple[Issue, ...]:
    found: list[Issue] = []
    booked = False
    quoted = False
    for line in turns:
        match = _TOOL.match(line)
        if match is None:
            continue
        name, rest = match.group(1), match.group(2)
        if rest.startswith("échec — "):
            found.append(ToolFailure(name, rest.removeprefix("échec — ").split(" « ", 1)[0]))
            continue
        unfinished = rest.startswith("(sans résultat)")
        if name == "creer_reservation" and not unfinished:
            found.append(_reservation(rest))
            booked = True
            continue
        if name == "transferer_reception":
            found.append(Transfer(unfinished))
            continue
        if name == "prendre_message" and not unfinished:
            found.append(Callback())
            continue
        if name == "verifier_disponibilite_et_prix" and not unfinished:
            quoted = True
            continue
        if unfinished:
            found.append(ToolFailure(name, "sans résultat"))
    if quoted and not booked:
        found.append(Quote())
    return tuple(found)


def _reservation(rest: str) -> Reservation:
    result, separator, arguments = rest.partition(" « ")
    if separator:
        arguments = arguments.removesuffix(" »")
    payload = _object(result)
    guest_fields = _object(arguments)
    guest = " ".join(
        part
        for part in (str(guest_fields.get("prenom", "")), str(guest_fields.get("nom", "")))
        if part
    ).strip()
    code = str(payload.get("reservation", "")).strip()
    room = str(payload.get("nom_chambre", "")).strip()
    return Reservation(code, guest, room)


def _object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(value, dict):
        return value
    return {}


def _clock(value: str, pattern: str) -> datetime | None:
    try:
        return datetime.strptime(value, pattern)
    except ValueError:
        return None


def _has(call: CallCard, kind: type[Issue]) -> bool:
    return any(isinstance(issue, kind) for issue in call.issues)
