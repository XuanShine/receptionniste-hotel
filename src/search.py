"""Recherche web xAI, une ou deux phrases à dire au téléphone."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import httpx

logger = logging.getLogger("receptionniste.search")

MODEL = "grok-4-1-fast-non-reasoning"
URL = "https://api.x.ai/v1/responses"
TIMEOUT = 8.0
WAIT_PHRASE = "Je regarde ça."
FAILED = (
    "La recherche n'a pas abouti. "
    "Excuse-toi et propose de passer un collègue. "
    "S'il accepte, appelle transferer_reception."
)


def request_body(question: str) -> dict[str, object]:
    return {
        "model": MODEL,
        "temperature": 0,
        "max_output_tokens": 120,
        "tools": [{"type": "web_search"}],
        "input": [{"role": "user", "content": _prompt(question)}],
    }


def _prompt(question: str) -> str:
    return (
        "Réponds en français, en une ou deux phrases à dire au téléphone. "
        "Pas d'URL.\n"
        f"{question.strip()}"
    )


def answer_text(payload: Mapping[str, object]) -> str | None:
    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "output_text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    if not parts:
        return None
    return " ".join(parts)


async def web_answer(question: str) -> str:
    if not question.strip():
        return FAILED
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        return FAILED
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                URL,
                headers={"Authorization": f"Bearer {key}"},
                json=request_body(question),
            )
    except httpx.HTTPError:
        logger.warning("recherche internet interrompue")
        return FAILED
    if response.status_code != 200:
        logger.error("recherche internet refusée : %s", response.status_code)
        return FAILED
    try:
        payload = response.json()
    except ValueError:
        return FAILED
    if not isinstance(payload, Mapping):
        return FAILED
    text = answer_text(payload)
    return text if text else FAILED
