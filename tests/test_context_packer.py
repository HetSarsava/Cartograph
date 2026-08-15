"""Tests for tools.context_packer's stubbed signature.

The degradation-stage loop isn't implemented yet (see the module docstring
in `tools/context_packer.py`); this pass only asserts the function is
importable, callable with valid Pydantic inputs, and clearly not-yet-done.
"""

import pytest
from pydantic import ValidationError

from cartograph.graph.models import SymbolGraph
from cartograph.tools.context_packer import ContextPackRequest, pack_context


def test_pack_context_not_yet_implemented():
    graph = SymbolGraph(root_path=".")
    request = ContextPackRequest(
        task_description="demo task", symbol_ids=["a.py::foo"], max_tokens=1000
    )
    with pytest.raises(NotImplementedError):
        pack_context(graph, request)


def test_context_pack_request_requires_positive_max_tokens():
    with pytest.raises(ValidationError):
        ContextPackRequest(task_description="demo", symbol_ids=[], max_tokens=0)
