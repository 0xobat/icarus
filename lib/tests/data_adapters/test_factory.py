"""Tests for the `build_default_adapter` factory.

Regression test pinning the W4 review fix: backtest-worker's
`_resolve_adapter_from_env` calls this factory; the factory MUST satisfy
the `DataAdapter` Protocol or worker boot fails.
"""

from __future__ import annotations

from icarus.data_adapters import (
    DataAdapter,
    DefiLlamaAdapter,
    build_default_adapter,
)


def test_build_default_adapter_returns_a_real_adapter():
    """W4 review regression: the factory must boot a usable adapter."""
    adapter = build_default_adapter()
    assert isinstance(adapter, DataAdapter)
    # name + historical_supported are the two Protocol attributes
    # backtest-worker / search.py reads at runtime.
    assert isinstance(adapter.name, str) and adapter.name
    assert isinstance(adapter.historical_supported, bool)


def test_build_default_adapter_defaults_to_defillama():
    """v1 default is DefiLlama (no API key required, public API).
    If this changes, audit backtest-worker callsites + .env.example."""
    adapter = build_default_adapter()
    assert isinstance(adapter, DefiLlamaAdapter)
