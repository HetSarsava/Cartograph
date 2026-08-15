"""LLM-backed agents. Each is a thin function: (Provider, request model) -> response model.

Agents never import a provider SDK directly (see `cartograph.providers`) and
never touch LangGraph state directly — `pipeline.nodes` is the only place
that adapts between `RunState` and these request/response models.
"""
