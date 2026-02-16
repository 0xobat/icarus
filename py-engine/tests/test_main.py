"""Trivial test to bootstrap the test suite."""

from main import SERVICE_NAME


def test_service_name() -> None:
    assert SERVICE_NAME == "py-engine"
