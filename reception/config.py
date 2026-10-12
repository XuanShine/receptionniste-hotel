"""Configuration lue depuis l'environnement ou un fichier .env."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from reception.errors import ConfigError
from reception.greeting import DEFAULT_GREETING_PATH

_REQUIRED = ("SIP_USER", "SIP_PASSWORD", "SIP_DOMAIN")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_TRANSPORTS = ("udp", "tcp")

# PJSUA place les ports RTP pairs de ``port`` à ``port + portRange`` et le
# RTCP sur RTP+1. L'exemple des bindings (port 5000, plage 4) occupe
# 5000–5005. La borne haute inclusive est donc port + plage + 1.
RTP_PORT_RANGE = 20


def rtp_port_bounds(start: int, port_range: int = RTP_PORT_RANGE) -> tuple[int, int]:
    """Bornes inclusives des ports UDP RTP et RTCP."""

    return start, start + port_range + 1


@dataclass(frozen=True)
class Settings:
    """Paramètres d'enregistrement SIP et chemin de l'annonce."""

    sip_user: str
    sip_password: str
    sip_domain: str
    sip_port: int
    sip_transport: str
    sip_local_port: int
    sip_auth_user: str
    sip_public_address: str | None
    rtp_port: int
    greeting_path: Path
    log_level: str

    def __repr__(self) -> str:
        return (
            "Settings("
            f"sip_user={self.sip_user!r}, "
            f"sip_domain={self.sip_domain!r}, "
            f"sip_port={self.sip_port}, "
            f"sip_transport={self.sip_transport!r}, "
            f"sip_password=***)"
        )

    @property
    def id_uri(self) -> str:
        return f'"Hotel Panorama" <sip:{self.sip_user}@{self.sip_domain}>'

    @property
    def registrar_uri(self) -> str:
        uri = f"sip:{self.sip_domain}:{self.sip_port}"
        if self.sip_transport == "tcp":
            uri += ";transport=tcp"
        return uri


def load_settings(
    source: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> Settings:
    """Construit Settings. ``source`` explicite ignore le fichier .env."""

    if source is None:
        if env_file is not None and Path(env_file).is_file():
            load_dotenv(env_file, override=False)
        source = os.environ

    missing = [key for key in _REQUIRED if not _text(source, key)]
    if missing:
        raise ConfigError(
            "Variables obligatoires manquantes : " + ", ".join(missing) + "."
        )

    domain = _domain(_text(source, "SIP_DOMAIN"))
    transport = _text(source, "SIP_TRANSPORT") or "udp"
    transport = transport.lower()
    if transport not in _TRANSPORTS:
        raise ConfigError("SIP_TRANSPORT doit valoir udp ou tcp.")

    log_level = (_text(source, "LOG_LEVEL") or "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError("LOG_LEVEL doit valoir DEBUG, INFO, WARNING ou ERROR.")

    greeting_raw = _text(source, "GREETING_WAV")
    if greeting_raw:
        greeting_path = Path(greeting_raw).expanduser()
        if not greeting_path.is_absolute():
            greeting_path = Path.cwd() / greeting_path
    else:
        greeting_path = DEFAULT_GREETING_PATH

    public = _text(source, "SIP_PUBLIC_ADDRESS") or None
    auth_user = _text(source, "SIP_AUTH_USER") or _text(source, "SIP_USER")

    return Settings(
        sip_user=_sip_user(_text(source, "SIP_USER")),
        sip_password=_text(source, "SIP_PASSWORD"),
        sip_domain=domain,
        sip_port=_port(source, "SIP_PORT", 5060, allow_zero=False),
        sip_transport=transport,
        sip_local_port=_port(source, "SIP_LOCAL_PORT", 5060, allow_zero=True),
        sip_auth_user=_sip_user(auth_user),
        sip_public_address=public,
        rtp_port=_port(source, "RTP_PORT", 4000, allow_zero=False),
        greeting_path=greeting_path.resolve(),
        log_level=log_level,
    )


def _text(source: Mapping[str, str], key: str) -> str:
    value = source.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _port(
    source: Mapping[str, str],
    key: str,
    default: int,
    *,
    allow_zero: bool,
) -> int:
    raw = _text(source, key)
    if raw == "":
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} doit être un entier (reçu : {raw!r}).") from exc
    lower = 0 if allow_zero else 1
    if port < lower or port > 65535:
        raise ConfigError(f"{key} doit être compris entre {lower} et 65535.")
    return port


def _sip_user(value: str) -> str:
    if any(character in value for character in " @<>\";"):
        raise ConfigError(
            "L'identifiant SIP ne doit contenir ni espace ni caractère réservé."
        )
    return value


def _domain(value: str) -> str:
    domain = value.strip()
    if domain.lower().startswith("sip:"):
        domain = domain[4:]
    domain = domain.strip("/")
    if not domain or any(character in domain for character in " @/;:"):
        raise ConfigError(
            "SIP_DOMAIN doit être un nom d'hôte ou une adresse IPv4, "
            "sans port (utilisez SIP_PORT)."
        )
    return domain
