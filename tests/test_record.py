import asyncio
import sys
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit import rtc
from livekit.agents.llm import ChatMessage, FunctionCall, FunctionCallOutput

import record

PARIS = ZoneInfo("Europe/Paris")

SCREENPLAY = """\
Hôtel Panorama
Début : 2026-10-20 22:14:03
Salle : call-AJ_8f3c
Appelant : +33612345678
Mode : appel

22:14:03  Réception — Hôtel Panorama Bonjour, comment puis-je vous aider ?
22:14:11  Appelant — Je voudrais parler à quelqu'un.
22:14:16  Réception — Je vous transfère à un réceptionniste, un instant.
22:14:18  Outil transferer_vers_humain — Transfert lancé. Ne dis plus rien.
"""


def at(hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 10, 20, hour, minute, second, tzinfo=PARIS)


def transfer_lines() -> tuple[record.Line, ...]:
    return (
        record.ReceptionSaid(
            text="Hôtel Panorama Bonjour, comment puis-je vous aider ?",
            at=at(22, 14, 3),
            interrupted=False,
        ),
        record.CallerSaid(text="Je voudrais parler à quelqu'un.", at=at(22, 14, 11)),
        record.ReceptionSaid(
            text="Je vous transfère à un réceptionniste, un instant.",
            at=at(22, 14, 16),
            interrupted=False,
        ),
        record.ToolCompleted(
            name="transferer_vers_humain",
            arguments="{}",
            result="Transfert lancé. Ne dis plus rien.",
            failed=False,
            at=at(22, 14, 18),
        ),
    )


def dialogue(
    *,
    room: str = "call-AJ_8f3c",
    job_id: str = "job_1",
    console: bool = False,
    started_at: datetime | None = None,
    caller: str | None = "+33612345678",
    lines: tuple[record.Line, ...] | None = None,
) -> record.Dialogue:
    return record.Dialogue(
        room=room,
        job_id=job_id,
        console=console,
        started_at=at(22, 14, 3) if started_at is None else started_at,
        caller=caller,
        lines=transfer_lines() if lines is None else lines,
    )


def transfer_items() -> list[ChatMessage | FunctionCall | FunctionCallOutput]:
    def epoch(moment: datetime) -> float:
        return moment.timestamp()

    return [
        ChatMessage(
            role="system",
            content=["consigne interne"],
            created_at=epoch(at(22, 14, 3)),
        ),
        ChatMessage(
            role="assistant",
            content=["Hôtel Panorama Bonjour, comment puis-je vous aider ?"],
            created_at=epoch(at(22, 14, 3)),
        ),
        ChatMessage(
            role="user",
            content=["Je voudrais parler à quelqu'un."],
            created_at=epoch(at(22, 14, 11)),
        ),
        ChatMessage(role="user", content=[], created_at=epoch(at(22, 14, 12))),
        ChatMessage(
            role="assistant",
            content=["Je vous transfère à un réceptionniste, un instant."],
            created_at=epoch(at(22, 14, 16)),
        ),
        FunctionCall(
            call_id="c1",
            name="transferer_vers_humain",
            arguments="{}",
            created_at=epoch(at(22, 14, 18)),
        ),
        FunctionCallOutput(
            call_id="c1",
            name="transferer_vers_humain",
            output="Transfert lancé. Ne dis plus rien.",
            is_error=False,
            created_at=epoch(at(22, 14, 18)),
        ),
        FunctionCallOutput(
            call_id="absent",
            name="autre",
            output="orphelin",
            is_error=False,
            created_at=epoch(at(22, 14, 19)),
        ),
    ]


class _Job:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class _Room:
    def __init__(self, name: str) -> None:
        self.name = name
        self.remote_participants: dict[str, _Participant] = {}
        self.events: list[tuple[str, object]] = []

    def on(self, event: str, callback: object) -> None:
        self.events.append((event, callback))


class _Participant:
    def __init__(self, phone: str, identity: str) -> None:
        self.kind = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        self.attributes = {"sip.phoneNumber": phone}
        self.identity = identity


class _Context:
    def __init__(self, *, console: bool = False) -> None:
        self.room = _Room("call-AJ_8f3c")
        self.job = _Job("job_1")
        self.console = console
        self.callbacks: list[Callable[[], Awaitable[None]]] = []

    def is_fake_job(self) -> bool:
        return self.console

    def add_shutdown_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        self.callbacks.append(callback)


class _History:
    def __init__(self, items: list[object]) -> None:
        self.items = items


class _Session:
    def __init__(self, items: list[object]) -> None:
        self.history = _History(items)


class RecordTest(unittest.TestCase):
    def test_transfer_screenplay(self) -> None:
        self.assertEqual(record.render(dialogue()), SCREENPLAY)

    def test_interrupted_reception_line(self) -> None:
        script = record.render(
            dialogue(
                lines=(
                    record.ReceptionSaid(
                        text="Un instant.",
                        at=at(22, 14, 16),
                        interrupted=True,
                    ),
                )
            )
        )
        line = next(row for row in script.splitlines() if "Réception" in row)
        self.assertTrue(line.endswith(" (interrompu)"))

    def test_failed_tool(self) -> None:
        script = record.render(
            dialogue(
                lines=(
                    record.ToolCompleted(
                        name="transferer_vers_humain",
                        arguments="{}",
                        result="Le transfert a échoué.",
                        failed=True,
                        at=at(22, 14, 18),
                    ),
                )
            )
        )
        self.assertIn(
            "Outil transferer_vers_humain — échec — Le transfert a échoué.",
            script,
        )

    def test_unfinished_tool(self) -> None:
        script = record.render(
            dialogue(
                lines=(
                    record.ToolUnfinished(
                        name="transferer_vers_humain",
                        arguments="{}",
                        at=at(22, 14, 18),
                    ),
                )
            )
        )
        self.assertIn("Outil transferer_vers_humain — (sans résultat)", script)
        self.assertNotIn("«", script)

    def test_console_header(self) -> None:
        script = record.render(dialogue(console=True, caller=None, lines=()))
        self.assertIn("Mode : console", script)
        self.assertIn("Appelant : (aucun)", script)

    def test_header_without_lines(self) -> None:
        script = record.render(dialogue(lines=()))
        self.assertEqual(script, SCREENPLAY.split("22:14:03  Réception")[0])
        self.assertNotIn("Réception —", script)
        self.assertNotIn("Outil ", script)

    def test_commit_twice_leaves_one_file(self) -> None:
        script = dialogue()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = record._commit(script, root=root)
            second = record._commit(script, root=root)
            files = list(root.iterdir())
            self.assertEqual(first, second)
            self.assertEqual(files, [first])
            self.assertEqual(first.read_bytes(), record.render(script).encode("utf-8"))
            self.assertEqual(
                first.name,
                "2026-10-20_22-14-03_call-AJ_8f3c_job_1.txt",
            )

    def test_repeat_call_id_stays_unfinished(self) -> None:
        moment = at(22, 14, 18)
        epoch = moment.timestamp()
        items = [
            FunctionCall(
                call_id="c1",
                name="transferer_vers_humain",
                arguments="{}",
                created_at=epoch,
            ),
            FunctionCallOutput(
                call_id="c1",
                name="transferer_vers_humain",
                output="Transfert lancé.",
                is_error=False,
                created_at=epoch,
            ),
            FunctionCall(
                call_id="c1",
                name="transferer_vers_humain",
                arguments="{}",
                created_at=epoch,
            ),
            FunctionCallOutput(
                call_id="absent",
                name="autre",
                output="orphelin",
                is_error=False,
                created_at=epoch,
            ),
        ]
        script = record.render(dialogue(lines=record.lines_of(items)))
        self.assertEqual(script.count("Outil transferer_vers_humain"), 2)
        self.assertIn("Outil transferer_vers_humain — Transfert lancé.", script)
        self.assertIn("Outil transferer_vers_humain — (sans résultat)", script)
        self.assertNotIn("orphelin", script)

    def test_lines_of_transfer_matches_screenplay(self) -> None:
        script = record.render(dialogue(lines=record.lines_of(transfer_items())))
        self.assertEqual(script, SCREENPLAY)

    def test_keep_dialogue_writes_one_file(self) -> None:
        moment = at(22, 14, 11)
        session = _Session(
            [
                ChatMessage(
                    role="user",
                    content=["Je voudrais parler à quelqu'un."],
                    created_at=moment.timestamp(),
                )
            ]
        )
        ctx = _Context()
        ctx.room.remote_participants["sip"] = _Participant(
            "+33612345678", "sip_+33612345678"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(record, "_record_root", return_value=root):
                record.keep_dialogue(session, ctx)
                record.keep_dialogue(session, ctx)
                self.assertEqual(len(ctx.callbacks), 1)
                self.assertEqual(
                    [event for event, _callback in ctx.room.events],
                    ["participant_connected"],
                )
                asyncio.run(ctx.callbacks[0]())
            files = list(root.iterdir())
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("Hôtel Panorama", text)
            self.assertIn("Appelant : +33612345678", text)
            self.assertIn("Appelant — Je voudrais parler à quelqu'un.", text)
            self.assertIn("Salle : call-AJ_8f3c", text)
            self.assertTrue(files[0].name.endswith("_call-AJ_8f3c_job_1.txt"))


if __name__ == "__main__":
    unittest.main()
