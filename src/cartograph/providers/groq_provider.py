"""Groq implementation of the `Provider` interface.

Groq is the primary/default provider (free-tier friendly, fast inference).
This is a thin adapter over the `groq` SDK: it does no agent-level
reasoning, just message-passing and structured-output parsing/validation.
"""

from __future__ import annotations

import json
import logging
import time

from groq import Groq
from groq.types.chat import ChatCompletion
from pydantic import ValidationError

from cartograph.providers.base import LLMMessage, Provider, ProviderError, ResponseModelT

logger = logging.getLogger(__name__)


class CallStats:
    """Process-wide counters for Groq calls, for end-of-run summaries.

    Every `GroqProvider` instance records into the same module-level
    `call_stats` singleton — a fresh `GroqProvider` is built per agent call
    (see `pipeline.nodes._build_provider`), so per-instance counters
    wouldn't accumulate across a run. Call `call_stats.reset()` before a run
    and read the totals after `graph.invoke(...)` returns.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self.failed_call_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0

    def record_success(self, usage: object) -> None:
        self.call_count += 1
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is not None:
            self.total_prompt_tokens += prompt_tokens
        if completion_tokens is not None:
            self.total_completion_tokens += completion_tokens
        if total_tokens is not None:
            self.total_tokens += total_tokens

    def record_failure(self) -> None:
        self.call_count += 1
        self.failed_call_count += 1

    def reset(self) -> None:
        self.__init__()  # noqa: PLC2801 - simplest correct way to reset all fields


call_stats = CallStats()


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
        start = time.monotonic()
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - normalize all SDK errors
            call_stats.record_failure()
            logger.error(
                "[provider:groq] request failed model=%s latency_ms=%.1f error=%s",
                self._model,
                (time.monotonic() - start) * 1000,
                exc,
            )
            raise ProviderError(f"Groq completion failed: {exc}") from exc

        self._log_usage(completion, start)
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
        start = time.monotonic()
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
            call_stats.record_failure()
            logger.error(
                "[provider:groq] request failed model=%s latency_ms=%.1f error=%s",
                self._model,
                (time.monotonic() - start) * 1000,
                exc,
            )
            raise ProviderError(f"Groq structured completion failed: {exc}") from exc

        self._log_usage(completion, start)
        content = completion.choices[0].message.content
        if content is None:
            raise ProviderError("Groq completion returned no content")
        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            logger.error(
                "[provider:groq] response failed schema validation model=%s expected=%s error=%s",
                self._model,
                response_model.__name__,
                exc,
            )
            raise ProviderError(
                f"Groq response did not match {response_model.__name__}: {exc}"
            ) from exc

    def _log_usage(self, completion: ChatCompletion, start: float) -> None:
        latency_ms = (time.monotonic() - start) * 1000
        usage = getattr(completion, "usage", None)
        call_stats.record_success(usage)
        if usage is not None:
            logger.debug(
                "[provider:groq] response model=%s latency_ms=%.1f prompt_tokens=%d "
                "completion_tokens=%d total_tokens=%d",
                self._model,
                latency_ms,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
            )
        else:
            logger.debug(
                "[provider:groq] response model=%s latency_ms=%.1f (no usage data)",
                self._model,
                latency_ms,
            )


# Statically verify GroqProvider satisfies the Provider protocol.
_: type[Provider] = GroqProvider
