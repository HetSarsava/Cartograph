"""Entrypoint that wires the service layer together."""

from sample_pkg.service import Calculator


def run() -> int:
    """Build a `Calculator` and return the result of `compute_twice(2, 3)`."""
    calc = Calculator()
    return calc.compute_twice(2, 3)
