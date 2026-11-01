"""Explicitly synthetic, repeatable green-tests/weak-assertions demonstration."""

from __future__ import annotations

from pathlib import Path

from cartograph.review.changes import git
from cartograph.review.engine import review
from cartograph.review.models import ReviewReport


def create_demo(output: Path) -> dict[str, ReviewReport]:
    if output.exists():
        raise ValueError("Demo output already exists; choose a new directory")
    output.mkdir(parents=True)
    reports = {}
    for case in ("weak", "strong"):
        repo = output / case
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / ".gitignore").write_text(".cartograph/\n__pycache__/\n", encoding="utf-8")
        source = (
            "def calculate_total(subtotal, discount=0):\n"
            "    return subtotal\n\n"
            "def checkout(subtotal, discount=0):\n"
            "    return calculate_total(subtotal, discount)\n"
        )
        (repo / "shop.py").write_text(source, encoding="utf-8")
        test = (
            "from shop import checkout\n\n"
            "def test_checkout():\n    assert checkout(100, 0) == 100\n"
        )
        if case == "strong":
            test += "    assert checkout(100, 20) == 80\n"
        (repo / "tests/test_shop.py").write_text(test, encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", ".")
        git(
            repo,
            "-c",
            "user.name=Cartograph Demo",
            "-c",
            "user.email=demo@example.invalid",
            "commit",
            "-qm",
            "Synthetic demo baseline",
        )
        # The proposed patch correctly adds discount handling. The deliberate
        # arithmetic mutation asks whether the tests distinguish subtraction/addition.
        (repo / "shop.py").write_text(
            source.replace("return subtotal\n", "return subtotal - discount\n"),
            encoding="utf-8",
        )
        reports[case] = review(repo, "HEAD", repo / ".cartograph/review", ["tests"])
    return reports
