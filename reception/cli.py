"""Point d'entrée : validation, puis boucle d'enregistrement SIP."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from reception import __version__
from reception.config import Settings, load_settings, rtp_port_bounds
from reception.errors import ConfigError, SipUnavailableError
from reception.greeting import Greeting, load_greeting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Réceptionniste téléphonique de l'Hôtel Panorama (v1)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valide la configuration et l'annonce, sans s'enregistrer.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Fichier .env (défaut : .env). Ignoré s'il est absent.",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings(env_file=args.env_file)
        greeting = load_greeting(settings.greeting_path)
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2

    _configure_logging(settings.log_level)
    if args.check:
        print(format_check(settings, greeting))
        return 0

    from reception.sip import ReceptionService

    service = ReceptionService(settings, greeting)
    _install_stop_signals(service)
    try:
        service.run()
    except SipUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def format_check(settings: Settings, greeting: Greeting) -> str:
    public = settings.sip_public_address or "(adresse locale)"
    rtp_low, rtp_high = rtp_port_bounds(settings.rtp_port)
    return "\n".join(
        [
            f"Configuration valide (réceptionniste {__version__}).",
            f"  Extension : {settings.sip_user}",
            f"  Registrar : {settings.registrar_uri}",
            f"  Transport : {settings.sip_transport}",
            f"  Port SIP local : {settings.sip_local_port}",
            f"  Adresse publique : {public}",
            f"  RTP : {rtp_low}-{rtp_high}",
            f"  Annonce : {greeting.text}",
            f"  Fichier : {greeting.path} ({greeting.duration_s:.1f} s)",
        ]
    )


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _install_stop_signals(service) -> None:
    def _stop(signum, _frame) -> None:
        logging.getLogger(__name__).info("Signal %s, arrêt.", signum)
        service.request_stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

