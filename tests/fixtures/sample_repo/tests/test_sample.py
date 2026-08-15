"""A couple of real, passing pytest tests against sample_pkg — used to exercise
`tools.test_runner.run_tests` against a real (if tiny) repo."""

from sample_pkg.service import Calculator
from sample_pkg.utils import add, double


def test_add():
    assert add(2, 3) == 5


def test_double():
    assert double(4) == 8


def test_calculator_compute():
    assert Calculator().compute(2, 3) == 10


def test_calculator_compute_twice():
    assert Calculator().compute_twice(2, 3) == 20
