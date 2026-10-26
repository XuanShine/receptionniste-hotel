import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import agent  # noqa: E402
import search  # noqa: E402


def _payload(text: str) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


class _Response:
    def __init__(self, status: int, payload: object, *, bad_json: bool = False) -> None:
        self.status_code = status
        self._payload = payload
        self._bad_json = bad_json

    def json(self) -> object:
        if self._bad_json:
            raise ValueError("bad")
        return self._payload


class _Client:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        status: int = 200,
        payload: object | None = None,
        bad_json: bool = False,
    ) -> None:
        self.error = error
        self.status = status
        self.payload = payload
        self.bad_json = bad_json
        self.timeouts: list[object] = []
        self.posts: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def factory(self):
        outer = self

        class _Factory:
            def __init__(self, *args: object, **kwargs: object) -> None:
                outer.timeouts.append(kwargs.get("timeout"))

            async def __aenter__(self) -> "_Factory":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ) -> _Response:
                outer.posts.append((url, headers, json))
                if outer.error is not None:
                    raise outer.error
                return _Response(outer.status, outer.payload, bad_json=outer.bad_json)

        return _Factory


class _Speech:
    async def wait_for_playout(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str, allow_interruptions: bool = True) -> _Speech:
        self.said.append(text)
        return _Speech()


class _Ctx:
    def __init__(self) -> None:
        self.session = _Session()


class WebSearchTest(unittest.IsolatedAsyncioTestCase):
    def test_tool_takes_a_question(self) -> None:
        signature = inspect.signature(agent.Receptionniste().rechercher_internet)
        self.assertEqual(list(signature.parameters), ["ctx", "question"])
        instructions = (SRC / "instructions.md").read_text(encoding="utf-8")
        for fragment in (
            "rechercher_internet",
            "ont toujours priorité sur internet",
            "Je ne peux vous renseigner que sur l'hôtel et Grasse",
            "Ne répète jamais un avis négatif trouvé en ligne",
            "trouvés sur internet",
            "Je regarde ça.",
            "ne dis jamais de mal de l'hôtel",
            "chambre au calme",
            "N'estime jamais",
        ):
            self.assertIn(fragment, instructions)

    async def test_request_uses_web_search_and_an_eight_second_timeout(self) -> None:
        client = _Client(payload=_payload("Le musée est ouvert jusqu'à 18 heures."))
        with (
            patch.dict(os.environ, {"XAI_API_KEY": "test-key"}),
            patch.object(search.httpx, "AsyncClient", client.factory()),
        ):
            answer = await search.web_answer("Le musée est-il ouvert ?")

        self.assertEqual(answer, "Le musée est ouvert jusqu'à 18 heures.")
        self.assertEqual(client.timeouts, [8.0])
        self.assertEqual(len(client.posts), 1)
        url, headers, body = client.posts[0]
        self.assertEqual(url, "https://api.x.ai/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "grok-4-1-fast-non-reasoning")
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        prompt = body["input"][0]["content"]
        self.assertIn("français", prompt)
        self.assertIn("une ou deux phrases", prompt)
        self.assertIn("Pas d'URL", prompt)
        self.assertIn("Le musée est-il ouvert ?", prompt)

    async def test_timeout_offers_a_colleague(self) -> None:
        client = _Client(error=httpx.TimeoutException("délai"))
        with (
            patch.dict(os.environ, {"XAI_API_KEY": "test-key"}),
            patch.object(search.httpx, "AsyncClient", client.factory()),
        ):
            answer = await search.web_answer("Météo à Grasse ?")

        self.assertEqual(answer, search.FAILED)
        self.assertIn("collègue", answer)
        self.assertIn("transferer_reception", answer)

    async def test_errors_offer_a_colleague(self) -> None:
        cases = (
            _Client(error=httpx.ConnectError("coupé")),
            _Client(status=500, payload={"output": []}),
            _Client(payload=_payload("ignoré"), bad_json=True),
            _Client(payload={"output": []}),
            _Client(payload="pas un objet"),
        )
        for client in cases:
            with self.subTest(client=client.error or client.status or client.bad_json):
                with (
                    patch.dict(os.environ, {"XAI_API_KEY": "test-key"}),
                    patch.object(search.httpx, "AsyncClient", client.factory()),
                ):
                    answer = await search.web_answer("Bus pour la gare ?")
                self.assertEqual(answer, search.FAILED)

    async def test_tool_says_the_wait_phrase(self) -> None:
        client = _Client(payload=_payload("La gare est à une quinzaine de minutes à pied."))
        ctx = _Ctx()
        with (
            patch.dict(os.environ, {"XAI_API_KEY": "test-key"}),
            patch.object(search.httpx, "AsyncClient", client.factory()),
        ):
            answer = await agent.Receptionniste().rechercher_internet(
                ctx,
                "Comment aller à la gare ?",
            )

        self.assertEqual(ctx.session.said, ["Je regarde ça."])
        self.assertEqual(answer, "La gare est à une quinzaine de minutes à pied.")


if __name__ == "__main__":
    unittest.main()
