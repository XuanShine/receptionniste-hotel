"""Lecture du WAV par PJSUA2, sans trunk et sans carte son."""

import time

import pytest

from reception.greeting import DEFAULT_GREETING_PATH, load_greeting


def test_greeting_reaches_eof_once_on_null_device():
    pytest.importorskip("pjsua2")
    from reception.sip import GreetingPlayer, import_pjsua2

    pj = import_pjsua2()
    greeting = load_greeting(DEFAULT_GREETING_PATH)
    eof = {"n": 0}
    endpoint = pj.Endpoint()
    endpoint.libCreate()
    player = None
    config = pj.EpConfig()
    config.uaConfig.threadCnt = 0
    config.uaConfig.mainThreadOnly = True
    config.logConfig.level = 1
    config.logConfig.consoleLevel = 1
    config.medConfig.clockRate = 16000
    config.medConfig.sndClockRate = 16000
    config.medConfig.channelCount = 1
    config.medConfig.noVad = True
    endpoint.libInit(config)
    try:
        endpoint.audDevManager().setNullDev()
        endpoint.libStart()
        player = GreetingPlayer(pj, lambda: eof.__setitem__("n", eof["n"] + 1))
        player.createPlayer(str(greeting.path), pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(endpoint.audDevManager().getPlaybackDevMedia())
        deadline = time.monotonic() + greeting.duration_s + 4
        while eof["n"] == 0 and time.monotonic() < deadline:
            endpoint.libHandleEvents(50)
        assert eof["n"] == 1
        extra = time.monotonic() + 0.4
        while time.monotonic() < extra:
            endpoint.libHandleEvents(50)
        assert eof["n"] == 1
    finally:
        del player
        endpoint.libDestroy()
