"""A tiny unittest.TestCase suite that the unittest_to_pytest codemod recipe
should fully convert to plain pytest asserts (no ambiguous assertions here)."""
import pytest

import unittest

from sample_pkg.service import Calculator
from sample_pkg.utils import add, double


class CalculatorTests(unittest.TestCase):
    def test_add(self):
        assert add(2, 3) == 5

    def test_double(self):
        assert double(4) == 8

    def test_not_negative(self):
        assert not (add(-1, -1) > 0)

    def test_compute_raises_on_bad_input(self):
        with pytest.raises(TypeError):
            Calculator().compute("x", 1)
