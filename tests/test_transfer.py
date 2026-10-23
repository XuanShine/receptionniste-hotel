import inspect
import sys
import unittest
from datetime import date, timedelta
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


class _Numbered:
    def __init__(self, kind: int, attributes: dict[str, str] | None) -> None:
        self.kind = kind
        self.identity = "sip-caller"
        if attributes is not None:
            self.attributes = attributes


def _sip(attributes: dict[str, str] | None) -> _Numbered:
    return _Numbered(rtc.ParticipantKind.PARTICIPANT_KIND_SIP, attributes)


class CallerPhoneTest(unittest.TestCase):
    def test_missing_attribute_is_unknown(self) -> None:
        self.assertIsNone(transfer.caller_phone([_sip(None)]))
        self.assertIsNone(transfer.caller_phone([_sip({})]))

    def test_empty_attribute_is_unknown(self) -> None:
        self.assertIsNone(transfer.caller_phone([_sip({"sip.phoneNumber": ""})]))
        self.assertIsNone(transfer.caller_phone([_sip({"sip.phoneNumber": "   "})]))

    def test_hotel_number_is_unknown(self) -> None:
        raw_numbers = (
            "0033422480521",
            "+33422480521",
            "0422480521",
            "sip:+33422480521",
            "tel:0422480521",
            "sip:0033422480521",
            "tel:+33422480521",
            "SIP:0033422480521",
            "sip:+33422480521@hotelpanorama.on3cx.fr",
        )
        for raw in raw_numbers:
            with self.subTest(raw=raw):
                self.assertIsNone(transfer.caller_phone([_sip({"sip.phoneNumber": raw})]))

    def test_real_number(self) -> None:
        self.assertEqual(
            transfer.caller_phone([_sip({"sip.phoneNumber": "+33612345678"})]),
            "+33612345678",
        )
        self.assertEqual(
            transfer.caller_phone([_sip({"sip.phoneNumber": "sip:+33612345678"})]),
            "+33612345678",
        )
        self.assertEqual(
            transfer.caller_phone([_sip({"sip.phoneNumber": "tel:0612345678"})]),
            "0612345678",
        )

    def test_real_invite_uses_the_remote_party_display_name(self) -> None:
        caller = _sip(
            {
                "sip.phoneNumber": "0033422480521",
                "sip.rpid": (
                    '"0651216491"'
                    "<sip:0033422480521@65m77j3flj8.sip.livekit.cloud:5060>;party=calling"
                ),
                "sip.pai": '"0651216491"<sip:anonymous>',
            }
        )
        self.assertEqual(transfer.caller_phone([caller]), "0651216491")

    def test_asserted_identity_display_name_when_remote_party_is_missing(self) -> None:
        caller = _sip(
            {
                "sip.phoneNumber": "0033422480521",
                "sip.pai": '"0651216491"<sip:anonymous>',
            }
        )
        self.assertEqual(transfer.caller_phone([caller]), "0651216491")

    def test_plus_display_name_is_kept(self) -> None:
        caller = _sip(
            {
                "sip.phoneNumber": "0033422480521",
                "sip.rpid": (
                    '"+33651216491"'
                    "<sip:0033422480521@65m77j3flj8.sip.livekit.cloud:5060>;party=calling"
                ),
                "sip.pai": '"anonymous"<sip:anonymous>',
            }
        )
        self.assertEqual(transfer.caller_phone([caller]), "+33651216491")

    def test_hotel_or_anonymous_display_name_falls_through(self) -> None:
        hotel_rpid = _sip(
            {
                "sip.phoneNumber": "0033422480521",
                "sip.rpid": '"0033422480521"<sip:0033422480521@65m77j3flj8.sip.livekit.cloud:5060>',
                "sip.pai": '"+33651216491"<sip:anonymous>',
            }
        )
        anonymous_rpid = _sip(
            {
                "sip.phoneNumber": "+33422480521",
                "sip.rpid": '"anonymous"<sip:0033422480521@65m77j3flj8.sip.livekit.cloud:5060>',
                "sip.pai": '"0651216491"<sip:anonymous>',
            }
        )
        self.assertEqual(transfer.caller_phone([hotel_rpid]), "+33651216491")
        self.assertEqual(transfer.caller_phone([anonymous_rpid]), "0651216491")

    def test_hotel_headers_stay_unknown(self) -> None:
        caller = _sip(
            {
                "sip.phoneNumber": "0033422480521",
                "sip.rpid": '"0422480521"<sip:0033422480521@65m77j3flj8.sip.livekit.cloud:5060>',
                "sip.pai": '"anonymous"<sip:anonymous>',
            }
        )
        self.assertIsNone(transfer.caller_phone([caller]))

    def test_hotel_number_does_not_change_the_transfer_identity(self) -> None:
        caller = _sip({"sip.phoneNumber": "0033422480521"})
        self.assertEqual(transfer.find_sip_caller([caller]), "sip-caller")
        self.assertIsNone(transfer.caller_phone([caller]))


class _CallRoom:
    def __init__(self, participants: list[_Numbered]) -> None:
        self.remote_participants = {person.identity: person for person in participants}


class _PhoneJob:
    def __init__(self, *, fake: bool, participants: list[_Numbered]) -> None:
        self._fake = fake
        self.connected = False
        self.room = _CallRoom(participants)

    def is_fake_job(self) -> bool:
        return self._fake

    async def connect(self) -> None:
        self.connected = True


class IncomingCallerPhoneTest(unittest.IsolatedAsyncioTestCase):
    async def test_console_has_no_caller_number(self) -> None:
        ctx = _PhoneJob(
            fake=True,
            participants=[_sip({"sip.phoneNumber": "+33612345678"})],
        )
        self.assertIsNone(await agent.incoming_caller_phone(ctx))
        self.assertFalse(ctx.connected)

    async def test_call_reads_the_number_after_connect(self) -> None:
        ctx = _PhoneJob(
            fake=False,
            participants=[_sip({"sip.phoneNumber": "tel:+33612345678"})],
        )
        self.assertEqual(await agent.incoming_caller_phone(ctx), "+33612345678")
        self.assertTrue(ctx.connected)


class CallerInstructionsTest(unittest.TestCase):
    def test_known_number_is_offered(self) -> None:
        receptionist = agent.Receptionniste(caller_phone="+33612345678")
        text = receptionist.instructions
        self.assertIn("+33612345678", text)
        self.assertIn(
            "Est-ce que je peux utiliser le numéro depuis lequel vous appelez ?",
            text,
        )

    def test_unknown_number_asks_for_the_phone(self) -> None:
        receptionist = agent.Receptionniste()
        text = receptionist.instructions
        self.assertIn("Le numéro de l'appelant est inconnu.", text)
        self.assertIn("Quel numéro de téléphone pour vous joindre ?", text)
        self.assertNotIn("0033422480521", text)


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
        self.assertNotIn(transfer.RECEPTION_PHONE, outcome.result); self.assertIn("message", outcome.result)

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
        self.assertNotIn(transfer.RECEPTION_PHONE, outcome.result); self.assertIn("message", outcome.result)


async def _refuse(identity: str, target: str) -> None:
    raise RuntimeError("403")


class AgentToolTest(unittest.IsolatedAsyncioTestCase):
    def test_tool_takes_no_destination_argument(self) -> None:
        signature = inspect.signature(agent.Receptionniste().transferer_reception)
        self.assertEqual(list(signature.parameters), ["ctx"])
        instructions = (SRC / "instructions.md").read_text(encoding="utf-8")
        self.assertIn("transferer_reception", instructions)
        self.assertIn(transfer.TRANSFER_PHRASE, instructions)
        stamp = date.today().strftime("%d/%m/%Y")
        self.assertIn(stamp, agent.Receptionniste().instructions)

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
        self.assertNotIn(transfer.RECEPTION_PHONE, first); self.assertIn("message", first)
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
        self.assertNotIn(transfer.RECEPTION_PHONE, result); self.assertIn("message", result)

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
