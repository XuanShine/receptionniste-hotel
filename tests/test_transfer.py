import inspect
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from livekit import rtc  # noqa: E402
from livekit.agents.llm import ToolResult  # noqa: E402

import agent  # noqa: E402
import transfer  # noqa: E402


class _Speech:
    def __init__(self) -> None:
        self.played = False

    async def wait_for_playout(self) -> None:
        self.played = True


class _Session:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.shutdown_args: list[dict[str, bool]] = []

    def say(self, text: str, allow_interruptions: bool = True) -> _Speech:
        self.said.append(text)
        return _Speech()

    def shutdown(self, *, drain: bool = True) -> None:
        self.shutdown_args.append({"drain": drain})


class _Ctx:
    def __init__(self) -> None:
        self.session = _Session()


class _Participant:
    def __init__(self, kind: int, identity: str) -> None:
        self.kind = kind
        self.identity = identity


class _Room:
    def __init__(self, participants: list[_Participant]) -> None:
        self.name = "call-test"
        self.remote_participants = {item.identity: item for item in participants}


class _SipApi:
    def __init__(self, refer_error: Exception | None = None) -> None:
        self.refer_error = refer_error
        self.refers: list[object] = []
        self.bridges: list[object] = []

    async def transfer_sip_participant(self, request: object) -> None:
        self.refers.append(request)
        if self.refer_error:
            raise self.refer_error

    async def create_sip_participant(self, request: object) -> None:
        self.bridges.append(request)


class _Job:
    def __init__(self, sip: _SipApi, participants: list[_Participant]) -> None:
        self.api = type("API", (), {"sip": sip})()
        self.room = _Room(participants)


class ReferTargetTest(unittest.TestCase):
    def test_default_is_extension_10_on_the_pbx(self) -> None:
        self.assertEqual(transfer.refer_target(), "sip:10@hotelpanorama.on3cx.fr")

    def test_explicit_uri_wins(self) -> None:
        self.assertEqual(
            transfer.refer_target(explicit="tel:+33493368080"),
            "tel:+33493368080",
        )

    def test_rejects_a_uri_the_model_could_smuggle(self) -> None:
        with self.assertRaises(ValueError):
            transfer.refer_target(explicit="sip:900@evil.example\nDiversion: x")
        with self.assertRaises(ValueError):
            transfer.refer_target(extension="10;ext=900")

    def test_dial_number_uses_the_sip_user(self) -> None:
        self.assertEqual(transfer.dial_number("sip:10@hotelpanorama.on3cx.fr"), "10")


class FindCallerTest(unittest.TestCase):
    def test_skips_non_sip_participants(self) -> None:
        participants = [
            _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, "agent"),
            _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, "sip-caller"),
        ]
        self.assertEqual(transfer.find_sip_caller(participants), "sip-caller")


class PerformTransferTest(unittest.IsolatedAsyncioTestCase):
    async def test_says_the_phrase_then_refers(self) -> None:
        said: list[str] = []
        refers: list[tuple[str, str]] = []
        speech = _Speech()

        def say(text: str) -> _Speech:
            said.append(text)
            return speech

        async def refer(identity: str, target: str) -> None:
            refers.append((identity, target))

        outcome = await transfer.perform_transfer(
            say=say,
            caller_identity="sip-caller",
            transfer_to="sip:10@hotelpanorama.on3cx.fr",
            refer=refer,
        )

        self.assertEqual(said, [transfer.TRANSFER_PHRASE])
        self.assertTrue(speech.played)
        self.assertEqual(refers, [("sip-caller", "sip:10@hotelpanorama.on3cx.fr")])
        self.assertTrue(outcome.connected)
        self.assertIsInstance(outcome.result, ToolResult)
        self.assertFalse(outcome.result.reply_required)

    async def test_without_a_sip_caller_does_not_refer(self) -> None:
        called = False

        async def refer(identity: str, target: str) -> None:
            nonlocal called
            called = True

        outcome = await transfer.perform_transfer(
            say=lambda text: _Speech(),
            caller_identity=None,
            transfer_to="sip:10@hotelpanorama.on3cx.fr",
            refer=refer,
        )

        self.assertFalse(called)
        self.assertFalse(outcome.connected)
        self.assertIn(transfer.RECEPTION_PHONE, outcome.result)

    async def test_refer_failure_bridges_when_a_trunk_exists(self) -> None:
        bridged = False

        async def refer(identity: str, target: str) -> None:
            raise RuntimeError("403")

        async def bridge() -> None:
            nonlocal bridged
            bridged = True

        outcome = await transfer.perform_transfer(
            say=lambda text: _Speech(),
            caller_identity="sip-caller",
            transfer_to="sip:10@hotelpanorama.on3cx.fr",
            refer=refer,
            bridge=bridge,
        )

        self.assertTrue(bridged)
        self.assertTrue(outcome.connected)

    async def test_refer_failure_without_bridge_offers_the_phone(self) -> None:
        outcome = await transfer.perform_transfer(
            say=lambda text: _Speech(),
            caller_identity="sip-caller",
            transfer_to="sip:10@hotelpanorama.on3cx.fr",
            refer=_refuse,
        )

        self.assertFalse(outcome.connected)
        self.assertIn(transfer.RECEPTION_PHONE, outcome.result)


async def _refuse(identity: str, target: str) -> None:
    raise RuntimeError("403")


class AgentToolTest(unittest.IsolatedAsyncioTestCase):
    def test_tool_takes_no_destination_argument(self) -> None:
        signature = inspect.signature(agent.Receptionniste().transferer_reception)
        self.assertEqual(list(signature.parameters), ["ctx"])
        instructions = (SRC / "instructions.md").read_text(encoding="utf-8")
        self.assertIn("transferer_reception", instructions)
        self.assertIn(transfer.TRANSFER_PHRASE, instructions)

    async def test_tool_sends_refer_to_extension_10(self) -> None:
        sip = _SipApi()
        caller = _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, "sip-caller")
        job = _Job(sip, [caller])
        ctx = _Ctx()
        receptionist = agent.Receptionniste()

        with patch.object(agent, "get_job_context", return_value=job):
            result = await receptionist.transferer_reception(ctx)

        self.assertEqual(ctx.session.said, [transfer.TRANSFER_PHRASE])
        self.assertEqual(len(sip.refers), 1)
        request = sip.refers[0]
        self.assertEqual(request.room_name, "call-test")
        self.assertEqual(request.participant_identity, "sip-caller")
        self.assertEqual(request.transfer_to, "sip:10@hotelpanorama.on3cx.fr")
        self.assertTrue(request.play_dialtone)
        self.assertEqual(request.ringing_timeout.seconds, int(timedelta(seconds=60).total_seconds()))
        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.reply_required)
        self.assertEqual(ctx.session.shutdown_args, [{"drain": False}])

    async def test_refused_refer_can_be_retried(self) -> None:
        sip = _SipApi(refer_error=RuntimeError("403"))
        caller = _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, "sip-caller")
        job = _Job(sip, [caller])
        receptionist = agent.Receptionniste()

        with patch.object(agent, "get_job_context", return_value=job):
            first = await receptionist.transferer_reception(_Ctx())
            second = await receptionist.transferer_reception(_Ctx())

        self.assertIsInstance(first, str)
        self.assertIn(transfer.RECEPTION_PHONE, first)
        self.assertIsInstance(second, str)
        self.assertEqual(len(sip.refers), 2)

    async def test_bad_target_does_not_refer(self) -> None:
        sip = _SipApi()
        caller = _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, "sip-caller")
        job = _Job(sip, [caller])

        with (
            patch.object(agent, "get_job_context", return_value=job),
            patch.dict("os.environ", {"SIP_TRANSFER_TO": "pas-un-uri"}),
        ):
            result = await agent.Receptionniste().transferer_reception(_Ctx())

        self.assertEqual(sip.refers, [])
        self.assertIsInstance(result, str)
        self.assertIn(transfer.RECEPTION_PHONE, result)

    async def test_refused_refer_dials_extension_10(self) -> None:
        sip = _SipApi(refer_error=RuntimeError("403"))
        caller = _Participant(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, "sip-caller")
        job = _Job(sip, [caller])

        with (
            patch.object(agent, "get_job_context", return_value=job),
            patch.dict("os.environ", {"SIP_OUTBOUND_TRUNK_ID": "ST_outbound"}),
        ):
            result = await agent.Receptionniste().transferer_reception(_Ctx())

        self.assertEqual(len(sip.bridges), 1)
        self.assertEqual(sip.bridges[0].sip_trunk_id, "ST_outbound")
        self.assertEqual(sip.bridges[0].sip_call_to, "10")
        self.assertTrue(sip.bridges[0].wait_until_answered)
        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.reply_required)


if __name__ == "__main__":
    unittest.main()
