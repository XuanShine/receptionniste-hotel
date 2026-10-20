import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
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
from livekit.agents.llm import ToolResult
from livekit.plugins import xai

from record import keep_dialogue
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
GROK_VOICE = os.environ.get("GROK_VOICE", "Ara")

INSTRUCTIONS = (
    Path(__file__).resolve().parent / "instructions.md"
).read_text(encoding="utf-8")

_WEEKDAYS = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def instructions_for(today: date) -> str:
    stamp = today.strftime("%d/%m/%Y")
    spoken = f"{_WEEKDAYS[today.weekday()]} {today.day} {_MONTHS[today.month - 1]} {today.year}"
    return (
        f"{INSTRUCTIONS}\n\n"
        "Date du jour\n\n"
        f"Aujourd'hui est le {spoken} ({stamp}). "
        "« Ce soir » et « aujourd'hui » sont cette date. "
        "« Demain » est le lendemain. "
        "Si le client donne un jour et un mois sans année, utilise cette année. "
        "Si ce jour est déjà passé, utilise l'année suivante. "
        "Appelle verifier_disponibilite_et_prix avec des dates jj/mm/aaaa "
        "de cette année ou de la suivante.\n"
    )


class Receptionniste(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=instructions_for(date.today()))
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
            "arrivee et depart sont au format jj/mm/aaaa, avec l'année de la date du jour "
            "indiquée dans les instructions. Le départ est le matin du checkout, "
            "pas une nuit facturée. Le client a dit un nombre de personnes : "
            "passe-le dans adultes et 0 dans enfants, sauf s'il a distingué des enfants. "
            "Annonce seulement les chambres renvoyées, avec le prix total. N'invente aucun prix."
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
            "Après succès, dis « Nous allons prochainement vous envoyer un email de confirmation ». "
            "Ne lis pas le numéro de réservation. "
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
    session = AgentSession(llm=xai.realtime.RealtimeModel(voice=GROK_VOICE))
    keep_dialogue(session, ctx)
    await session.start(room=ctx.room, agent=Receptionniste())
    await session.generate_reply(
        instructions="Hôtel Panorama Bonjour, comment puis-je vous aider ?",
    )


if __name__ == "__main__":
    cli.run_app(server)
