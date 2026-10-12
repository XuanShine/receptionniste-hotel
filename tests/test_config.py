"""Configuration sans trunk SIP."""

import pytest

from reception.config import load_settings, rtp_port_bounds
from reception.errors import ConfigError

BASE = {
    "SIP_USER": "100",
    "SIP_PASSWORD": "s3cret-value",
    "SIP_DOMAIN": "pbx.hotel.test",
}


def test_rtp_bounds_include_last_rtcp_port():
    assert rtp_port_bounds(4000) == (4000, 4021)
    assert rtp_port_bounds(5000, 4) == (5000, 5005)


def test_defaults_and_uris():
    settings = load_settings(BASE)
    assert settings.sip_port == 5060
    assert settings.sip_transport == "udp"
    assert settings.sip_local_port == 5060
    assert settings.sip_auth_user == "100"
    assert settings.rtp_port == 4000
    assert settings.registrar_uri == "sip:pbx.hotel.test:5060"
    assert settings.id_uri == '"Hotel Panorama" <sip:100@pbx.hotel.test>'
    assert "s3cret-value" not in repr(settings)


def test_tcp_registrar_and_auth_user():
    settings = load_settings(
        {
            **BASE,
            "SIP_PORT": "5070",
            "SIP_TRANSPORT": "TCP",
            "SIP_AUTH_USER": "auth-100",
            "SIP_PUBLIC_ADDRESS": "203.0.113.10",
        }
    )
    assert settings.registrar_uri == "sip:pbx.hotel.test:5070;transport=tcp"
    assert settings.sip_auth_user == "auth-100"
    assert settings.sip_public_address == "203.0.113.10"


def test_domain_strips_sip_prefix():
    settings = load_settings({**BASE, "SIP_DOMAIN": "sip:pbx.hotel.test/"})
    assert settings.sip_domain == "pbx.hotel.test"


def test_missing_variables_are_listed():
    with pytest.raises(ConfigError) as exc:
        load_settings({})
    message = str(exc.value)
    assert "SIP_USER" in message
    assert "SIP_PASSWORD" in message
    assert "SIP_DOMAIN" in message


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SIP_PORT", "0"),
        ("SIP_PORT", "70000"),
        ("SIP_PORT", "abc"),
        ("SIP_TRANSPORT", "tls"),
        ("SIP_DOMAIN", "pbx.hotel.test:5060"),
        ("SIP_USER", "100 200"),
        ("LOG_LEVEL", "verbose"),
    ],
)
def test_rejects_invalid_values(key, value):
    with pytest.raises(ConfigError):
        load_settings({**BASE, key: value})
