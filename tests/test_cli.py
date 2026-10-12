"""Commande --check, sans enregistrement SIP."""

from reception.cli import main
from reception.greeting import GREETING_TEXT
from reception.sip import pjsua2_install_help

_KEYS = (
    "SIP_USER",
    "SIP_PASSWORD",
    "SIP_DOMAIN",
    "SIP_PORT",
    "SIP_TRANSPORT",
    "SIP_LOCAL_PORT",
    "SIP_AUTH_USER",
    "SIP_PUBLIC_ADDRESS",
    "RTP_PORT",
    "GREETING_WAV",
    "LOG_LEVEL",
)


def _clear(monkeypatch) -> None:
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_check_prints_target_without_password(monkeypatch, tmp_path, capsys):
    _clear(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SIP_USER=100\nSIP_PASSWORD=s3cret-value\nSIP_DOMAIN=pbx.hotel.test\n",
        encoding="utf-8",
    )
    assert main(["--check", "--env-file", str(env_file)]) == 0
    output = capsys.readouterr().out
    assert "s3cret-value" not in output
    assert "sip:pbx.hotel.test:5060" in output
    assert "4000-4021" in output
    assert GREETING_TEXT in output


def test_check_missing_env_returns_2(monkeypatch, tmp_path, capsys):
    _clear(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("SIP_PORT=5060\n", encoding="utf-8")
    assert main(["--check", "--env-file", str(env_file)]) == 2
    assert "SIP_USER" in capsys.readouterr().err


def test_install_help_mentions_wheel_limits():
    text = pjsua2_install_help()
    assert "pjsua2-python==2.15.1.post1" in text
    assert "aarch64" in text
    assert "3.13" in text
