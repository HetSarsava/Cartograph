"""Recipe: rewrite `unittest.TestCase` classes into plain pytest-style tests.

This is the example recipe referenced by `tools.codemod`'s docstrings. It
demonstrates the shape every recipe should have; the actual libcst
transformation logic is intentionally left as a TODO for this scaffolding
pass.

Intended (eventual) behavior:
    - Classes inheriting from `unittest.TestCase` have their `test_*` methods
      lifted to module-level functions (dropping `self`).
    - `self.assertEqual(a, b)`      -> `assert a == b`
    - `self.assertTrue(x)`          -> `assert x`
    - `self.assertFalse(x)`         -> `assert not x`
    - `self.assertRaises(E)` (as a context manager) -> `pytest.raises(E)`
    - `setUp`/`tearDown` methods    -> a module-scoped `@pytest.fixture`
      (autouse=True) that yields between the two bodies.
    - Constructs it can't confidently rewrite (e.g. `assertRaises` used as a
      plain call rather than a context manager, custom assert* helpers,
      multiple inheritance) are reported as `CodemodAmbiguity` instead of
      guessed at.
"""

from __future__ import annotations

import libcst as cst

from cartograph.tools.codemod import CodemodAmbiguity, register_recipe


class UnittestToPytestRecipe:
    """Converts `unittest.TestCase`-based tests to plain pytest style."""

    name = "unittest_to_pytest"
    description = (
        "Rewrites unittest.TestCase classes and self.assert* calls into "
        "module-level pytest test functions and bare `assert` statements."
    )

    def applies_to(self, file_path: str, module: cst.Module) -> bool:
        """Return True if `module` defines or imports a `unittest.TestCase` subclass.

        Args:
            file_path: Repo-relative path of the module (for context only).
            module: The parsed module to inspect.

        Returns:
            True if this recipe has anything to do here.
        """
        # TODO: walk module.body for ClassDef nodes with a base of
        # `unittest.TestCase` / `TestCase` (accounting for `import unittest`
        # vs `from unittest import TestCase`), or return True conservatively
        # and let `transform` no-op when it finds nothing.
        raise NotImplementedError("UnittestToPytestRecipe.applies_to: detection not implemented")

    def transform(
        self, file_path: str, module: cst.Module
    ) -> tuple[cst.Module, list[CodemodAmbiguity]]:
        """Rewrite unittest-style tests in `module` to pytest style.

        Args:
            file_path: Repo-relative path of the module being transformed.
            module: The parsed module to rewrite.

        Returns:
            A tuple of (possibly-unchanged module, ambiguities found). See
            the module docstring for the intended rewrite rules and what
            counts as an ambiguity.
        """
        # TODO: implement via a cst.CSTTransformer, roughly:
        #   - A ClassDef visitor that recognizes TestCase subclasses and
        #     flattens their test_* methods to module-level FunctionDefs.
        #   - A Call visitor/matcher (libcst.matchers is a good fit here) for
        #     self.assert* -> assert-statement rewrites.
        #   - Emit a CodemodAmbiguity (rather than transforming) for any
        #     assert* call whose shape doesn't match a known pattern.
        raise NotImplementedError(
            "UnittestToPytestRecipe.transform: libcst rewrite not implemented"
        )


register_recipe(UnittestToPytestRecipe())
