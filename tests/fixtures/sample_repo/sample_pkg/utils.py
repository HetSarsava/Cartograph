"""Small, dependency-free helper functions."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def double(x: int) -> int:
    """Return `x` doubled, implemented in terms of `add`."""
    return add(x, x)
