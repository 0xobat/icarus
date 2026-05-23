"""Shared fixtures for the db test suite.

Replaces v4.2's `from tests.test_database import _make_X` cross-file imports
(which assumed a flat tests/ package). Importlib mode + per-package conftest
is the modern pytest pattern.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def snapshot_data_factory():
    """Return a callable that builds a minimal valid portfolio snapshot dict.

    Mirrors `_make_snapshot_data` from test_database.py, exposed via fixture
    so any test in the db/ suite can request it.
    """

    def _build(**overrides):
        data = {
            "total_value_usd": "10000.00",
            "stablecoin_value_usd": "3000.00",
            "deployed_value_usd": "7000.00",
            "positions": [{"protocol": "aave", "value": 7000}],
            "drawdown_from_peak": "0.05",
            "peak_value_usd": "10500.00",
        }
        data.update(overrides)
        return data

    return _build
