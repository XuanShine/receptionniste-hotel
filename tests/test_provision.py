import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from livekit import api  # noqa: E402

import provision_sip  # noqa: E402

TRUNK_ID = "ST_BxA4aAxSS3y4"
ENV = {
    "LIVEKIT_URL": "wss://65m77j3flj8.livekit.cloud",
    "LIVEKIT_API_KEY": "key",
    "LIVEKIT_API_SECRET": "secret",
    "SIP_TRUNK_NAME": "3cx-panorama",
    "SIP_ALLOWED_ADDRESSES": "203.0.113.10/32",
    "SIP_NUMBERS": "+33422480521",
    "AGENT_NAME": "receptionniste",
}


def _trunk(*, headers: dict[str, str] | None = None) -> api.SIPInboundTrunkInfo:
    trunk = api.SIPInboundTrunkInfo(
        sip_trunk_id=TRUNK_ID,
        name="3cx-panorama",
        numbers=["+33422480521"],
        allowed_addresses=["203.0.113.10/32"],
    )
    if headers:
        trunk.headers_to_attributes.update(headers)
    return trunk


def _rule() -> api.SIPDispatchRuleInfo:
    return api.SIPDispatchRuleInfo(
        sip_dispatch_rule_id="SDR_test",
        name="receptionniste-inbound",
        trunk_ids=[TRUNK_ID],
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name="receptionniste")]
        ),
    )


class _Page:
    def __init__(self, items: list[object]) -> None:
        self.items = items


class _Sip:
    def __init__(self, trunks: list[api.SIPInboundTrunkInfo]) -> None:
        self.trunks = trunks
        self.rules = [_rule()]
        self.updates: list[tuple[str, api.SIPInboundTrunkInfo]] = []
        self.creates: list[api.CreateSIPInboundTrunkRequest] = []

    async def list_inbound_trunk(self, request: object) -> _Page:
        return _Page(self.trunks)

    async def list_dispatch_rule(self, request: object) -> _Page:
        return _Page(self.rules)

    async def update_inbound_trunk(
        self,
        trunk_id: str,
        trunk: api.SIPInboundTrunkInfo,
    ) -> api.SIPInboundTrunkInfo:
        self.updates.append((trunk_id, trunk))
        stored = api.SIPInboundTrunkInfo()
        stored.CopyFrom(trunk)
        stored.sip_trunk_id = trunk_id
        self.trunks = [stored]
        return stored

    async def create_inbound_trunk(
        self,
        request: api.CreateSIPInboundTrunkRequest,
    ) -> api.SIPInboundTrunkInfo:
        self.creates.append(request)
        created = api.SIPInboundTrunkInfo()
        created.CopyFrom(request.trunk)
        created.sip_trunk_id = TRUNK_ID
        self.trunks = [created]
        return created


class _API:
    def __init__(self, sip: _Sip) -> None:
        self.sip = sip

    async def aclose(self) -> None:
        return None


class CallerHeadersTest(unittest.TestCase):
    def test_update_keeps_the_trunk_and_adds_the_map(self) -> None:
        original = _trunk(headers={"X-Keep": "sip.keep"})
        updated = provision_sip.with_caller_headers(original)
        self.assertEqual(original.sip_trunk_id, TRUNK_ID)
        self.assertEqual(updated.sip_trunk_id, "")
        self.assertEqual(list(updated.numbers), ["+33422480521"])
        self.assertEqual(list(updated.allowed_addresses), ["203.0.113.10/32"])
        self.assertEqual(updated.headers_to_attributes["X-Keep"], "sip.keep")
        self.assertEqual(updated.headers_to_attributes["Remote-Party-ID"], "sip.rpid")
        self.assertEqual(
            updated.headers_to_attributes["P-Asserted-Identity"],
            "sip.pai",
        )
        self.assertTrue(provision_sip.caller_headers_ready(updated))
        self.assertFalse(provision_sip.caller_headers_ready(original))


class ProvisionMainTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_trunk_is_updated_once(self) -> None:
        sip = _Sip([_trunk()])
        with (
            patch.dict("os.environ", ENV, clear=False),
            patch.object(provision_sip.api, "LiveKitAPI", return_value=_API(sip)),
        ):
            await provision_sip.main()
            await provision_sip.main()

        self.assertEqual(len(sip.updates), 1)
        trunk_id, trunk = sip.updates[0]
        self.assertEqual(trunk_id, TRUNK_ID)
        self.assertEqual(trunk.headers_to_attributes["Remote-Party-ID"], "sip.rpid")
        self.assertEqual(trunk.headers_to_attributes["P-Asserted-Identity"], "sip.pai")
        self.assertEqual(list(trunk.numbers), ["+33422480521"])
        self.assertEqual(list(trunk.allowed_addresses), ["203.0.113.10/32"])
        self.assertEqual(sip.creates, [])

    async def test_new_trunk_is_created_with_the_map(self) -> None:
        sip = _Sip([])
        with (
            patch.dict("os.environ", ENV, clear=False),
            patch.object(provision_sip.api, "LiveKitAPI", return_value=_API(sip)),
        ):
            await provision_sip.main()

        self.assertEqual(sip.updates, [])
        self.assertEqual(len(sip.creates), 1)
        created = sip.creates[0].trunk
        self.assertEqual(created.headers_to_attributes["Remote-Party-ID"], "sip.rpid")
        self.assertEqual(created.headers_to_attributes["P-Asserted-Identity"], "sip.pai")


if __name__ == "__main__":
    unittest.main()
