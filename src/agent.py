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

TRANSFER_ANNOUNCEMENT = "Je vous transfère à un réceptionniste, un instant."


def sip_caller(room: rtc.Room) -> rtc.RemoteParticipant | None:
    for participant in room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return participant
    return None


class Receptionniste(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool()
    async def transferer_vers_humain(self, ctx: RunContext) -> str:
        """Transfère l'appelant vers un réceptionniste humain de l'hôtel.

        À appeler uniquement quand l'appelant demande explicitement à parler
        à un humain, une personne, un réceptionniste ou la réception.
        """
        if not TRANSFER_TO:
            return (
                "Le transfert n'est pas configuré. Excuse-toi et donne le "
                "04 93 36 80 80."
            )

        ctx.disallow_interruptions()
        await ctx.wait_for_playout()
        await ctx.session.say(TRANSFER_ANNOUNCEMENT, allow_interruptions=False)

        job_ctx = get_job_context()
        if job_ctx.is_fake_job():
            logger.info("transfert simulé (console) vers %s", TRANSFER_TO)
            return (
                "Mode console : aucun appel téléphonique à transférer. "
                "Dis que tu aurais passé l'appel à la réception."
            )

        caller = sip_caller(job_ctx.room)
        if caller is None:
            return (
                "Aucun appelant téléphonique n'est dans la room. "
                "Dis que le transfert n'est possible que pendant un appel, "
                "et donne le 04 93 36 80 80."
            )

        try:
            result = await job_ctx.transfer_sip_participant(
                caller,
                TRANSFER_TO,
                play_dialtone=True,
            )
        except Exception:
            logger.exception("transfert SIP échoué vers %s", TRANSFER_TO)
            return (
                "Le transfert a échoué. Excuse-toi et donne le numéro "
                "04 93 36 80 80."
            )

        logger.info(
            "transfert SIP de %s vers %s status=%s reason=%s",
            caller.identity,
            TRANSFER_TO,
            getattr(result, "status", None),
            getattr(result, "reason", None),
        )
        if getattr(result, "status", None) == api.SIPTransferStatus.STS_TRANSFER_FAILED:
            return (
                "Le transfert a échoué. Excuse-toi et donne le numéro "
                "04 93 36 80 80."
            )
        return "Transfert lancé. Ne dis plus rien."


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
