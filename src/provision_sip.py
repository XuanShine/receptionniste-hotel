"""Crée le trunk SIP entrant 3CX et la règle de dispatch, sans rien supprimer."""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from livekit import api

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROOM_PREFIX = "call-"
PAGE_SIZE = 100
CALLER_HEADERS = {
    "Remote-Party-ID": "sip.rpid",
    "P-Asserted-Identity": "sip.pai",
}


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Variable manquante : {name}", file=sys.stderr)
        raise SystemExit(1)
    return value


def csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def sip_host(livekit_url: str) -> str | None:
    host = urlparse(livekit_url).hostname or ""
    suffix = ".livekit.cloud"
    if not host.endswith(suffix):
        return None
    subdomain = host[: -len(suffix)]
    if not subdomain:
        return None
    return f"{subdomain}.sip.livekit.cloud"


def rule_dispatches(rule: api.SIPDispatchRuleInfo, agent_name: str) -> bool:
    return any(agent.agent_name == agent_name for agent in rule.room_config.agents)


async def list_inbound_trunks(lkapi: api.LiveKitAPI) -> list[api.SIPInboundTrunkInfo]:
    trunks: list[api.SIPInboundTrunkInfo] = []
    after_id = ""
    while True:
        response = await lkapi.sip.list_inbound_trunk(
            api.ListSIPInboundTrunkRequest(page=api.Pagination(limit=PAGE_SIZE, after_id=after_id))
        )
        trunks.extend(response.items)
        if len(response.items) < PAGE_SIZE:
            break
        next_id = response.items[-1].sip_trunk_id
        if next_id == after_id:
            break
        after_id = next_id
    return trunks


async def list_dispatch_rules(lkapi: api.LiveKitAPI) -> list[api.SIPDispatchRuleInfo]:
    rules: list[api.SIPDispatchRuleInfo] = []
    after_id = ""
    while True:
        response = await lkapi.sip.list_dispatch_rule(
            api.ListSIPDispatchRuleRequest(page=api.Pagination(limit=PAGE_SIZE, after_id=after_id))
        )
        rules.extend(response.items)
        if len(response.items) < PAGE_SIZE:
            break
        next_id = response.items[-1].sip_dispatch_rule_id
        if next_id == after_id:
            break
        after_id = next_id
    return rules


def caller_headers_ready(trunk: api.SIPInboundTrunkInfo) -> bool:
    return all(
        trunk.headers_to_attributes.get(header) == attribute
        for header, attribute in CALLER_HEADERS.items()
    )


def with_caller_headers(trunk: api.SIPInboundTrunkInfo) -> api.SIPInboundTrunkInfo:
    """Copie le trunk et pose le mappage. L'id est vidé pour un replace."""
    updated = api.SIPInboundTrunkInfo()
    updated.CopyFrom(trunk)
    updated.ClearField("sip_trunk_id")
    updated.ClearField("created_at")
    updated.ClearField("updated_at")
    updated.headers_to_attributes.update(CALLER_HEADERS)
    return updated


def warn_if_drift(trunk: api.SIPInboundTrunkInfo, allowed: list[str], numbers: list[str]) -> None:
    if set(trunk.allowed_addresses) != set(allowed):
        print("Le trunk existant a une allowlist différente. Il n'est pas modifié.")
        print(f"  existant : {', '.join(trunk.allowed_addresses) or '(vide)'}")
        print(f"  .env     : {', '.join(allowed)}")
    if set(trunk.numbers) != set(numbers):
        print("Le trunk existant a une liste de numéros différente. Il n'est pas modifié.")
        print(f"  existant : {', '.join(trunk.numbers) or '(tout numéro)'}")
        print(f"  .env     : {', '.join(numbers) or '(tout numéro)'}")


async def main() -> None:
    livekit_url = require("LIVEKIT_URL")
    require("LIVEKIT_API_KEY")
    require("LIVEKIT_API_SECRET")
    trunk_name = require("SIP_TRUNK_NAME")
    agent_name = os.environ.get("AGENT_NAME", "receptionniste").strip() or "receptionniste"
    allowed = csv_env("SIP_ALLOWED_ADDRESSES")
    numbers = csv_env("SIP_NUMBERS")

    if not allowed:
        print("Variable manquante : SIP_ALLOWED_ADDRESSES", file=sys.stderr)
        raise SystemExit(1)
    for address in allowed:
        if "/" not in address:
            print(
                "SIP_ALLOWED_ADDRESSES doit être en CIDR, par exemple 203.0.113.10/32. "
                f"Reçu : {address}",
                file=sys.stderr,
            )
            raise SystemExit(1)

    lkapi = api.LiveKitAPI()
    try:
        trunks = await list_inbound_trunks(lkapi)
        trunk = next((item for item in trunks if item.name == trunk_name), None)
        trunk_state = "existant"
        if trunk is None:
            trunk = await lkapi.sip.create_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(
                        name=trunk_name,
                        numbers=numbers,
                        allowed_addresses=allowed,
                        headers_to_attributes=dict(CALLER_HEADERS),
                    )
                )
            )
            trunk_state = "créé"
        else:
            warn_if_drift(trunk, allowed, numbers)
            if not caller_headers_ready(trunk):
                trunk_id = trunk.sip_trunk_id
                trunk = await lkapi.sip.update_inbound_trunk(
                    trunk_id,
                    with_caller_headers(trunk),
                )
                trunk_state = "mis à jour"

        rules = await list_dispatch_rules(lkapi)
        rule = next((item for item in rules if rule_dispatches(item, agent_name)), None)
        rule_state = "existante"
        if rule is None:
            rule = await lkapi.sip.create_dispatch_rule(
                api.CreateSIPDispatchRuleRequest(
                    dispatch_rule=api.SIPDispatchRuleInfo(
                        name=f"{agent_name}-inbound",
                        trunk_ids=[trunk.sip_trunk_id],
                        rule=api.SIPDispatchRule(
                            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                                room_prefix=ROOM_PREFIX,
                            )
                        ),
                        room_config=api.RoomConfiguration(
                            agents=[api.RoomAgentDispatch(agent_name=agent_name)]
                        ),
                    )
                )
            )
            rule_state = "créée"
        elif trunk.sip_trunk_id not in rule.trunk_ids and list(rule.trunk_ids):
            print(
                "La règle existante ne vise pas ce trunk. Elle n'est pas modifiée. "
                f"trunks de la règle : {', '.join(rule.trunk_ids)}"
            )

        host = sip_host(livekit_url)
        print(f"Trunk : {trunk.sip_trunk_id} ({trunk_state}) {trunk.name}")
        print("En-têtes : Remote-Party-ID vers sip.rpid, P-Asserted-Identity vers sip.pai")
        print(f"Règle : {rule.sip_dispatch_rule_id} ({rule_state}) {rule.name}")
        if host:
            print(f"Hôte SIP 3CX : {host} port 5060")
        else:
            print(
                "Hôte SIP non déduit depuis LIVEKIT_URL. "
                "Le endpoint est dans Project settings de LiveKit Cloud."
            )
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
