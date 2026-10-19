import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

from livekit.agents import inference, llm  # noqa: E402

import pipeline  # noqa: E402

_HENRI = "d9f4af15-c402-4f50-bbda-d8823d028d6a"


class VoicePipelineTest(unittest.TestCase):
    def test_defaults_to_henri_without_xai(self) -> None:
        plan = pipeline.VoicePipeline.from_env({})
        self.assertEqual(str(plan.voice), _HENRI)
        self.assertIsNone(plan.xai_api_key)

    def test_blank_values_count_as_unset(self) -> None:
        plan = pipeline.VoicePipeline.from_env(
            {"CARTESIA_VOICE_ID": " ", "XAI_API_KEY": ""}
        )
        self.assertEqual(plan, pipeline.VoicePipeline.from_env({}))

    def test_voice_comes_from_the_environment(self) -> None:
        other = "00000000-0000-4000-8000-000000000001"
        plan = pipeline.VoicePipeline.from_env({"CARTESIA_VOICE_ID": other})
        self.assertEqual(str(plan.voice), other)

    def test_rejects_a_voice_that_is_not_an_id(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "CARTESIA_VOICE_ID doit être un identifiant de voix Cartesia \\(UUID\\)",
        ):
            pipeline.VoicePipeline.from_env({"CARTESIA_VOICE_ID": "Henri"})

    def test_key_stays_out_of_logs(self) -> None:
        plan = pipeline.VoicePipeline.from_env({"XAI_API_KEY": "xai-secret"})
        self.assertNotIn("xai-secret", repr(plan))

    def test_grok_voice_is_gone(self) -> None:
        for relative in ("src/agent.py", "README.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("GROK_VOICE", text)


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {
                "LIVEKIT_API_KEY": "pipeline-test-livekit-api-key-0001",
                "LIVEKIT_API_SECRET": "pipeline-test-livekit-api-secret-01",
            },
        )
        self._env.start()

    async def asyncTearDown(self) -> None:
        self._env.stop()

    async def test_cartesia_hears_and_speaks_grok_answers(self) -> None:
        plan = pipeline.VoicePipeline.from_env({})
        session = plan.new_session()
        try:
            self.assertEqual(session.stt.model, "cartesia/ink-2")
            self.assertEqual(session.tts.model, "cartesia/sonic-3")
            self.assertIsInstance(session.llm, inference.LLM)
            self.assertEqual(session.llm.model, "xai/grok-4.3")
            self.assertIsNotNone(session.vad)
            self.assertEqual(session.vad.model, "silero")
            self.assertEqual(str(session.stt._opts.language), "fr")
            self.assertEqual(str(session.tts._opts.language), "fr")
            self.assertEqual(session.tts._opts.voice, str(plan.voice))
        finally:
            await _close_session(session)

    async def test_xai_key_puts_the_plugin_behind_inference(self) -> None:
        session = pipeline.VoicePipeline.from_env(
            {"XAI_API_KEY": "xai-test"}
        ).new_session()
        try:
            self.assertIsInstance(session.llm, llm.FallbackAdapter)
            self.assertEqual(session.llm.model, "xai/grok-4.3")
        finally:
            await _close_session(session)


async def _close_session(session: object) -> None:
    model = session.llm
    await session.aclose()
    if isinstance(model, llm.FallbackAdapter):
        await model.aclose()
        for instance in model._llm_instances:
            await instance.aclose()
        return
    if model is not None:
        await model.aclose()
