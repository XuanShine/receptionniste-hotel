"""Worker LiveKit : répond aux appels SIP. Cartesia entend et parle, Grok répond."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    JobContext,
    RunContext,
    cli,
    function_tool,
    get_job_context,
)
from livekit.agents.llm import ToolResult

from pipeline import VoicePipeline
from transfer import (
    RECEPTION_PHONE,
    TransferOutcome,
    bridge_sip_participant,
    dial_number,
    find_sip_caller,
    outbound_trunk_id,
    perform_transfer,
    refer_sip_participant,
    refer_target_from_env,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("receptionniste")
AGENT_NAME = os.environ.get("AGENT_NAME", "receptionniste")
PIPELINE = VoicePipeline.from_env(os.environ)

INSTRUCTIONS = (
    Path(__file__).resolve().parent / "instructions.md"
).read_text(encoding="utf-8")


class Receptionniste(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self._transfer_started = False

    @function_tool(
        description=(
            "À appeler immédiatement quand le client demande à parler à un "
            "réceptionniste, à la réception, à une personne ou à un humain. "
            "Aucun argument. L'outil prononce la phrase de transfert puis "
            "envoie un SIP REFER vers le poste 10."
        )
    )
    async def transferer_reception(self, ctx: RunContext) -> ToolResult | str:
        if self._transfer_started:
            return ToolResult(
                "Transfert déjà en cours vers le poste 10. Ne dis plus rien.",
                reply_required=False,
            )
        self._transfer_started = True
        try:
            outcome = await self._transfer(ctx)
        except Exception:
            self._transfer_started = False
            logger.exception("transfert interrompu")
            raise
        if not outcome.connected:
            self._transfer_started = False
        elif ctx.session is not None:
            ctx.session.shutdown(drain=False)
        return outcome.result

    async def _transfer(self, ctx: RunContext) -> TransferOutcome:
        job = get_job_context(required=False)
        caller = None if job is None else find_sip_caller(job.room.remote_participants.values())
        try:
            transfer_to = refer_target_from_env()
        except ValueError as exc:
            logger.error("cible de transfert invalide : %s", exc)
            return TransferOutcome(
                result=(
                    "Le transfert est mal configuré. "
                    f"Invite le client à appeler la réception au {RECEPTION_PHONE}."
                ),
                connected=False,
            )
        trunk_id = outbound_trunk_id()

        async def refer(identity: str, target: str) -> None:
            if job is None:
                raise RuntimeError("pas de room LiveKit")
            logger.info("SIP REFER de %s vers %s", job.room.name, target)
            await refer_sip_participant(
                job.api,
                room_name=job.room.name,
                participant_identity=identity,
                transfer_to=target,
            )

        async def bridge() -> None:
            if job is None:
                raise RuntimeError("pas de room LiveKit")
            logger.info(
                "repli : appel du poste %s dans %s",
                dial_number(transfer_to),
                job.room.name,
            )
            await bridge_sip_participant(
                job.api,
                room_name=job.room.name,
                trunk_id=trunk_id,
                dial=dial_number(transfer_to),
            )

        return await perform_transfer(
            say=lambda text: ctx.session.say(text, allow_interruptions=False),
            caller_identity=caller,
            transfer_to=transfer_to,
            refer=refer,
            bridge=bridge if trunk_id else None,
        )


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    session = PIPELINE.new_session()
    await session.start(room=ctx.room, agent=Receptionniste())
    await session.generate_reply(
        instructions=(
            "Accueille l'appelant en français, brièvement, "
            "et demande comment tu peux l'aider."
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
