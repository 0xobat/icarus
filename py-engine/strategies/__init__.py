"""Strategy engine — signal generation, portfolio optimization.

Provides the Strategy protocol, data types, and auto-discovery of
strategy classes from this directory.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING

from strategies.aave_lending import AaveLendingStrategy
from strategies.aerodrome_lp import AerodromeLpStrategy
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    Observation,
    PoolState,
    Recommendation,
    Signal,
    SignalType,
    Strategy,
    StrategyReport,
    TokenPrice,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "AaveLendingStrategy",
    "AerodromeLpStrategy",
    "GasInfo",
    "MarketSnapshot",
    "Observation",
    "PoolState",
    "Recommendation",
    "Signal",
    "SignalType",
    "Strategy",
    "StrategyReport",
    "TokenPrice",
    "discover_strategies",
]


def discover_strategies() -> dict[str, type]:
    """Auto-discover strategy classes from the strategies/ directory.

    Scans all modules in this package for classes that satisfy the
    Strategy protocol (have strategy_id, eval_interval, data_window
    properties and an evaluate method). Skips the base module.

    Returns:
        Dictionary mapping strategy_id to the strategy class.
        Strategy IDs are read from class instances or class-level attributes.
    """
    import strategies as pkg

    discovered: dict[str, type] = {}

    for importer, modname, ispkg in pkgutil.iter_modules(
        pkg.__path__, prefix="strategies."
    ):
        # Skip base module and __init__
        if modname in ("strategies.base", "strategies.__init__"):
            continue

        try:
            module = importlib.import_module(modname)
        except Exception:  # noqa: BLE001
            continue

        for _name, obj in inspect.getmembers(module, inspect.isclass):
            # Skip imported classes (only register classes defined in this module)
            if obj.__module__ != modname:
                continue

            # Check if the class satisfies the Strategy protocol
            if _is_strategy_class(obj):
                # Try to get strategy_id from the class
                sid = _get_strategy_id(obj)
                if sid is not None:
                    discovered[sid] = obj

    return discovered


def _is_strategy_class(cls: type) -> bool:
    """Check if a class satisfies the Strategy protocol.

    Checks for the presence of strategy_id, eval_interval, data_window,
    and evaluate attributes/methods.

    Args:
        cls: Class to check.

    Returns:
        True if the class has all required Strategy protocol members.
    """
    required_attrs = ("strategy_id", "eval_interval", "data_window")
    for attr in required_attrs:
        if not hasattr(cls, attr):
            return False

    if not hasattr(cls, "evaluate") or not callable(getattr(cls, "evaluate", None)):
        return False

    return True


def _get_strategy_id(cls: type) -> str | None:
    """Extract strategy_id from a strategy class.

    Tries property/descriptor access first, then falls back to
    instantiation with no args.

    Args:
        cls: Strategy class to extract ID from.

    Returns:
        The strategy_id string, or None if it cannot be determined.
    """
    # Check if strategy_id is a class-level attribute (not a property)
    if isinstance(inspect.getattr_static(cls, "strategy_id", None), str):
        return inspect.getattr_static(cls, "strategy_id")

    # Check if there's a default value we can read without instantiation
    # For properties, we need to try instantiation
    try:
        instance = cls()
        return instance.strategy_id
    except Exception:  # noqa: BLE001
        pass

    return None
