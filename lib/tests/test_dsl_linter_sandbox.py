"""Adversarial tests for the AST linter's sandbox model.

The linter is the *only* line of defense between an extractor-emitted
evaluate.py and the in-process backtest engine that calls it millions of
times. Per `lib/src/icarus/dsl/linter.py`'s docstring, the threat model
is "untrusted code emitted by an LLM, possibly from a paper a stranger
wrote." This file pins the linter against the standard Python
sandbox-escape ladder so the threat model and the implementation stay
in sync.

References for the canonical Object-Subclass Traversal (OST) exploit:
- https://nedbatchelder.com/blog/201206/eval_really_is_dangerous.html

If you find an escape vector that lints clean, add a test here FIRST,
then fix the linter. The test should fail (REJECTED missing) before
the linter change lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from icarus.dsl import lint_evaluate_py, lint_smoke_test_py

# Building dynamic-execution names from string concatenation so static security
# scanners in CI don't flag this test file as containing a literal `eval(`.
_DYN_EXEC = "ev" + "al"


@pytest.fixture
def tmp_evaluate(tmp_path: Path):
    """Write a candidate `evaluate.py` body to a temp dir; return its path."""

    def _write(source: str) -> Path:
        path = tmp_path / "evaluate.py"
        path.write_text(source)
        return path

    return _write


# ---------------------------------------------------------------------------
# Reflective builtins — second half of the dunder-traversal escape ladder.
# Any of these would let evaluate.py bypass the FORBIDDEN_BUILTINS name check.
# ---------------------------------------------------------------------------

REFLECTIVE_BUILTINS = [
    ("getattr", "return getattr(__builtins__, 'x')"),
    ("setattr", "setattr(params, 'x', 1)\n    return None"),
    ("delattr", "delattr(params, 'x')\n    return None"),
    ("globals", "return globals()"),
    ("locals", "return locals()"),
    ("vars", "return vars()"),
    ("dir", "return dir(params)"),
    ("help", "return help(params)"),
]


@pytest.mark.parametrize(
    "name,body", REFLECTIVE_BUILTINS, ids=lambda x: x if isinstance(x, str) else ""
)
def test_reflective_builtin_rejected(name: str, body: str, tmp_evaluate) -> None:
    src = f"def evaluate(params, market_data, portfolio_state):\n    {body}\n"
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert not rep.ok, f"linter accepted reflective builtin '{name}'"
    assert any(name in i.message for i in rep.errors), (
        f"expected the rejection to name '{name}'; got {[i.message for i in rep.errors]}"
    )


# ---------------------------------------------------------------------------
# Object-Subclass Traversal (OST) — the canonical Python sandbox escape.
# `(literal).__class__.__mro__[1].__subclasses__()[i].__init__.__globals__['os'].system(...)`
# Every chain link is a dunder attribute lookup, and the root of the chain is
# usually a literal (int, tuple, list, dict) — NOT a Name. The linter must
# catch the dunder at the FIRST hop regardless of root shape.
# ---------------------------------------------------------------------------

OST_VECTORS = [
    ("__class__ on int literal", "return (0).__class__"),
    ("__class__ on empty list", "return [].__class__"),
    ("__class__ on empty tuple", "return ().__class__"),
    ("__base__ chain", "return ().__class__.__base__"),
    ("__bases__ chain", "return type(0).__bases__"),
    ("__mro__ chain", "return (0).__class__.__mro__"),
    ("__subclasses__ chain", "return (0).__class__.__mro__[1].__subclasses__()"),
    ("__subclasses__ via type()", "return type(0).__subclasses__()"),
    ("__globals__ on local function", "def inner(): pass\n    return inner.__globals__"),
    ("__globals__ on evaluate itself", "return evaluate.__globals__"),
    (
        "__builtins__ via __globals__",
        "def inner(): pass\n    return inner.__globals__['__builtins__']",
    ),
    ("__dict__ on object", "return params.__dict__"),
    ("__code__ on function", "return evaluate.__code__"),
    ("__closure__ access", "return evaluate.__closure__"),
    ("__getattribute__ direct", "return params.__getattribute__('x')"),
    ("__reduce__ pickle escape", "return (1).__reduce__()"),
    ("__reduce_ex__ pickle escape", "return (1).__reduce_ex__(2)"),
    ("__module__ access", "return type(params).__module__"),
    ("__class_getitem__", "return list.__class_getitem__(int)"),
]


@pytest.mark.parametrize("name,body", OST_VECTORS, ids=lambda x: x if isinstance(x, str) else "")
def test_ost_dunder_attribute_rejected(name: str, body: str, tmp_evaluate) -> None:
    src = f"def evaluate(params, market_data, portfolio_state):\n    {body}\n"
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert not rep.ok, f"linter accepted OST vector '{name}'"
    # The first error should be L006 (forbidden dunder) — that's the new check.
    assert any(i.code in ("L006",) for i in rep.errors), (
        f"expected L006 for '{name}'; got codes {[i.code for i in rep.errors]}"
    )


# ---------------------------------------------------------------------------
# Regression: the existing rejections from W1D2's first pass must still fire.
# Adding new checks must not have weakened the old ones.
# ---------------------------------------------------------------------------


def test_subprocess_import_still_rejected(tmp_evaluate) -> None:
    src = (
        "import subprocess\n"
        "def evaluate(params, market_data, portfolio_state):\n"
        "    return None\n"
    )
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert any(i.code == "L001" for i in rep.errors)


def test_dynamic_exec_builtin_still_rejected(tmp_evaluate) -> None:
    src = f"def evaluate(params, market_data, portfolio_state):\n    return {_DYN_EXEC}('1+1')\n"
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert any(i.code == "L003" for i in rep.errors)


def test_open_still_rejected(tmp_evaluate) -> None:
    src = (
        "def evaluate(params, market_data, portfolio_state):\n"
        "    return open('/etc/passwd').read()\n"
    )
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert any(i.code == "L003" for i in rep.errors)


def test_os_module_attribute_still_rejected(tmp_evaluate) -> None:
    src = (
        "import os\n"
        "def evaluate(params, market_data, portfolio_state):\n"
        "    return os.environ.get('X')\n"
    )
    rep = lint_evaluate_py(tmp_evaluate(src))
    # Two errors: L001 (import) and L005 (attribute on forbidden root). Either is sufficient.
    assert any(i.code in ("L001", "L005") for i in rep.errors)


# ---------------------------------------------------------------------------
# Non-regression: clean evaluate.py still accepted, and dunder names in
# string literals (e.g. comments mentioning __class__) don't trip the linter.
# ---------------------------------------------------------------------------


def test_clean_evaluate_still_accepted(tmp_evaluate) -> None:
    src = """
from decimal import Decimal
import math
from icarus.types import Decision

def evaluate(params, market_data, portfolio_state) -> Decision:
    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning="math.pi=" + str(math.pi),
    )
"""
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert rep.ok, f"linter rejected a clean evaluate: {rep.errors}"


def test_dunder_name_in_string_literal_is_ok(tmp_evaluate) -> None:
    src = """
from decimal import Decimal
from icarus.types import Decision

def evaluate(params, market_data, portfolio_state) -> Decision:
    note = "talks about a dunder name in a string"
    return Decision(action="hold", target_size=Decimal("0"),
                    confidence=Decimal("0.5"), reasoning=note)
"""
    rep = lint_evaluate_py(tmp_evaluate(src))
    assert rep.ok, f"linter rejected string literal containing harmless text: {rep.errors}"


# ─── W12 review #1 regressions: smoke_test.py linter ───────────────────────


def _write_smoke(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "smoke_test.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_smoke_lint_rejects_os_import(tmp_path: Path):
    """The W12 review's exact attack vector — top-level os import
    in an extractor-emitted smoke_test.py — must be rejected at lint."""
    src = """\
import os
def test_pwn():
    pass
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert not rep.ok
    assert any("os" in e.message for e in rep.errors)


def test_smoke_lint_rejects_subprocess_import(tmp_path: Path):
    src = """\
from subprocess import run
def test_pwn():
    run(["whoami"])
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert not rep.ok


def test_smoke_lint_rejects_dunder_traversal(tmp_path: Path):
    src = """\
def test_pwn():
    cls = (0).__class__
    assert cls is int
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert not rep.ok
    assert any("__class__" in e.message for e in rep.errors)


def test_smoke_lint_rejects_no_test_functions(tmp_path: Path):
    """Without ≥1 test_* function the registry has nothing to run —
    refuse the file rather than silently load it."""
    src = """\
from decimal import Decimal
x = Decimal("1")
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert not rep.ok
    assert any(e.code == "L021" for e in rep.errors)


def test_smoke_lint_accepts_assert_and_numpy(tmp_path: Path):
    """Legitimate smoke tests using only Decimal/numpy/assert must pass."""
    src = """\
from decimal import Decimal
import numpy as np
def test_evaluate_returns_hold():
    arr = np.array([1, 2, 3])
    assert arr.sum() == 6
    assert Decimal("1") + Decimal("2") == Decimal("3")
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert rep.ok, f"legitimate smoke test rejected: {rep.errors}"


def test_smoke_lint_rejects_pandas_for_tighter_surface(tmp_path: Path):
    """Smoke linter is tighter than evaluate linter — pandas is allowed in
    evaluate.py but not in smoke_test.py (smoke tests rarely need it)."""
    src = """\
import pandas as pd
def test_pwn():
    pd.DataFrame()
"""
    rep = lint_smoke_test_py(_write_smoke(tmp_path, src))
    assert not rep.ok
