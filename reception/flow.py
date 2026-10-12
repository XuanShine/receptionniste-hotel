"""Décisions d'un appel entrant, sans aucune entrée-sortie SIP.

La v1 répond, joue l'annonce une seule fois, puis raccroche.
Si l'appelant a déjà raccroché, aucun BYE n'est renvoyé.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from reception.greeting import Greeting


class CallAction(Enum):
    ANSWER = "answer"
    PLAY = "play"
    HANGUP = "hangup"
    IGNORE = "ignore"


@dataclass(frozen=True)
class FlowDecision:
    action: CallAction
    wav_path: Path | None = None


class IncomingCallFlow:
    """Automate d'un seul appel. Les méthodes sont idempotentes."""

    def __init__(self, greeting: Greeting) -> None:
        self.greeting = greeting
        self.answered = False
        self.playback_started = False
        self.hangup_requested = False
        self.remote_gone = False

    def on_incoming(self) -> FlowDecision:
        if self.answered or self.remote_gone:
            return FlowDecision(CallAction.IGNORE)
        self.answered = True
        return FlowDecision(CallAction.ANSWER)

    def on_media_active(self) -> FlowDecision:
        if self.remote_gone or self.hangup_requested or self.playback_started:
            return FlowDecision(CallAction.IGNORE)
        if not self.answered:
            return FlowDecision(CallAction.IGNORE)
        self.playback_started = True
        return FlowDecision(CallAction.PLAY, wav_path=self.greeting.path)

    def on_playback_finished(self) -> FlowDecision:
        return self._request_hangup()

    def on_remote_disconnected(self) -> FlowDecision:
        self.remote_gone = True
        self.hangup_requested = True
        return FlowDecision(CallAction.IGNORE)

    def _request_hangup(self) -> FlowDecision:
        if self.hangup_requested or self.remote_gone:
            return FlowDecision(CallAction.IGNORE)
        self.hangup_requested = True
        return FlowDecision(CallAction.HANGUP)
