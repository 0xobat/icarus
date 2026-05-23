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

from icarus.dsl.linter import LintIssue, LintReport, lint_evaluate_py
from icarus.dsl.manifest import (
    CategoricalParam,
    ContinuousParam,
    ExpectedMetrics,
    GridParam,
    ParamSpec,
    Source,
    TemplateManifest,
)
from icarus.dsl.registry import Template, TemplateRegistry

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
    "lint_evaluate_py",
]
