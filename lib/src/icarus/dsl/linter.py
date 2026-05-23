"""AST linter for templates/<id>/evaluate.py.

Sandbox model: `evaluate.py` is **untrusted code** — it was emitted by an
LLM, possibly from a paper a stranger wrote. We run it in-process for
performance (the backtest engine calls it millions of times per search),
so we cannot rely on OS sandboxing. We rely on static AST checks instead.

What the linter enforces:
  1. Imports come from a strict allowlist.
  2. No `eval`, `exec`, `compile`, or `__import__`.
  3. No attribute access into forbidden modules (process spawning, network,
     fs, threading, secrets, ssl).
  4. No `open()` calls (no fs I/O at all; data comes in via MarketSnapshot).
  5. Module defines exactly one `evaluate` function with the right signature.

What the linter does NOT enforce:
  - Side effects through mutable args (the dataclasses are frozen, so this
    is impossible by construction).
  - CPU time (the backtest engine enforces a per-template compute budget
    by SIGKILL at the process level).
  - Memory (same — process-level cap).

Limitations:
  - A determined attacker can still escape via numpy/pandas C extensions.
    The allowlist + the fact that this is a single-operator personal
    project make that an acceptable v1 risk. v2 hardens with subprocess
    sandboxing or seccomp.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Imports allowed inside evaluate.py. Add to this list with care.
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # stdlib — pure compute
        "math",
        "decimal",
        "datetime",
        "typing",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "functools",
        "itertools",
        "statistics",
        # numerical libs
        "numpy",
        "pandas",
        # the DSL contract types
        "icarus.types",
    }
)

# Builtins evaluate.py may NOT use. These trigger an error even on bare-name use.
FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
)

# Module roots evaluate.py may NOT reach via attribute access.
# Anything imported with these as the root resolves to capability we forbid.
FORBIDDEN_ATTR_ROOTS: frozenset[str] = frozenset(
    {
        # network
        "socket",
        "urllib",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        # process / fs / introspection
        "os",
        "sys",
        "subprocess",
        "shutil",
        "pathlib",
        "io",
        "tempfile",
        "importlib",
        "inspect",
        # threading
        "threading",
        "multiprocessing",
        "asyncio",
        # crypto / secrets
        "secrets",
        "ssl",
    }
)

EXPECTED_FUNCTION_NAME = "evaluate"
EXPECTED_ARG_NAMES: tuple[str, ...] = ("params", "market_data", "portfolio_state")
EXPECTED_RETURN_NAME = "Decision"


@dataclass(frozen=True)
class LintIssue:
    """One problem found by the linter.

    `severity`:
      - "error": the registry refuses to load the template.
      - "warning": the registry loads, but the operator sees it in webapp.
    """

    severity: str  # "error" | "warning"
    line: int
    col: int
    code: str  # short stable identifier, e.g. "L001"
    message: str


@dataclass(frozen=True)
class LintReport:
    """Result of linting one evaluate.py file."""

    path: Path
    issues: Sequence[LintIssue] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[LintIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[LintIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


class _LintVisitor(ast.NodeVisitor):
    """AST walker that records issues.

    Stateful: tracks names imported from forbidden modules to catch
    `from <bad> import x` then `x(...)` later. The `_forbidden_aliases`
    set holds every local name bound to a forbidden module or symbol.
    """

    def __init__(self) -> None:
        self.issues: list[LintIssue] = []
        self._forbidden_aliases: set[str] = set()
        self._evaluate_found = False

    # --- Imports ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            local = alias.asname or alias.name.split(".")[0]
            if not self._is_allowed_import(name):
                self._error(
                    node,
                    "L001",
                    f"import of '{name}' is not on the DSL allowlist",
                )
                self._forbidden_aliases.add(local)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if not self._is_allowed_import(module):
            self._error(
                node,
                "L002",
                f"import-from '{module}' is not on the DSL allowlist",
            )
            for alias in node.names:
                local = alias.asname or alias.name
                self._forbidden_aliases.add(local)
        self.generic_visit(node)

    @staticmethod
    def _is_allowed_import(name: str) -> bool:
        if not name:
            return False
        # Top-level allowed module
        if name in ALLOWED_IMPORTS:
            return True
        # Submodule of an allowed module (e.g. numpy.linalg)
        for allowed in ALLOWED_IMPORTS:
            if name.startswith(f"{allowed}."):
                return True
        return False

    # --- Forbidden builtins / attribute reach ---

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_BUILTINS:
            self._error(node, "L003", f"forbidden builtin '{node.id}'")
        if node.id in self._forbidden_aliases:
            self._error(
                node,
                "L004",
                f"reference to forbidden symbol '{node.id}' (imported from a forbidden module)",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Walk down to the root Name to detect attribute reach into forbidden
        # modules even when bound to an unfamiliar local alias.
        root = self._root_name(node)
        if root and root in FORBIDDEN_ATTR_ROOTS:
            self._error(
                node,
                "L005",
                f"attribute access on forbidden module '{root}'",
            )
        self.generic_visit(node)

    @staticmethod
    def _root_name(node: ast.AST) -> str | None:
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id
        return None

    # --- Function definition checks ---

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == EXPECTED_FUNCTION_NAME:
            self._evaluate_found = True
            args = [a.arg for a in node.args.args]
            if tuple(args) != EXPECTED_ARG_NAMES:
                self._error(
                    node,
                    "L010",
                    (
                        f"evaluate() must take exactly "
                        f"{EXPECTED_ARG_NAMES} positional args; got {tuple(args)}"
                    ),
                )
            if node.returns is None:
                self._warning(node, "L011", "evaluate() should be annotated with `-> Decision`")
            elif self._return_name(node.returns) != EXPECTED_RETURN_NAME:
                self._warning(
                    node,
                    "L012",
                    f"evaluate() should return `Decision`, got `{self._return_name(node.returns)}`",
                )
        self.generic_visit(node)

    @staticmethod
    def _return_name(annotation: ast.AST) -> str:
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return annotation.attr
        if isinstance(annotation, ast.Subscript):
            return _LintVisitor._return_name(annotation.value)
        return ast.unparse(annotation)

    # --- emit helpers ---

    def _error(self, node: ast.AST, code: str, message: str) -> None:
        self.issues.append(
            LintIssue(
                severity="error",
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                code=code,
                message=message,
            )
        )

    def _warning(self, node: ast.AST, code: str, message: str) -> None:
        self.issues.append(
            LintIssue(
                severity="warning",
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                code=code,
                message=message,
            )
        )


def lint_evaluate_py(path: Path) -> LintReport:
    """Lint a single evaluate.py file.

    Raises FileNotFoundError if `path` does not exist. Returns a LintReport
    on every other outcome (including syntax errors, which become L000).
    """

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return LintReport(
            path=path,
            issues=(
                LintIssue(
                    severity="error",
                    line=e.lineno or 0,
                    col=e.offset or 0,
                    code="L000",
                    message=f"syntax error: {e.msg}",
                ),
            ),
        )

    visitor = _LintVisitor()
    visitor.visit(tree)

    if not visitor._evaluate_found:
        visitor.issues.append(
            LintIssue(
                severity="error",
                line=0,
                col=0,
                code="L020",
                message=(
                    f"file does not define an `{EXPECTED_FUNCTION_NAME}()` function — "
                    "templates must export exactly one"
                ),
            )
        )

    return LintReport(path=path, issues=tuple(visitor.issues))
