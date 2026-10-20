"""Worker LiveKit : répond aux appels SIP. Cartesia entend et parle, Grok répond."""

import asyncio
import json
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
from wubook import WubookError, WubookService, parse_guest

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
        self._wubook: WubookService | None = None

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

    @function_tool(
        description=(
            "Interroge WuBook pour les chambres réelles libres entre l'arrivée et le départ. "
            "arrivee et depart sont au format jj/mm/aaaa. Le départ est le matin du checkout, "
            "pas une nuit facturée. adultes vaut au moins 1. enfants vaut 0 s'il n'y en a pas. "
            "Annonce au plus 3 chambres, avec le prix total renvoyé. N'invente aucun prix."
        )
    )
    async def verifier_disponibilite_et_prix(
        self,
        ctx: RunContext,
        arrivee: str,
        depart: str,
        adultes: int,
        enfants: int,
    ) -> str:
        return await self._wubook_call(
            lambda desk: desk.disponibilite(arrivee, depart, adultes, enfants)
        )

    @function_tool(
        description=(
            "Crée une réservation confirmée dans WuBook après une confirmation explicite. "
            "chambre est l'identifiant renvoyé par verifier_disponibilite_et_prix. "
            "prix_total_eur est le total annoncé, par exemple 120.00. "
            "Ne demande jamais de carte bancaire. Un pays vide signifie FR. "
            "Si la réponse contient erreur, propose le transfert vers la réception."
        )
    )
    async def creer_reservation(
        self,
        ctx: RunContext,
        arrivee: str,
        depart: str,
        chambre: int,
        nom: str,
        prenom: str,
        email: str,
        telephone: str,
        adultes: int,
        enfants: int,
        prix_total_eur: str,
        ville: str = "",
        rue: str = "",
        pays: str = "",
        heure_arrivee: str = "",
        notes: str = "",
    ) -> str:
        return await self._wubook_call(
            lambda desk: desk.reserver(
                arrival=arrivee,
                departure=depart,
                room_id=chambre,
                price_total=prix_total_eur,
                guest=parse_guest(
                    last_name=nom,
                    first_name=prenom,
                    email=email,
                    phone=telephone,
                    city=ville,
                    street=rue,
                    country=pays,
                    arrival_hour=heure_arrivee,
                    notes=notes,
                ),
                adults=adultes,
                children=enfants,
            )
        )

    def _desk(self) -> WubookService:
        if self._wubook is None:
            self._wubook = WubookService.from_env(os.environ)
        return self._wubook

    async def _wubook_call(self, run) -> str:
        try:
            desk = self._desk()
            result = await asyncio.to_thread(run, desk)
        except WubookError as exc:
            return json.dumps({"erreur": str(exc)}, ensure_ascii=False)
        except Exception:
            logger.exception("appel WuBook")
            return json.dumps({"erreur": "WuBook est indisponible."}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)


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
