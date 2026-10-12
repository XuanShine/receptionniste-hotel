"""Automate d'appel, sans socket et sans pjsua2."""

from reception.flow import CallAction, IncomingCallFlow
from reception.greeting import DEFAULT_GREETING_PATH, load_greeting


def _flow() -> IncomingCallFlow:
    return IncomingCallFlow(load_greeting(DEFAULT_GREETING_PATH))


def test_answer_play_once_then_hangup():
    flow = _flow()
    assert flow.on_incoming().action is CallAction.ANSWER
    assert flow.on_incoming().action is CallAction.IGNORE

    play = flow.on_media_active()
    assert play.action is CallAction.PLAY
    assert play.wav_path == DEFAULT_GREETING_PATH.resolve()
    assert flow.on_media_active().action is CallAction.IGNORE

    assert flow.on_playback_finished().action is CallAction.HANGUP
    assert flow.on_playback_finished().action is CallAction.IGNORE


def test_remote_hangup_suppresses_bye():
    flow = _flow()
    flow.on_incoming()
    flow.on_media_active()
    assert flow.on_remote_disconnected().action is CallAction.IGNORE
    assert flow.on_playback_finished().action is CallAction.IGNORE
    assert flow.on_media_active().action is CallAction.IGNORE


def test_does_not_play_before_answer():
    flow = _flow()
    assert flow.on_media_active().action is CallAction.IGNORE
