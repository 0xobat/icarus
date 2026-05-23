"""Template registry — walks templates/ and registers valid templates.

A template directory `templates/<id>/` must contain:
  - manifest.yaml          — parsed and validated against TemplateManifest
  - evaluate.py            — AST-linted, must export `evaluate()`
  - smoke_test.py          — extractor-emitted sanity tests (warn-only W1-W3,
                             blocking gate from W4 per blueprint week-4 milestone)
  - parameter_rationale.md — operator-facing rationale (Q8); inspected by the
                             LLM-as-judge plausibility check, not by this loader

Loader behaviour:
  - Validates each template, accumulates `LoadError` per template that fails,
    keeps loading the rest. The full result lists `loaded` + `failed` so the
    operator can fix one template without all of them refusing to load.
  - The `evaluate` callable is imported via importlib using a synthetic
    module name (`icarus.templates.<id>`). The AST linter has already run
    by the time importlib executes the module body, so untrusted code is
    a bounded risk: only the import-time side effects can fire (which the
    linter blocks for the entire allowlisted import set).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from icarus.dsl.linter import LintReport, lint_evaluate_py
from icarus.dsl.manifest import TemplateManifest
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.yaml"
EVALUATE_NAME = "evaluate.py"
SMOKE_TEST_NAME = "smoke_test.py"
PARAMETER_RATIONALE_NAME = "parameter_rationale.md"

EvaluateCallable = Callable[[dict[str, Any], MarketSnapshot, PortfolioSnapshot], Decision]


@dataclass(frozen=True)
class Template:
    """A loaded, validated template — ready to be called per cycle."""

    manifest: TemplateManifest
    evaluate: EvaluateCallable
    source_dir: Path
    lint_report: LintReport

    @property
    def id(self) -> str:
        return self.manifest.id


@dataclass(frozen=True)
class LoadError:
    """A template that failed to load. The operator sees these in webapp."""

    template_dir: Path
    stage: str  # "manifest" | "lint" | "import" | "structure"
    message: str
    lint_report: LintReport | None = None


@dataclass(frozen=True)
class RegistryLoadResult:
    """Outcome of `TemplateRegistry.load`."""

    loaded: tuple[Template, ...] = field(default_factory=tuple)
    failed: tuple[LoadError, ...] = field(default_factory=tuple)
    warnings: tuple[LintReport, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.failed


class TemplateRegistry:
    """In-memory registry of loaded templates, keyed by template_id.

    The decision-engine builds one of these at startup and queries
    `by_id` per cycle. Hot-reload is intentionally NOT done here — the
    blueprint's week-4 design says the extractor writes new templates
    to disk, and the registry can be reloaded on a `RELOAD` Redis signal
    or on next startup. Live mutation of `_templates` is verboten;
    construct a new registry instance instead.

    `smoke_test_mode`:
      - "warn":     W1-W3 — run smoke tests if present; failures are warnings.
      - "blocking": W4+   — smoke tests must pass or the template is rejected.
    """

    def __init__(self, root: Path, smoke_test_mode: str = "warn") -> None:
        if smoke_test_mode not in ("warn", "blocking"):
            raise ValueError(
                f"smoke_test_mode must be 'warn' or 'blocking', got {smoke_test_mode!r}"
            )
        self._root = root
        self._smoke_test_mode = smoke_test_mode
        self._templates: dict[str, Template] = {}

    def load(self) -> RegistryLoadResult:
        """Walk `root` once and load every template directory found."""

        if not self._root.exists():
            logger.warning("template root %s does not exist; nothing to load", self._root)
            return RegistryLoadResult()

        loaded: list[Template] = []
        failed: list[LoadError] = []
        warnings: list[LintReport] = []

        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            outcome = self._load_one(child)
            if isinstance(outcome, Template):
                loaded.append(outcome)
                if outcome.lint_report.warnings:
                    warnings.append(outcome.lint_report)
                self._templates[outcome.id] = outcome
                logger.info("template_loaded id=%s dir=%s", outcome.id, child)
            else:
                failed.append(outcome)
                logger.error(
                    "template_load_failed dir=%s stage=%s msg=%s",
                    outcome.template_dir,
                    outcome.stage,
                    outcome.message,
                )

        return RegistryLoadResult(
            loaded=tuple(loaded),
            failed=tuple(failed),
            warnings=tuple(warnings),
        )

    def _load_one(self, template_dir: Path) -> Template | LoadError:
        # --- File presence check ---
        manifest_path = template_dir / MANIFEST_NAME
        evaluate_path = template_dir / EVALUATE_NAME
        if not manifest_path.exists():
            return LoadError(template_dir, "structure", f"missing {MANIFEST_NAME}")
        if not evaluate_path.exists():
            return LoadError(template_dir, "structure", f"missing {EVALUATE_NAME}")

        # --- Parse + validate manifest ---
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            return LoadError(template_dir, "manifest", f"YAML parse error: {e}")
        try:
            manifest = TemplateManifest.model_validate(raw)
        except Exception as e:
            return LoadError(template_dir, "manifest", f"manifest validation: {e}")

        # --- AST lint evaluate.py ---
        lint_report = lint_evaluate_py(evaluate_path)
        if not lint_report.ok:
            return LoadError(
                template_dir,
                "lint",
                f"{len(lint_report.errors)} lint error(s); first: "
                f"{lint_report.errors[0].code} {lint_report.errors[0].message}",
                lint_report=lint_report,
            )

        # --- Import evaluate.py into a synthetic module ---
        try:
            evaluate_fn = self._import_evaluate(manifest.id, evaluate_path)
        except Exception as e:
            return LoadError(template_dir, "import", f"importlib failed: {e!r}")

        # --- Smoke test (warn or blocking per mode) ---
        smoke_path = template_dir / SMOKE_TEST_NAME
        if smoke_path.exists():
            smoke_ok, smoke_msg = self._run_smoke(manifest.id, smoke_path)
            if not smoke_ok:
                if self._smoke_test_mode == "blocking":
                    return LoadError(template_dir, "smoke", f"smoke_test failed: {smoke_msg}")
                logger.warning("template_smoke_warn id=%s msg=%s", manifest.id, smoke_msg)

        return Template(
            manifest=manifest,
            evaluate=evaluate_fn,
            source_dir=template_dir,
            lint_report=lint_report,
        )

    @staticmethod
    def _import_evaluate(template_id: str, evaluate_path: Path) -> EvaluateCallable:
        synthetic_name = f"icarus.templates.{template_id.lower().replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(synthetic_name, evaluate_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not build spec for {evaluate_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[synthetic_name] = module
        spec.loader.exec_module(module)
        fn = getattr(module, "evaluate", None)
        if fn is None or not callable(fn):
            raise AttributeError(f"{evaluate_path} did not define a callable 'evaluate'")
        return fn  # type: ignore[return-value]

    @staticmethod
    def _run_smoke(template_id: str, smoke_path: Path) -> tuple[bool, str]:
        """Import smoke_test.py and run every `test_*` function it defines."""
        synthetic_name = f"icarus.templates.{template_id.lower().replace('-', '_')}_smoke"
        try:
            spec = importlib.util.spec_from_file_location(synthetic_name, smoke_path)
            if spec is None or spec.loader is None:
                return False, f"could not load {smoke_path}"
            module = importlib.util.module_from_spec(spec)
            sys.modules[synthetic_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            return False, f"smoke_test import failed: {e!r}"

        tests = [
            (name, fn)
            for name, fn in vars(module).items()
            if name.startswith("test_") and callable(fn)
        ]
        if not tests:
            return False, "smoke_test.py defines no `test_*` functions"

        for name, fn in tests:
            try:
                fn()
            except Exception as e:
                return False, f"{name} raised {type(e).__name__}: {e}"
        return True, f"{len(tests)} smoke test(s) passed"

    # --- Accessors ---

    def by_id(self, template_id: str) -> Template:
        return self._templates[template_id]

    def ids(self) -> Iterable[str]:
        return self._templates.keys()

    def __contains__(self, template_id: object) -> bool:
        return isinstance(template_id, str) and template_id in self._templates

    def __len__(self) -> int:
        return len(self._templates)
