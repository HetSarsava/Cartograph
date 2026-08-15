"""Token-budget-aware context assembly for LLM calls.

The core idea: an agent asks for context on a set of symbols, and
`pack_context` decides *how much detail* to include for each one so the total
stays under a token budget, instead of either truncating blindly or blowing
the budget. It does this by degrading symbols through stages, cheapest last:

    1. `FULL_SOURCE`             — the symbol's full source text, verbatim.
    2. `SIGNATURES_PLUS_CALLERS` — signature + docstring + a list of caller
                                    names (from `graph.queries.find_callers`),
                                    no body.
    3. `SIGNATURES_ONLY`         — signature + docstring only.
    4. `NAMES_ONLY`              — just the symbol's qualified name and kind.

All requested symbols start at `FULL_SOURCE`. If the packed total would
exceed `max_tokens`, the packer degrades symbols one stage at a time
(lowest-priority symbols first — see `ContextPackRequest.symbol_ids` order,
which callers are expected to provide in descending relevance) until it fits,
or everything is at `NAMES_ONLY` and it still doesn't fit, at which point
`PackedContext.truncated` is set and remaining low-priority symbols are
dropped entirely.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cartograph.graph.models import SymbolGraph


class DegradationStage(str, Enum):
    """How much detail is included for a single symbol in a packed context."""

    FULL_SOURCE = "full_source"
    SIGNATURES_PLUS_CALLERS = "signatures_plus_callers"
    SIGNATURES_ONLY = "signatures_only"
    NAMES_ONLY = "names_only"


class ContextPackRequest(BaseModel):
    """A request to pack context for a task, scoped to a set of symbols."""

    task_description: str = Field(description="What the agent is trying to accomplish.")
    symbol_ids: list[str] = Field(
        description="Symbol ids to include, in descending relevance order."
    )
    max_tokens: int = Field(gt=0, description="Hard budget for the packed context.")


class PackedContextItem(BaseModel):
    """The packed representation of a single symbol."""

    symbol_id: str
    stage_used: DegradationStage
    content: str = Field(description="The rendered text for this symbol at `stage_used`.")
    estimated_tokens: int = Field(ge=0)


class PackedContext(BaseModel):
    """The result of a `pack_context()` call, ready to drop into a prompt."""

    items: list[PackedContextItem]
    total_estimated_tokens: int = Field(ge=0)
    truncated: bool = Field(
        description="True if one or more requested symbols were dropped entirely to fit budget."
    )
    dropped_symbol_ids: list[str] = Field(default_factory=list)


def pack_context(graph: SymbolGraph, request: ContextPackRequest) -> PackedContext:
    """Assemble a token-budgeted context blob for the symbols in `request`.

    See the module docstring for the degradation-stage algorithm. Token
    counts should be estimated with a real tokenizer (or a conservative
    chars-per-token heuristic) once implemented — not yet wired up here.

    Args:
        graph: The symbol graph to pull source/signatures/callers from.
        request: Which symbols to pack, for what task, under what budget.

    Returns:
        A `PackedContext` whose `items` are ordered to match `request.symbol_ids`.

    Raises:
        NotImplementedError: Always, for now.
    """
    # TODO: implement the degradation loop described in the module docstring:
    #   1. Render every requested symbol at FULL_SOURCE (read source_lines from
    #      its file using Symbol.line_start/line_end).
    #   2. Estimate tokens per item (start with len(text) // 4 as a placeholder
    #      heuristic; swap for a real tokenizer later).
    #   3. While total > request.max_tokens: degrade the lowest-priority item
    #      that isn't already at NAMES_ONLY to the next stage, re-render, re-sum.
    #   4. If still over budget with everything at NAMES_ONLY: drop lowest-
    #      priority items entirely, set `truncated=True` and populate
    #      `dropped_symbol_ids`.
    raise NotImplementedError("pack_context: token-budget degradation loop not yet implemented")
