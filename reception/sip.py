"""Session SIP PJSUA2 : enregistrement 3CX, décrochage, annonce, raccrochage.

Les rappels PJSUA2 sont pompés sur le fil principal (``threadCnt = 0`` et
``mainThreadOnly``). Les bindings Python se comportent mal quand un fil
natif entre dans un objet SWIG. Le raccrochage est différé hors du rappel
``onEof2`` pour la même raison.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from reception import __version__
from reception.codecs import apply_codec_policy
from reception.config import RTP_PORT_RANGE, Settings
from reception.errors import SipUnavailableError
from reception.flow import CallAction, IncomingCallFlow
from reception.greeting import Greeting

logger = logging.getLogger(__name__)

_PJSIP_LOG = {"DEBUG": 5, "INFO": 3, "WARNING": 2, "ERROR": 1}


def pjsua2_install_help() -> str:
    return (
        "Le module pjsua2 est introuvable. "
        "Installez la roue précompilée (Python 3.8 à 3.12, "
        "Linux x86_64, macOS ou Windows amd64) : "
        "pip install pjsua2-python==2.15.1.post1. "
        "Sur Raspberry Pi (Linux aarch64) ou avec Python 3.13, "
        "cette roue n'existe pas : il faut compiler pjproject "
        "avec SWIG et les bindings Python. Voir SPEC.md."
    )


def import_pjsua2():
    try:
        import pjsua2 as pj
    except ImportError as exc:
        raise SipUnavailableError(pjsua2_install_help()) from exc
    return pj


class ReceptionService:
    """Compte SIP enregistré auprès de 3CX. Un processus, plusieurs appels."""

    def __init__(self, settings: Settings, greeting: Greeting) -> None:
        self.settings = settings
        self.greeting = greeting
        self._pj = None
        self._ep = None
        self._account: ReceptionAccount | None = None
        self._stop = False
        self._stopped = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self.start()
            logger.info(
                "En écoute. Registrar %s, port local %s.",
                self.settings.registrar_uri,
                self.settings.sip_local_port,
            )
            while not self._stop:
                self._ep.libHandleEvents(50)
                if self._account is not None:
                    self._account.poll()
        finally:
            self.stop()

    def start(self) -> None:
        pj = import_pjsua2()
        self._pj = pj
        endpoint = pj.Endpoint()
        endpoint.libCreate()
        try:
            self._configure_endpoint(pj, endpoint)
        except Exception:
            account = self._account
            self._account = None
            if account is not None:
                try:
                    account.shutdown()
                except pj.Error:
                    pass
                account.release_calls()
                del account
            try:
                endpoint.libDestroy()
            except pj.Error:
                pass
            raise

        self._ep = endpoint
        logger.info(
            "Réceptionniste Hôtel Panorama %s — enregistrement de %s vers %s",
            __version__,
            self.settings.id_uri,
            self.settings.registrar_uri,
        )

    def _configure_endpoint(self, pj, endpoint) -> None:
        endpoint.libInit(self._endpoint_config(pj))
        try:
            endpoint.audDevManager().setNullDev()
        except pj.Error as exc:
            raise SipUnavailableError(
                "Périphérique audio nul indisponible "
                f"({exc.reason}). La v1 n'utilise pas de carte son : "
                "elle injecte le WAV dans l'appel."
            ) from exc

        transport = pj.TransportConfig()
        transport.port = self.settings.sip_local_port
        if self.settings.sip_public_address:
            transport.publicAddress = self.settings.sip_public_address
        kind = (
            pj.PJSIP_TRANSPORT_TCP
            if self.settings.sip_transport == "tcp"
            else pj.PJSIP_TRANSPORT_UDP
        )
        try:
            endpoint.transportCreate(kind, transport)
            endpoint.libStart()
        except pj.Error as exc:
            raise SipUnavailableError(
                f"Démarrage SIP impossible ({exc.reason}). "
                "Vérifiez que SIP_LOCAL_PORT est libre."
            ) from exc

        applied = apply_codec_policy(
            endpoint.codecSetPriority,
            [codec.codecId for codec in endpoint.codecEnum2()],
        )
        enabled = [f"{codec_id}={priority}" for codec_id, priority in applied if priority]
        logger.info("Codecs proposés : %s", ", ".join(enabled) or "(aucun)")
        if not any(codec_id.startswith("opus/") for codec_id, _priority in applied):
            logger.warning(
                "Opus est absent de cette compilation pjsua2. "
                "Activez PCMA, PCMU et G.722 sur l'extension 3CX."
            )

        account_cfg = pj.AccountConfig()
        account_cfg.idUri = self.settings.id_uri
        account_cfg.regConfig.registrarUri = self.settings.registrar_uri
        account_cfg.regConfig.registerOnAdd = True
        account_cfg.sipConfig.authCreds.append(
            pj.AuthCredInfo(
                "digest",
                "*",
                self.settings.sip_auth_user,
                0,
                self.settings.sip_password,
            )
        )
        media = account_cfg.mediaConfig.transportConfig
        media.port = self.settings.rtp_port
        media.portRange = RTP_PORT_RANGE
        # Un re-INVITE de verrouillage codec couperait l'annonce de quatre secondes.
        account_cfg.mediaConfig.lockCodecEnabled = False
        if self.settings.sip_public_address:
            media.publicAddress = self.settings.sip_public_address

        account = ReceptionAccount(pj, self.greeting)
        try:
            account.create(account_cfg)
        except pj.Error as exc:
            raise SipUnavailableError(
                f"Création du compte SIP impossible ({exc.reason})."
            ) from exc
        self._account = account

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop = True
        pj = self._pj
        account = self._account
        endpoint = self._ep
        self._account = None
        if account is not None and endpoint is not None and pj is not None:
            try:
                account.shutdown()
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    endpoint.libHandleEvents(20)
            except pj.Error as exc:
                logger.warning("Fermeture du compte SIP : %s", exc.reason)
            account.release_calls()
            del account
        if endpoint is not None:
            try:
                endpoint.libDestroy()
            except pj.Error as exc:
                logger.warning("Arrêt de la pile SIP : %s", exc.reason)
        self._ep = None

    def _endpoint_config(self, pj):
        config = pj.EpConfig()
        config.uaConfig.threadCnt = 0
        config.uaConfig.mainThreadOnly = True
        config.uaConfig.maxCalls = 4
        config.uaConfig.userAgent = f"HotelPanorama-Reception/{__version__}"
        level = _PJSIP_LOG[self.settings.log_level]
        config.logConfig.level = level
        config.logConfig.consoleLevel = level
        config.medConfig.clockRate = 16000
        config.medConfig.sndClockRate = 16000
        config.medConfig.channelCount = 1
        config.medConfig.noVad = True
        return config


class ReceptionAccount:
    """Compte enregistré. Créé seulement après ``import pjsua2``."""

    def __init__(self, pj, greeting: Greeting) -> None:
        # La classe de base est construite à la volée : le module reste
        # importable dans les tests qui ne chargent pas la pile native,
        # et l'héritage SWIG n'existe qu'au démarrage réel.
        self._calls: list[IncomingCall] = []
        self._dead: list[IncomingCall] = []

        class _Account(pj.Account):
            def onRegState(self_inner, prm) -> None:
                del prm
                info = self_inner.getInfo()
                logger.info(
                    "Enregistrement SIP : %s %s (actif=%s)",
                    info.regStatus,
                    info.regStatusText,
                    info.regIsActive,
                )

            def onIncomingCall(self_inner, iprm) -> None:
                call = IncomingCall(pj, self_inner, iprm.callId, greeting, self)
                self._calls.append(call)
                try:
                    remote = call.getInfo().remoteUri
                except pj.Error:
                    remote = "?"
                logger.info("Appel entrant de %s", remote)
                decision = call.flow.on_incoming()
                if decision.action == CallAction.ANSWER:
                    call.answer_now()

        self._inner = _Account()

    def create(self, account_cfg) -> None:
        self._inner.create(account_cfg)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def poll(self) -> None:
        for call in list(self._calls):
            call.poll()
        self.sweep()

    def discard_later(self, call: IncomingCall) -> None:
        self._dead.append(call)

    def sweep(self) -> None:
        for call in self._dead:
            if call in self._calls:
                self._calls.remove(call)
        self._dead.clear()

    def release_calls(self) -> None:
        self._calls.clear()
        self._dead.clear()


class IncomingCall:
    """Un appel entrant et son lecteur WAV."""

    def __init__(
        self,
        pj,
        account,
        call_id: int,
        greeting: Greeting,
        owner: ReceptionAccount,
    ) -> None:
        self._pj = pj
        self._owner = owner
        self.flow = IncomingCallFlow(greeting)
        self.player = None
        self._eof_pending = False

        class _Call(pj.Call):
            def onCallState(self_inner, prm) -> None:
                del prm
                info = self_inner.getInfo()
                logger.info("État d'appel : %s", info.stateText)
                if info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                    logger.info(
                        "Appel terminé : %s %s",
                        info.lastStatusCode,
                        info.lastReason,
                    )
                    self.flow.on_remote_disconnected()
                    owner.discard_later(self)

            def onCallMediaState(self_inner, prm) -> None:
                del prm
                info = self_inner.getInfo()
                for index, media in enumerate(info.media):
                    if media.type != pj.PJMEDIA_TYPE_AUDIO:
                        continue
                    if media.status != pj.PJSUA_CALL_MEDIA_ACTIVE:
                        continue
                    decision = self.flow.on_media_active()
                    if decision.action != CallAction.PLAY or decision.wav_path is None:
                        return
                    self._play(index, decision.wav_path)
                    return

        self._inner = _Call(account, call_id)

    def getInfo(self):
        return self._inner.getInfo()

    def answer_now(self) -> None:
        pj = self._pj
        params = pj.CallOpParam(True)
        params.statusCode = pj.PJSIP_SC_OK
        try:
            self._inner.answer(params)
            logger.info("Décrochage.")
        except pj.Error as exc:
            logger.error("Décrochage impossible : %s", exc.reason)

    def hangup_now(self) -> None:
        pj = self._pj
        params = pj.CallOpParam(True)
        params.statusCode = pj.PJSIP_SC_OK
        try:
            self._inner.hangup(params)
            logger.info("Raccrochage.")
        except pj.Error as exc:
            logger.warning("Raccrochage : %s", exc.reason)

    def poll(self) -> None:
        if not self._eof_pending:
            return
        self._eof_pending = False
        decision = self.flow.on_playback_finished()
        if decision.action == CallAction.HANGUP:
            self.hangup_now()

    def on_player_eof(self) -> None:
        logger.info("Fin de l'annonce.")
        self._eof_pending = True

    def _play(self, media_index: int, wav_path: Path) -> None:
        pj = self._pj
        player = GreetingPlayer(pj, self.on_player_eof)
        try:
            player.createPlayer(str(wav_path), pj.PJMEDIA_FILE_NO_LOOP)
            audio = self._inner.getAudioMedia(media_index)
            player.startTransmit(audio)
        except pj.Error as exc:
            logger.error("Lecture impossible : %s", exc.reason)
            self._eof_pending = True
            return
        self.player = player
        logger.info("Lecture de %s", wav_path)


class GreetingPlayer:
    """Lecteur WAV qui signale la fin de fichier une seule fois."""

    def __init__(self, pj, on_eof) -> None:
        self._on_eof = on_eof

        class _Player(pj.AudioMediaPlayer):
            def onEof2(self_inner) -> bool:
                del self_inner
                on_eof()
                return False

        self._inner = _Player()

    def createPlayer(self, filename: str, options: int) -> None:
        self._inner.createPlayer(filename, options)

    def startTransmit(self, sink) -> None:
        self._inner.startTransmit(sink)
