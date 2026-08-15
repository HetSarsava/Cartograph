"""Provider-agnostic interface for calling an LLM.

Every concrete provider (Groq today, others later) implements `Provider`.
Agent modules depend only on this interface and on Pydantic models for their
own request/response shapes — never on a provider SDK directly — so a
provider can be swapped by changing what gets injected, not by editing agent
code.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class LLMMessage(BaseModel):
    """A single message in a chat-style LLM conversation."""

    role: Literal["system", "user", "assistant"]
    content: str


class ProviderError(RuntimeError):
    """Raised when a provider call fails (network, auth, or invalid response)."""


@runtime_checkable
class Provider(Protocol):
    """The interface every LLM provider implementation must satisfy."""

    name: str

    def complete_text(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Send `messages` to the model and return its raw text response.

        Args:
            messages: The conversation so far, in order.
            temperature: Sampling temperature.
            max_tokens: Optional cap on generated tokens.

        Returns:
            The model's response text.

        Raises:
            ProviderError: On any failure to obtain a response.
        """
        ...

    def complete_structured(
        self,
        messages: list[LLMMessage],
        response_model: type[ResponseModelT],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ResponseModelT:
        """Send `messages` to the model and parse its response into `response_model`.

        Args:
            messages: The conversation so far, in order.
            response_model: A Pydantic v2 model describing the expected shape.
            temperature: Sampling temperature.
            max_tokens: Optional cap on generated tokens.

        Returns:
            A validated instance of `response_model`.

        Raises:
            ProviderError: If the call fails or the response can't be
                validated against `response_model`.
        """
        ...
