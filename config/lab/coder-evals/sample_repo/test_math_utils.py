"""Minimal tests for coder-evals sample_repo (intentionally fail until bug fixed)."""

from math_utils import add, greet


def test_add():
    assert add(2, 3) == 5


def test_greet():
    assert greet("yonder") == "hello yonder"
