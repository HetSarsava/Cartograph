"""Codemod recipes for `tools.codemod.apply_codemod`.

Each recipe module should instantiate its `Recipe` implementation and call
`cartograph.tools.codemod.register_recipe(...)` at import time.
"""

from cartograph.tools.recipes.unittest_to_pytest import UnittestToPytestRecipe

__all__ = ["UnittestToPytestRecipe"]
