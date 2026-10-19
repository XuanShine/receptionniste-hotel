"""Pipeline vocal de l'appel : Cartesia entend et parle, Grok répond."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from livekit.agents import AgentSession, inference, llm
from livekit.plugins import xai

_LANGUAGE = "fr"
_STT_MODEL = "cartesia/ink-2"
_TTS_MODEL = "cartesia/sonic-3"
_GROK_MODEL = "grok-4.3"
_HENRI = UUID("d9f4af15-c402-4f50-bbda-d8823d028d6a")


@dataclass(frozen=True)
class VoicePipeline:
    """La clé xAI est la seule entrée du repli. Absente, LiveKit Inference sert Grok seul."""

    voice: UUID
    xai_api_key: str | None = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> VoicePipeline:
        """Une voix illisible arrête le worker au démarrage, plutôt qu'un appel muet."""
        raw_voice = env.get("CARTESIA_VOICE_ID", "").strip()
        try:
            voice = UUID(raw_voice) if raw_voice else _HENRI
        except ValueError:
            raise ValueError(
                "CARTESIA_VOICE_ID doit être un identifiant de voix Cartesia (UUID)"
            ) from None
        xai_api_key = env.get("XAI_API_KEY", "").strip() or None
        return cls(voice=voice, xai_api_key=xai_api_key)

    def new_session(self) -> AgentSession:
        """vad et turn_handling restent absents pour garder le VAD silero et le TurnDetector du SDK, seuil français 0.2850."""
        return AgentSession(
            stt=inference.STT(model=_STT_MODEL, language=_LANGUAGE),
            llm=_grok(self.xai_api_key),
            tts=inference.TTS(
                model=_TTS_MODEL,
                voice=str(self.voice),
                language=_LANGUAGE,
            ),
        )


def _grok(xai_api_key: str | None) -> llm.LLM:
    """Avec une clé xAI, le plugin répond quand Inference échoue. Les deux chemins dérivent l'identifiant de _GROK_MODEL."""
    primary = inference.LLM(model=f"xai/{_GROK_MODEL}")
    if xai_api_key is None:
        return primary
    return llm.FallbackAdapter(
        [primary, xai.responses.LLM(model=_GROK_MODEL, api_key=xai_api_key)]
    )
