"""Worker LiveKit : répond aux appels SIP et parle avec Grok Voice."""

import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import xai

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

AGENT_NAME = os.environ.get("AGENT_NAME", "receptionniste")
GROK_VOICE = os.environ.get("GROK_VOICE", "Ara")

INSTRUCTIONS = (
    Path(__file__).resolve().parent / "instructions.md"
).read_text(encoding="utf-8")


class Receptionniste(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(voice=GROK_VOICE),
    )
    await session.start(room=ctx.room, agent=Receptionniste())
    await session.generate_reply(
        instructions=(
            "Accueille l'appelant en français, brièvement, "
            "et demande comment tu peux l'aider."
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
