"""Groq implementation of the `Provider` interface.

Groq is the primary/default provider (free-tier friendly, fast inference).
This is a thin adapter over the `groq` SDK: it does no agent-level
reasoning, just message-passing and structured-output parsing/validation.
"""

from __future__ import annotations

import json

from groq import Groq
from pydantic import ValidationError

from cartograph.providers.base import LLMMessage, Provider, ProviderError, ResponseModelT


class GroqProvider:
    """Calls the Groq chat completions API. Implements `Provider`."""

    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        """
        Args:
            api_key: Groq API key.
            model: Default model id to use for all calls (e.g. 'llama-3.3-70b-versatile').
        """
        self._client = Groq(api_key=api_key)
        self._model = model

    def complete_text(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - normalize all SDK errors
            raise ProviderError(f"Groq completion failed: {exc}") from exc

        content = completion.choices[0].message.content
        if content is None:
            raise ProviderError("Groq completion returned no content")
        return content

    def complete_structured(
        self,
        messages: list[LLMMessage],
        response_model: type[ResponseModelT],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ResponseModelT:
        schema_message = LLMMessage(
            role="system",
            content=(
                "Respond with ONLY a single JSON object matching this JSON Schema. "
                "No prose, no markdown fences.\n\n"
                f"{json.dumps(response_model.model_json_schema())}"
            ),
        )
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": m.role, "content": m.content} for m in (schema_message, *messages)
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - normalize all SDK errors
            raise ProviderError(f"Groq structured completion failed: {exc}") from exc

        content = completion.choices[0].message.content
        if content is None:
            raise ProviderError("Groq completion returned no content")
        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            raise ProviderError(
                f"Groq response did not match {response_model.__name__}: {exc}"
            ) from exc


# Statically verify GroqProvider satisfies the Provider protocol.
_: type[Provider] = GroqProvider
