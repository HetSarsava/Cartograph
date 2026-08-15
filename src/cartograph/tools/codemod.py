"""libcst-based codemod application, driven by a recipe registry.

Codemods never use regex-based text editing — every rewrite goes through
libcst so it operates on a real syntax tree and can report *ambiguity*
(constructs it isn't confident it understood) instead of silently guessing.
`apply_codemod()` is the generic driver: it loads files, hands each one's
CST to a `Recipe`, collects ambiguities, and only writes changes back when
the recipe is confident (subject to whatever ambiguity-tolerance policy is
implemented — see the TODO below).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import libcst as cst
from pydantic import BaseModel, Field


class CodemodAmbiguity(BaseModel):
    """A construct a recipe found but wasn't confident it could safely rewrite.

    Surfaced back to the caller (typically the Adjudicator agent) instead of
    guessing, so a human or LLM can make the call explicitly.
    """

    file_path: str
    line: int | None = Field(default=None, description="1-indexed line, if localized.")
    description: str = Field(description="What's ambiguous and why the recipe stopped.")
    candidates: list[str] = Field(
        default_factory=list, description="Possible resolutions, if any are known."
    )


class CodemodResult(BaseModel):
    """The outcome of an `apply_codemod()` call."""

    recipe_name: str
    applied: bool = Field(description="False if changes were withheld due to ambiguities.")
    files_changed: list[str] = Field(default_factory=list)
    diff: str | None = Field(default=None, description="Unified diff of all changes, if applied.")
    ambiguities: list[CodemodAmbiguity] = Field(default_factory=list)


@runtime_checkable
class Recipe(Protocol):
    """A single codemod recipe: detects applicability and transforms a module.

    Implementations should be pure with respect to the filesystem — they
    receive a parsed `cst.Module` and return a new one plus any ambiguities;
    `apply_codemod()` owns reading/writing files.
    """

    name: str
    description: str

    def applies_to(self, file_path: str, module: cst.Module) -> bool:
        """Return True if this recipe has anything to do in `module`."""
        ...

    def transform(
        self, file_path: str, module: cst.Module
    ) -> tuple[cst.Module, list[CodemodAmbiguity]]:
        """Return a rewritten module plus any ambiguities encountered.

        If ambiguities is non-empty, `apply_codemod()` may choose not to
        write the result back (policy TODO — see `apply_codemod`).
        """
        ...


_RECIPE_REGISTRY: dict[str, Recipe] = {}


def register_recipe(recipe: Recipe) -> None:
    """Register a recipe so it can be looked up by name in `apply_codemod`."""
    _RECIPE_REGISTRY[recipe.name] = recipe


def get_recipe(name: str) -> Recipe | None:
    """Look up a registered recipe by name, or None if not found."""
    return _RECIPE_REGISTRY.get(name)


def list_recipes() -> list[str]:
    """Return the names of all currently registered recipes."""
    return sorted(_RECIPE_REGISTRY)


def apply_codemod(
    repo_path: str | Path,
    recipe_name: str,
    target_files: list[str] | None = None,
) -> CodemodResult:
    """Apply a registered recipe to files in `repo_path`.

    Args:
        repo_path: Root of the repo to modify.
        recipe_name: Name of a recipe registered via `register_recipe`.
        target_files: Repo-relative paths to consider. If None, the recipe's
            `applies_to` is used to scan the whole repo for candidates.

    Returns:
        A `CodemodResult` describing what changed (or would have changed).

    Raises:
        KeyError: If `recipe_name` isn't registered.
        NotImplementedError: Always, for now — see TODO below.
    """
    recipe = get_recipe(recipe_name)
    if recipe is None:
        raise KeyError(f"No recipe registered as {recipe_name!r}. Known: {list_recipes()}")

    # TODO: implement the driver:
    #   1. Resolve target_files (walk repo_path for *.py if None), parse each
    #      with cst.parse_module(path.read_text()).
    #   2. For each module where recipe.applies_to(...) is True, call
    #      recipe.transform(...) and collect (new_module, ambiguities).
    #   3. Decide an ambiguity-tolerance policy — e.g. "withhold writes for
    #      any file that produced ambiguities, but still report them" is the
    #      safe default; a stricter/looser policy can be a parameter later.
    #   4. Write back accepted files (new_module.code), compute a unified
    #      diff (difflib.unified_diff against the original text) for the
    #      returned CodemodResult.diff.
    #   5. Return CodemodResult with applied=True only if at least one file
    #      was actually written.
    raise NotImplementedError(f"apply_codemod: driver not yet implemented (recipe={recipe_name!r})")
