"""Trivial test to bootstrap the test suite."""

from main import DecisionLoop


def test_decision_loop_importable() -> None:
    assert DecisionLoop is not None
