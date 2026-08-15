"""A small service layer that composes the helpers in `utils.py`."""

from sample_pkg.utils import add, double


class Calculator:
    """Performs a couple of toy arithmetic operations."""

    def compute(self, x: int, y: int) -> int:
        """Add `x` and `y`, then double the result."""
        total = add(x, y)
        return double(total)

    def compute_twice(self, x: int, y: int) -> int:
        """Call `compute` twice and sum the results."""
        return self.compute(x, y) + self.compute(x, y)
