"""Worker LiveKit : répond aux appels SIP et parle avec Grok Voice."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    StopResponse,
    ToolError,
    cli,
    function_tool,
    get_job_context,
)
from livekit.plugins import xai

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("receptionniste")

AGENT_NAME = os.environ.get("AGENT_NAME", "receptionniste")
GROK_VOICE = os.environ.get("GROK_VOICE", "Ara")
TRANSFER_TO = os.environ.get("TRANSFER_TO", "").strip()

INSTRUCTIONS = (
    Path(__file__).resolve().parent / "instructions.md"
).read_text(encoding="utf-8")

TRANSFER_UNAVAILABLE = (
    "Le transfert n'a pas abouti. Excuse-toi et propose d'appeler la réception "
    "au 04 93 36 80 80 ou d'écrire à hotelpanoramagrasse@gmail.com."
)


def sip_caller(room: rtc.Room) -> rtc.RemoteParticipant | None:
    """Renvoie l'appelant téléphonique présent dans la room, ou None s'il n'y en a pas.

    Chaque appel reçu par 3CX crée sa propre room LiveKit (préfixe « call- »),
    où l'appelant apparaît comme un participant de type SIP. On prend le premier
    participant distant de ce type : c'est lui qu'il faut transférer. L'agent
    lui-même n'est pas dans remote_participants, et les autres participants
    éventuels (web, autres agents) sont ignorés car leur type n'est pas SIP.
    """
    return next(
        (
            participant
            for participant in room.remote_participants.values()
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )


class Receptionniste(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def transferer_vers_humain(self, context: RunContext) -> None:
        """Transfère l'appel vers un réceptionniste humain de l'hôtel.

        À utiliser uniquement quand l'appelant demande à parler à un humain,
        une vraie personne, la réception ou un employé de l'hôtel.
        Avant d'appeler cet outil, dis à l'appelant que tu le transfères.
        """
        if not TRANSFER_TO:
            logger.error("TRANSFER_TO absent du .env, transfert impossible")
            raise ToolError(TRANSFER_UNAVAILABLE)

        job_ctx = get_job_context()
        caller = sip_caller(job_ctx.room)
        if caller is None:
            logger.error("aucun participant SIP dans la room %s", job_ctx.room.name)
            raise ToolError(TRANSFER_UNAVAILABLE)

        context.disallow_interruptions()
        await context.wait_for_playout()

        if caller.identity not in job_ctx.room.remote_participants:
            logger.info("%s a raccroché avant le transfert", caller.identity)
            raise StopResponse()

        logger.info("transfert de %s vers %s", caller.identity, TRANSFER_TO)
        try:
            response = await job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=job_ctx.room.name,
                    participant_identity=caller.identity,
                    transfer_to=TRANSFER_TO,
                    play_dialtone=False,
                )
            )
        except Exception:
            logger.exception("échec du transfert vers %s", TRANSFER_TO)
            raise ToolError(TRANSFER_UNAVAILABLE) from None

        if response.status == api.SIPTransferStatus.STS_TRANSFER_FAILED:
            logger.error(
                "transfert refusé : %s (SIP %s)",
                api.SIPTransferReason.Name(response.reason),
                response.sip_status.code,
            )
            raise ToolError(TRANSFER_UNAVAILABLE)

        logger.info("transfert accepté vers %s", TRANSFER_TO)
        raise StopResponse()


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(voice=GROK_VOICE),
    )
    await session.start(room=ctx.room, agent=Receptionniste())
    await session.generate_reply(
        instructions=(
            "Hôtel Panorama Bonjour, comment puis-je vous aider ?"
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
