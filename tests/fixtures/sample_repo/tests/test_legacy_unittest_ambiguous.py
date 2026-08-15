"""A tiny unittest.TestCase with one assertion the unittest_to_pytest codemod
recipe doesn't recognize, kept around to exercise the AMBIGUOUS reporting
path end to end (this file should be left unconverted)."""

import unittest

from sample_pkg.utils import add


class AmbiguousAssertionTests(unittest.TestCase):
    def test_add_in_expected_set(self):
        # self.assertIn isn't one of the recipe's known rewrites, so this
        # file should be reported as ambiguous rather than partially (or
        # incorrectly) rewritten.
        self.assertIn(add(1, 1), [2, 3, 4])
