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

import difflib
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

import libcst as cst
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Directories never walked when target_files isn't given explicitly.
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".cartograph",
}


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

    Ambiguity policy: any file where `recipe.transform` reports one or more
    `CodemodAmbiguity`s is left untouched on disk (all-or-nothing per file —
    other, unambiguous files still get written). Ambiguities are always
    collected into the returned result regardless of whether the file was
    written.

    Returns:
        A `CodemodResult` describing what changed (or would have changed).

    Raises:
        KeyError: If `recipe_name` isn't registered.
    """
    recipe = get_recipe(recipe_name)
    if recipe is None:
        raise KeyError(f"No recipe registered as {recipe_name!r}. Known: {list_recipes()}")

    root = Path(repo_path).resolve()
    candidates = target_files if target_files is not None else list(_iter_relative_python_files(root))

    files_changed: list[str] = []
    ambiguities: list[CodemodAmbiguity] = []
    diffs: list[str] = []

    for rel_path in sorted(candidates):
        abs_path = root / rel_path
        try:
            original_text = abs_path.read_text(encoding="utf-8")
        except OSError as exc:
            ambiguities.append(
                CodemodAmbiguity(file_path=rel_path, description=f"Could not read file: {exc}")
            )
            continue

        try:
            module = cst.parse_module(original_text)
        except cst.ParserSyntaxError as exc:
            ambiguities.append(
                CodemodAmbiguity(file_path=rel_path, description=f"Could not parse file: {exc}")
            )
            continue

        if not recipe.applies_to(rel_path, module):
            continue

        new_module, file_ambiguities = recipe.transform(rel_path, module)
        if file_ambiguities:
            logger.debug(
                "[tool:apply_codemod] %s: %d ambiguity(ies), withholding write",
                rel_path,
                len(file_ambiguities),
            )
            ambiguities.extend(file_ambiguities)
            continue

        new_text = new_module.code
        if new_text == original_text:
            continue

        abs_path.write_text(new_text, encoding="utf-8")
        files_changed.append(rel_path)
        diffs.append(
            "".join(
                difflib.unified_diff(
                    original_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
            )
        )

    return CodemodResult(
        recipe_name=recipe_name,
        applied=bool(files_changed),
        files_changed=files_changed,
        diff="\n".join(diffs) if diffs else None,
        ambiguities=ambiguities,
    )


def _iter_relative_python_files(root: Path) -> list[str]:
    if root.is_file():
        return [root.name] if root.suffix == ".py" else []
    results = []
    for path in root.rglob("*.py"):
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part in _SKIP_DIR_NAMES for part in rel_parts):
            continue
        results.append(path.relative_to(root).as_posix())
    return results
