"""Recipe: rewrite unittest-style assertions to plain pytest asserts.

This pass implements the *assertion-level* rewrite only:
    - `self.assertEqual(a, b[, msg])`      -> `assert a == b[, msg]`
    - `self.assertTrue(x[, msg])`          -> `assert x[, msg]`
    - `self.assertFalse(x[, msg])`         -> `assert not (x)[, msg]`
    - `with self.assertRaises(E):`         -> `with pytest.raises(E):`
      (only the no-`as`-clause, single-positional-arg form; `import pytest`
      is added if the module doesn't already have it)

Deliberately deferred (not in scope for this pass): lifting `test_*`
methods out of their `TestCase` class into module-level functions, and
converting `setUp`/`tearDown` into a pytest fixture. The rewritten `assert`
statements remain valid inside an unmodified `TestCase` subclass — pytest
runs unittest-style classes natively — so this is a real, working
intermediate state, not a half-broken one.

Anything the recipe isn't confident about is reported as a
`CodemodAmbiguity` instead of guessed at:
    - any `self.assertX(...)` name other than the four above
      (e.g. `assertIn`, `assertIsInstance`, custom `assert*` helpers)
    - `self.assertRaises(...)` used as a plain call (the 3-arg
      call-a-callable form) rather than a `with` context manager
    - `self.assertEqual`/`assertTrue`/`assertFalse` called with an
      argument shape that doesn't match `(value[, value], msg=None)`
    - `with self.assertRaises(...) as name:` — unittest exposes the
      caught exception as `name.exception`, pytest as `name.value`;
      rewriting the call but not every downstream `.exception` access
      would produce broken code, so this form is left alone
"""

from __future__ import annotations

from collections.abc import Sequence

import libcst as cst

from cartograph.tools.codemod import CodemodAmbiguity, register_recipe


def _self_assert_name(node: cst.BaseExpression, names: set[str] | None = None) -> str | None:
    """Return the method name if `node` is `self.assertX(...)`, else None."""
    if not isinstance(node, cst.Call):
        return None
    func = node.func
    if not (
        isinstance(func, cst.Attribute)
        and isinstance(func.value, cst.Name)
        and func.value.value == "self"
    ):
        return None
    name = func.attr.value
    if not name.startswith("assert"):
        return None
    if names is not None and name not in names:
        return None
    return name


def _strip_parens(node: cst.BaseExpression) -> cst.BaseExpression:
    if node.lpar or node.rpar:
        return node.with_changes(lpar=[], rpar=[])
    return node


def _ensure_parens(node: cst.BaseExpression) -> cst.BaseExpression:
    if node.lpar and node.rpar:
        return node
    return node.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])


def _args_shape_ok(args: Sequence[cst.Arg], required: int) -> bool:
    """True if `args` has exactly `required` unkeyworded args, plus an optional msg."""
    if len(args) < required or len(args) > required + 1:
        return False
    if any(arg.keyword is not None for arg in args[:required]):
        return False
    if len(args) == required + 1:
        extra = args[required]
        if extra.keyword is not None and extra.keyword.value != "msg":
            return False
    return True


def _has_pytest_import(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if isinstance(small, cst.Import):
                if any(
                    isinstance(alias.name, cst.Name) and alias.name.value == "pytest"
                    for alias in small.names
                ):
                    return True
            elif isinstance(small, cst.ImportFrom):
                if isinstance(small.module, cst.Name) and small.module.value == "pytest":
                    return True
    return False


def _is_docstring_statement(stmt: cst.BaseStatement) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return False
    small = stmt.body[0]
    return isinstance(small, cst.Expr) and isinstance(small.value, cst.BaseString)


def _add_pytest_import(module: cst.Module) -> cst.Module:
    import_stmt = cst.SimpleStatementLine(
        body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("pytest"))])]
    )
    body = list(module.body)
    # Insert after the module docstring, if there is one, so `__doc__` stays intact.
    insert_at = 1 if body and _is_docstring_statement(body[0]) else 0
    new_body = [*body[:insert_at], import_stmt, *body[insert_at:]]
    return module.with_changes(body=new_body)


class _TestCaseFinder(cst.CSTVisitor):
    """Detects whether a module defines any `unittest.TestCase` subclass."""

    def __init__(self) -> None:
        super().__init__()
        self.found = False

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        for arg in node.bases:
            base = arg.value
            if isinstance(base, cst.Name) and base.value == "TestCase":
                self.found = True
            elif isinstance(base, cst.Attribute) and base.attr.value == "TestCase":
                self.found = True


class _AssertRewriter(cst.CSTTransformer):
    """Rewrites `self.assertX(...)` call sites; collects ambiguities it can't."""

    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path
        self.ambiguities: list[CodemodAmbiguity] = []
        self.used_pytest_raises = False

    def _line_of(self, node: cst.CSTNode) -> int | None:
        try:
            return self.get_metadata(cst.metadata.PositionProvider, node).start.line
        except KeyError:
            return None

    def _ambiguous(self, node: cst.CSTNode, description: str) -> None:
        self.ambiguities.append(
            CodemodAmbiguity(file_path=self.file_path, line=self._line_of(node), description=description)
        )

    def leave_Expr(
        self, original_node: cst.Expr, updated_node: cst.Expr
    ) -> cst.BaseSmallStatement:
        name = _self_assert_name(original_node.value)
        if name is None:
            return updated_node
        call = original_node.value
        assert isinstance(call, cst.Call)

        rewritten: cst.Assert | None
        if name == "assertEqual":
            rewritten = self._rewrite_comparison(original_node, call)
        elif name == "assertTrue":
            rewritten = self._rewrite_truthy(original_node, call, negate=False)
        elif name == "assertFalse":
            rewritten = self._rewrite_truthy(original_node, call, negate=True)
        elif name == "assertRaises":
            self._ambiguous(
                original_node,
                "self.assertRaises(...) used as a plain call (3-arg call-a-callable form) "
                "rather than a `with` context manager; not rewritten.",
            )
            rewritten = None
        else:
            self._ambiguous(
                original_node, f"Unrecognized unittest assertion 'self.{name}(...)'; not rewritten."
            )
            rewritten = None

        return rewritten if rewritten is not None else updated_node

    def leave_With(self, original_node: cst.With, updated_node: cst.With) -> cst.With:
        if len(original_node.items) != 1:
            return updated_node
        item = original_node.items[0]
        name = _self_assert_name(item.item, {"assertRaises"})
        if name is None:
            return updated_node
        call = item.item
        assert isinstance(call, cst.Call)

        if item.asname is not None:
            self._ambiguous(
                original_node,
                "with self.assertRaises(...) as <name>: unittest exposes the caught exception "
                "as <name>.exception, pytest.raises as <name>.value — rewriting the call without "
                "also rewriting every downstream attribute access would break the test, so this "
                "form is left alone.",
            )
            return updated_node
        if not _args_shape_ok(call.args, required=1):
            self._ambiguous(
                original_node,
                "self.assertRaises(...) with other than exactly one positional (exception type) "
                "argument; not rewritten.",
            )
            return updated_node

        self.used_pytest_raises = True
        new_call = call.with_changes(func=cst.Attribute(value=cst.Name("pytest"), attr=cst.Name("raises")))
        new_item = updated_node.items[0].with_changes(item=new_call)
        return updated_node.with_changes(items=[new_item])

    def _rewrite_comparison(self, original_node: cst.Expr, call: cst.Call) -> cst.Assert | None:
        args = call.args
        if not _args_shape_ok(args, required=2):
            self._ambiguous(
                original_node,
                "self.assertEqual(...) with unexpected argument shape "
                "(expected first, second[, msg]); not rewritten.",
            )
            return None
        first, second = _strip_parens(args[0].value), _strip_parens(args[1].value)
        msg = args[2].value if len(args) == 3 else None
        test = cst.Comparison(
            left=first, comparisons=[cst.ComparisonTarget(operator=cst.Equal(), comparator=second)]
        )
        return cst.Assert(test=test, msg=msg)

    def _rewrite_truthy(
        self, original_node: cst.Expr, call: cst.Call, *, negate: bool
    ) -> cst.Assert | None:
        args = call.args
        if not _args_shape_ok(args, required=1):
            method = "assertFalse" if negate else "assertTrue"
            self._ambiguous(
                original_node,
                f"self.{method}(...) with unexpected argument shape (expected expr[, msg]); "
                "not rewritten.",
            )
            return None
        expr = _strip_parens(args[0].value)
        msg = args[1].value if len(args) == 2 else None
        test: cst.BaseExpression = expr
        if negate:
            test = cst.UnaryOperation(operator=cst.Not(), expression=_ensure_parens(expr))
        return cst.Assert(test=test, msg=msg)


class UnittestToPytestRecipe:
    """Converts `unittest.TestCase` assertion calls to plain pytest `assert` statements."""

    name = "unittest_to_pytest"
    description = (
        "Rewrites self.assertEqual/assertTrue/assertFalse/assertRaises calls in "
        "unittest.TestCase subclasses into bare `assert` statements and pytest.raises."
    )

    def applies_to(self, file_path: str, module: cst.Module) -> bool:
        """Return True if `module` defines a `unittest.TestCase` subclass.

        Args:
            file_path: Repo-relative path of the module (unused; part of the
                `Recipe` protocol for parity with `transform`).
            module: The parsed module to inspect.

        Returns:
            True if this recipe has anything to do here.
        """
        del file_path
        finder = _TestCaseFinder()
        module.visit(finder)
        return finder.found

    def transform(
        self, file_path: str, module: cst.Module
    ) -> tuple[cst.Module, list[CodemodAmbiguity]]:
        """Rewrite unittest-style assertions in `module` to pytest style.

        Args:
            file_path: Repo-relative path of the module being transformed.
            module: The parsed module to rewrite.

        Returns:
            A tuple of (possibly-unchanged module, ambiguities found). See
            the module docstring for the exact rewrite rules and what counts
            as an ambiguity.
        """
        wrapper = cst.MetadataWrapper(module)
        transformer = _AssertRewriter(file_path)
        new_module = wrapper.visit(transformer)

        if transformer.used_pytest_raises and not _has_pytest_import(new_module):
            new_module = _add_pytest_import(new_module)

        return new_module, transformer.ambiguities


register_recipe(UnittestToPytestRecipe())
