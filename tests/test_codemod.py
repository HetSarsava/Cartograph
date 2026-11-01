"""Tests for tools.codemod's driver and the unittest_to_pytest recipe.

Covers the registry wiring, the real `apply_codemod` driver (write vs
withhold-on-ambiguity), and the recipe's rewrite rules directly via libcst
(one case per handled assertion type, plus the ambiguous cases it should
refuse to guess at).
"""

import libcst as cst
import pytest

from cartograph.tools import codemod
from cartograph.tools.recipes.unittest_to_pytest import UnittestToPytestRecipe


def _transform(source: str) -> tuple[str, list[codemod.CodemodAmbiguity]]:
    recipe = UnittestToPytestRecipe()
    module = cst.parse_module(source)
    new_module, ambiguities = recipe.transform("test_x.py", module)
    return new_module.code, ambiguities


# --- registry ---------------------------------------------------------


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


# --- applies_to ---------------------------------------------------------


def test_applies_to_detects_unittest_dotted_base():
    recipe = UnittestToPytestRecipe()
    module = cst.parse_module("import unittest\n\nclass Foo(unittest.TestCase):\n    pass\n")
    assert recipe.applies_to("foo.py", module) is True


def test_applies_to_detects_bare_testcase_import():
    recipe = UnittestToPytestRecipe()
    module = cst.parse_module("from unittest import TestCase\n\nclass Foo(TestCase):\n    pass\n")
    assert recipe.applies_to("foo.py", module) is True


def test_applies_to_false_for_unrelated_module():
    recipe = UnittestToPytestRecipe()
    module = cst.parse_module("def test_add():\n    assert 1 + 1 == 2\n")
    assert recipe.applies_to("foo.py", module) is False


# --- rewrite rules: one case per handled assertion ----------------------


def test_rewrites_assert_equal():
    code, ambiguities = _transform(
        "class T(TestCase):\n    def test_a(self):\n        self.assertEqual(f(x), 5)\n"
    )
    assert ambiguities == []
    assert "assert f(x) == 5" in code
    assert "self.assertEqual" not in code


def test_rewrites_assert_equal_with_message():
    code, ambiguities = _transform(
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        self.assertEqual(f(x), 5, 'should be five')\n"
    )
    assert ambiguities == []
    assert "assert f(x) == 5, 'should be five'" in code


def test_rewrites_assert_true():
    code, ambiguities = _transform(
        "class T(TestCase):\n    def test_a(self):\n        self.assertTrue(ok)\n"
    )
    assert ambiguities == []
    assert "assert ok" in code
    assert "self.assertTrue" not in code


def test_rewrites_assert_false():
    code, ambiguities = _transform(
        "class T(TestCase):\n    def test_a(self):\n        self.assertFalse(x > 0)\n"
    )
    assert ambiguities == []
    assert "assert not (x > 0)" in code
    assert "self.assertFalse" not in code


def test_rewrites_assert_raises_context_manager_and_adds_pytest_import():
    code, ambiguities = _transform(
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            f()\n"
    )
    assert ambiguities == []
    assert "with pytest.raises(ValueError):" in code
    assert "import pytest" in code


def test_added_pytest_import_preserves_module_docstring():
    code, ambiguities = _transform(
        '"""Module docstring."""\n\n'
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            f()\n"
    )
    assert ambiguities == []
    lines = code.splitlines()
    assert lines[0] == '"""Module docstring."""'
    assert "import pytest" in code


def test_does_not_duplicate_existing_pytest_import():
    code, ambiguities = _transform(
        "import pytest\n\n"
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            f()\n"
    )
    assert ambiguities == []
    assert code.count("import pytest") == 1


# --- ambiguous cases: reported, not guessed ------------------------------


def test_unrecognized_assertion_is_ambiguous_and_left_alone():
    source = "class T(TestCase):\n    def test_a(self):\n        self.assertIn(x, [1, 2])\n"
    code, ambiguities = _transform(source)
    assert len(ambiguities) == 1
    assert ambiguities[0].file_path == "test_x.py"
    assert "assertIn" in ambiguities[0].description
    assert "self.assertIn(x, [1, 2])" in code  # left untouched


def test_assert_raises_as_plain_call_is_ambiguous():
    source = (
        "class T(TestCase):\n    def test_a(self):\n        self.assertRaises(ValueError, f, x)\n"
    )
    code, ambiguities = _transform(source)
    assert len(ambiguities) == 1
    assert "plain call" in ambiguities[0].description
    assert "self.assertRaises(ValueError, f, x)" in code


def test_assert_raises_with_as_clause_is_ambiguous():
    source = (
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        with self.assertRaises(ValueError) as cm:\n"
        "            f()\n"
        "        assert cm.exception.args\n"
    )
    code, ambiguities = _transform(source)
    assert len(ambiguities) == 1
    assert "exception" in ambiguities[0].description
    assert "with self.assertRaises(ValueError) as cm:" in code


def test_assert_equal_with_wrong_arg_shape_is_ambiguous():
    source = "class T(TestCase):\n    def test_a(self):\n        self.assertEqual(a, b, c, d)\n"
    code, ambiguities = _transform(source)
    assert len(ambiguities) == 1
    assert "self.assertEqual(a, b, c, d)" in code


def test_ambiguities_have_line_numbers():
    source = "class T(TestCase):\n    def test_a(self):\n        self.assertIn(x, [1, 2])\n"
    _, ambiguities = _transform(source)
    assert ambiguities[0].line == 3


# --- apply_codemod driver: write-vs-withhold policy ----------------------


def test_apply_codemod_writes_fully_convertible_file(tmp_path):
    target = tmp_path / "test_ok.py"
    target.write_text(
        "from unittest import TestCase\n\n"
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        self.assertEqual(1 + 1, 2)\n"
    )

    result = codemod.apply_codemod(tmp_path, "unittest_to_pytest")

    assert result.applied is True
    assert "test_ok.py" in result.files_changed
    assert result.ambiguities == []
    assert result.diff is not None
    assert "assert 1 + 1 == 2" in target.read_text()


def test_apply_codemod_withholds_file_with_ambiguity(tmp_path):
    target = tmp_path / "test_mixed.py"
    original = (
        "from unittest import TestCase\n\n"
        "class T(TestCase):\n"
        "    def test_a(self):\n"
        "        self.assertEqual(1 + 1, 2)\n"
        "    def test_b(self):\n"
        "        self.assertIn(1, [1, 2])\n"
    )
    target.write_text(original)

    result = codemod.apply_codemod(tmp_path, "unittest_to_pytest")

    assert result.applied is False
    assert result.files_changed == []
    assert len(result.ambiguities) == 1
    assert target.read_text() == original  # untouched


def test_apply_codemod_skips_files_the_recipe_does_not_apply_to(tmp_path):
    (tmp_path / "plain.py").write_text("def f():\n    return 1\n")

    result = codemod.apply_codemod(tmp_path, "unittest_to_pytest")

    assert result.applied is False
    assert result.files_changed == []
    assert result.ambiguities == []


# --- path containment ---------------------------------------------------

_UNITTEST_SOURCE = (
    "import unittest\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_a(self):\n"
    "        self.assertEqual(1, 1)\n"
)


def test_apply_codemod_refuses_target_outside_repo_root(tmp_path):
    """`target_files` is untrusted — it comes from LLM-generated plan steps
    and straight off the wire from MCP clients. A `..` segment must not let a
    write land outside the repo, where `tools.snapshot` couldn't roll it back.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(_UNITTEST_SOURCE)

    result = codemod.apply_codemod(repo, "unittest_to_pytest", target_files=["../outside.py"])

    assert outside.read_text() == _UNITTEST_SOURCE  # untouched
    assert result.applied is False
    assert result.files_changed == []
    assert len(result.ambiguities) == 1
    assert "outside the repo root" in result.ambiguities[0].description


def test_apply_codemod_refuses_absolute_target_outside_repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(_UNITTEST_SOURCE)

    result = codemod.apply_codemod(repo, "unittest_to_pytest", target_files=[str(outside)])

    assert outside.read_text() == _UNITTEST_SOURCE
    assert result.files_changed == []
    assert len(result.ambiguities) == 1


def test_apply_codemod_still_accepts_nested_targets_inside_the_repo(tmp_path):
    """The containment check must not reject legitimate subdirectory paths."""
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    target = nested / "test_x.py"
    target.write_text(_UNITTEST_SOURCE)

    result = codemod.apply_codemod(
        tmp_path, "unittest_to_pytest", target_files=["pkg/sub/test_x.py"]
    )

    assert result.applied is True
    assert result.files_changed == ["pkg/sub/test_x.py"]
    assert "assert 1 == 1" in target.read_text()
