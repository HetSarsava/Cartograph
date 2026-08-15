"""Tests for tools.codemod's driver structure (registry, ambiguity-gating shape).

This pass only covers the stubbed scaffolding — the actual libcst rewrite
logic isn't implemented yet, so these tests assert `NotImplementedError` is
raised where the real logic will eventually go, and that the recipe registry
wiring works.
"""

import libcst as cst
import pytest

from cartograph.tools import codemod
from cartograph.tools.recipes.unittest_to_pytest import UnittestToPytestRecipe


def test_unittest_to_pytest_recipe_is_registered():
    assert "unittest_to_pytest" in codemod.list_recipes()
    recipe = codemod.get_recipe("unittest_to_pytest")
    assert isinstance(recipe, UnittestToPytestRecipe)
    assert isinstance(recipe, codemod.Recipe)


def test_get_recipe_unknown_returns_none():
    assert codemod.get_recipe("does_not_exist") is None


def test_apply_codemod_unknown_recipe_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        codemod.apply_codemod(tmp_path, "does_not_exist")


def test_apply_codemod_known_recipe_not_yet_implemented(tmp_path):
    with pytest.raises(NotImplementedError):
        codemod.apply_codemod(tmp_path, "unittest_to_pytest")


def test_recipe_methods_not_yet_implemented():
    recipe = UnittestToPytestRecipe()
    module = cst.parse_module("")
    with pytest.raises(NotImplementedError):
        recipe.applies_to("foo.py", module)
    with pytest.raises(NotImplementedError):
        recipe.transform("foo.py", module)
