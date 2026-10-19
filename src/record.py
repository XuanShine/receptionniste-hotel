from __future__ import annotations

import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from livekit import rtc
from livekit.agents.llm import ChatItem, ChatMessage, FunctionCall, FunctionCallOutput

__all__ = ["keep_dialogue"]

logger = logging.getLogger("receptionniste.record")

_PARIS = ZoneInfo("Europe/Paris")
_KEPT = "_panorama_dialogue_kept"
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class CallerSaid:
    text: str
    at: datetime


@dataclass(frozen=True)
class ReceptionSaid:
    text: str
    at: datetime
    interrupted: bool


@dataclass(frozen=True)
class ToolCompleted:
    name: str
    arguments: str
    result: str
    failed: bool
    at: datetime


@dataclass(frozen=True)
class ToolUnfinished:
    name: str
    arguments: str
    at: datetime


Line = CallerSaid | ReceptionSaid | ToolCompleted | ToolUnfinished


@dataclass(frozen=True)
class Dialogue:
    room: str
    job_id: str
    console: bool
    started_at: datetime
    caller: str | None
    lines: tuple[Line, ...]


def hotel_clock(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, _PARIS)


def lines_of(items: Sequence[ChatItem]) -> tuple[Line, ...]:
    outputs: dict[str, FunctionCallOutput] = {}
    for item in items:
        if isinstance(item, FunctionCallOutput) and item.call_id not in outputs:
            outputs[item.call_id] = item

    consumed: set[str] = set()
    lines: list[Line] = []
    for item in items:
        if isinstance(item, ChatMessage):
            if item.role not in ("user", "assistant"):
                continue
            text = item.text_content
            if not text:
                continue
            at = hotel_clock(item.created_at)
            if item.role == "user":
                lines.append(CallerSaid(text=text, at=at))
            else:
                lines.append(
                    ReceptionSaid(text=text, at=at, interrupted=item.interrupted)
                )
        elif isinstance(item, FunctionCall):
            at = hotel_clock(item.created_at)
            output = outputs.get(item.call_id)
            if output is not None and item.call_id not in consumed:
                consumed.add(item.call_id)
                lines.append(
                    ToolCompleted(
                        name=item.name,
                        arguments=item.arguments,
                        result=output.output,
                        failed=output.is_error,
                        at=at,
                    )
                )
            else:
                lines.append(
                    ToolUnfinished(name=item.name, arguments=item.arguments, at=at)
                )
    return tuple(lines)


def _flat(text: str) -> str:
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _arguments_suffix(arguments: str) -> str:
    if arguments.strip() in ("", "{}"):
        return ""
    return f" « {_flat(arguments)} »"


def _turn(line: Line) -> str:
    clock = line.at.strftime("%H:%M:%S")
    if isinstance(line, CallerSaid):
        body = f"Appelant — {_flat(line.text)}"
    elif isinstance(line, ReceptionSaid):
        text = _flat(line.text)
        if line.interrupted:
            text = f"{text} (interrompu)"
        body = f"Réception — {text}"
    elif isinstance(line, ToolCompleted):
        name = _flat(line.name)
        result = _flat(line.result)
        if line.failed:
            body = f"Outil {name} — échec — {result}"
        else:
            body = f"Outil {name} — {result}"
        body += _arguments_suffix(line.arguments)
    else:
        body = f"Outil {_flat(line.name)} — (sans résultat)"
        body += _arguments_suffix(line.arguments)
    return f"{clock}  {body}"


def render(dialogue: Dialogue) -> str:
    caller = "(aucun)" if dialogue.caller is None else dialogue.caller
    mode = "console" if dialogue.console else "appel"
    parts = [
        "Hôtel Panorama",
        f"Début : {dialogue.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Salle : {dialogue.room}",
        f"Appelant : {caller}",
        f"Mode : {mode}",
        "",
    ]
    parts.extend(_turn(line) for line in dialogue.lines)
    return "\n".join(parts) + "\n"


def _record_root() -> Path:
    return Path(__file__).resolve().parent.parent / "record"


def _sanitize(value: str) -> str:
    return _UNSAFE_SEGMENT.sub("_", value)


def _screenplay_path(dialogue: Dialogue, root: Path) -> Path:
    stamp = dialogue.started_at.strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{stamp}_{_sanitize(dialogue.room)}_{_sanitize(dialogue.job_id)}.txt"
    return root / name


def _commit(dialogue: Dialogue, root: Path | None = None) -> Path:
    directory = _record_root() if root is None else root
    directory.mkdir(parents=True, exist_ok=True)
    path = _screenplay_path(dialogue, directory)
    temporary = directory / f".{path.name}.{secrets.token_hex(8)}.tmp"
    temporary.write_text(render(dialogue), encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


def _caller_label(participant: rtc.RemoteParticipant) -> str | None:
    if participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return None
    phone = participant.attributes.get("sip.phoneNumber", "").strip()
    label = phone or participant.identity
    return label or None


class _HistoryView(Protocol):
    items: Sequence[ChatItem]


class _SessionView(Protocol):
    history: _HistoryView


class _CallerView(Protocol):
    kind: object
    attributes: Mapping[str, str]
    identity: str


class _RoomView(Protocol):
    name: str
    remote_participants: Mapping[str, _CallerView]

    def on(self, event: str, callback: Callable[..., object]) -> None: ...


class _JobView(Protocol):
    id: str


class _ContextView(Protocol):
    room: _RoomView
    job: _JobView

    def is_fake_job(self) -> bool: ...

    def add_shutdown_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None: ...


def keep_dialogue(session: _SessionView, ctx: _ContextView) -> None:
    """Attach one shutdown write for this session. A second call returns."""
    if getattr(session, _KEPT, False):
        return
    setattr(session, _KEPT, True)

    room = ctx.room.name
    job_id = ctx.job.id
    console = ctx.is_fake_job()
    started_at = hotel_clock(time.time())
    caller: list[str | None] = [None]

    def remember(participant: rtc.RemoteParticipant) -> None:
        if caller[0] is not None:
            return
        label = _caller_label(participant)
        if label is not None:
            caller[0] = label

    for participant in ctx.room.remote_participants.values():
        remember(participant)
    ctx.room.on("participant_connected", remember)

    async def write_screenplay() -> None:
        items = list(session.history.items)
        dialogue = Dialogue(
            room=room,
            job_id=job_id,
            console=console,
            started_at=started_at,
            caller=caller[0],
            lines=lines_of(items),
        )
        try:
            _commit(dialogue, root=_record_root())
        except OSError:
            logger.exception("écriture du dialogue impossible")

    ctx.add_shutdown_callback(write_screenplay)
