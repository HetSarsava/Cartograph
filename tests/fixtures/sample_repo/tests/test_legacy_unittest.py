"""A tiny unittest.TestCase suite that the unittest_to_pytest codemod recipe
should fully convert to plain pytest asserts (no ambiguous assertions here)."""

import unittest

from sample_pkg.service import Calculator
from sample_pkg.utils import add, double


class CalculatorTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_double(self):
        self.assertEqual(double(4), 8)

    def test_is_positive(self):
        self.assertTrue(add(2, 3) > 0)

    def test_not_negative(self):
        self.assertFalse(add(-1, -1) > 0)

    def test_compute_raises_on_bad_input(self):
        with self.assertRaises(TypeError):
            Calculator().compute("x", 1)
