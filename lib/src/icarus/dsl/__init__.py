"""DSL runtime — manifest schema, AST linter, template registry.

Three pieces:
  - `manifest`: pydantic models that validate templates/<id>/manifest.yaml.
    Also exports JSON Schema for editor tooling and for the extractor's
    LLM prompt (the prompt embeds the schema verbatim so the LLM emits
    schema-compliant manifests on the first call).

  - `linter`: AST walker that validates templates/<id>/evaluate.py —
    allowlists imports, forbids I/O / network / eval / exec / __import__,
    verifies the `evaluate()` signature matches the contract.

  - `registry`: walks templates/ at startup, validates each template
    (manifest + AST lint + optional smoke test), and registers Template
    objects keyed by template_id.

Stability rule: the manifest model is part of the durable contract.
A breaking change here invalidates every template the extractor has
ever emitted. Bump the manifest's `semver` field on any breaking change
and provide a migration in the registry loader.
"""

from icarus.dsl.linter import (
    LintIssue,
    LintReport,
    lint_evaluate_py,
    lint_smoke_test_py,
)
from icarus.dsl.manifest import (
    CategoricalParam,
    ContinuousParam,
    ExpectedMetrics,
    GridParam,
    ParamSpec,
    Source,
    TemplateManifest,
)
from icarus.dsl.registry import Template, TemplateRegistry, VerdictLookup

__all__ = [
    "CategoricalParam",
    "ContinuousParam",
    "ExpectedMetrics",
    "GridParam",
    "LintIssue",
    "LintReport",
    "ParamSpec",
    "Source",
    "Template",
    "TemplateManifest",
    "TemplateRegistry",
    "VerdictLookup",
    "build_db_verdict_lookup",
    "lint_evaluate_py",
    "lint_smoke_test_py",
]


def build_db_verdict_lookup(db) -> VerdictLookup:  # type: ignore[no-untyped-def]
    """Build a VerdictLookup that reads ``templates.judge_verdict`` from
    the DatabaseManager. Cached per-call inside the closure so a single
    registry load fires one SELECT instead of N.

    The import is lazy + db is untyped here so this module stays free
    of a hard dependency on `icarus.db` (kept importable in test
    contexts that don't have a DB stack wired).
    """
    from sqlalchemy import select

    from icarus.db.models import Template as TemplateRow

    cache: dict[str, str | None] | None = None

    def _lookup(template_id: str) -> str | None:
        nonlocal cache
        if cache is None:
            cache = {}
            with db.get_session() as session:
                rows = session.execute(
                    select(TemplateRow.template_id, TemplateRow.judge_verdict)
                ).all()
                for tid, verdict in rows:
                    cache[tid] = verdict
        return cache.get(template_id)

    return _lookup
