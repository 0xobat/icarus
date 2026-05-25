"""Frontier prompt templates for paper/blog → 4-file template extraction.

The system prompt embeds three things verbatim so the LLM emits against a
contract that other services already speak:
  1. The v2 type stubs (`MarketSnapshot`, `PortfolioSnapshot`, `Decision`)
     — exact source from `lib/src/icarus/types/`. Mismatches with these
     stubs would make `evaluate.py` uncallable by the backtest engine.
  2. The manifest schema (rendered from `TemplateManifest` pydantic model).
  3. The AST linter rules (`ALLOWED_IMPORTS`, `FORBIDDEN_BUILTINS`, etc.)
     — so the LLM knows the sandbox boundary up front rather than
     learning via the repair loop.

The user prompt carries the source text + template_id + chain_hint. The
output spec demands four code blocks tagged with the filename — this is a
simpler, more reliable structured-output mechanism than tool-use forcing
because the LLM gets exact control over file contents (whitespace,
comments, YAML formatting).

The repair prompt is sent when a previous attempt failed
manifest/lint/smoke-test validation. It carries the validation error
verbatim so the LLM has the same diagnostic the operator would.
"""

from __future__ import annotations

import inspect
import json

from icarus.dsl.linter import (
    ALLOWED_IMPORTS,
    EXPECTED_ARG_NAMES,
    EXPECTED_FUNCTION_NAME,
    FORBIDDEN_ATTR_ROOTS,
    FORBIDDEN_BUILTINS,
    FORBIDDEN_DUNDER_ATTRS,
)
from icarus.dsl.manifest import TemplateManifest
from icarus.types import decision as _decision_mod
from icarus.types import market as _market_mod
from icarus.types import portfolio as _portfolio_mod


def _types_block() -> str:
    """Verbatim source of the three v2 contract types. Embedded so the LLM
    sees the same dataclass definitions every implementer sees."""
    return "\n\n".join(
        [
            inspect.getsource(_market_mod),
            inspect.getsource(_portfolio_mod),
            inspect.getsource(_decision_mod),
        ]
    )


def _manifest_schema_block() -> str:
    """JSON Schema for the manifest. Pydantic emits this from the model,
    so the prompt stays in sync with the validator automatically."""
    return json.dumps(TemplateManifest.model_json_schema(), indent=2)


def _linter_rules_block() -> str:
    return f"""ALLOWED_IMPORTS = {sorted(ALLOWED_IMPORTS)}
FORBIDDEN_BUILTINS = {sorted(FORBIDDEN_BUILTINS)}
FORBIDDEN_ATTR_ROOTS = {sorted(FORBIDDEN_ATTR_ROOTS)}
FORBIDDEN_DUNDER_ATTRS = {sorted(FORBIDDEN_DUNDER_ATTRS)}

EXPECTED_FUNCTION_NAME = {EXPECTED_FUNCTION_NAME!r}
EXPECTED_ARG_NAMES = {EXPECTED_ARG_NAMES!r}"""


SYSTEM_PROMPT = """\
You are an expert quantitative researcher extracting a parameterized DeFi
trading strategy template from a research source (paper / blog).

You will output exactly FOUR files describing one template:
  1. manifest.yaml — the strategy spine (id, params, sources, metrics)
  2. evaluate.py — the strategy logic (single `evaluate()` function)
  3. smoke_test.py — 3-5 unit tests that prove `evaluate()` returns sane
     `Decision`s on canned `MarketSnapshot` + `PortfolioSnapshot` inputs
  4. parameter_rationale.md — free-text justification of each parameter's
     range, citing the source. This artifact is read by a separate
     LLM-as-judge plausibility check that gates the template entering the
     backtest search queue.

# THE CONTRACT YOUR evaluate.py MUST IMPLEMENT

```python
{types_block}
```

`evaluate()` MUST have this exact signature:

```python
def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision: ...
```

# THE MANIFEST SCHEMA YOUR manifest.yaml MUST VALIDATE AGAINST

```json
{manifest_schema_block}
```

Notes on parameters:
  - You MUST emit parameter RANGES, not single fitted values. The
    backtest engine sweeps the range to find empirical winners.
  - Each param's `kind` is one of "grid" (discrete values), "continuous"
    (low/high interval), or "categorical" (string choices).
  - Decimal-valued params (low, high, values) are emitted as JSON
    strings (e.g. "0.005") so the YAML round-trips through Python's
    Decimal type without floating-point loss.

# THE AST SANDBOX YOUR evaluate.py MUST PASS

```python
{linter_rules_block}
```

`evaluate.py` must:
  - import only from ALLOWED_IMPORTS
  - never call anything in FORBIDDEN_BUILTINS as a bare name
  - never reach attributes rooted in FORBIDDEN_ATTR_ROOTS
  - never reference any name in FORBIDDEN_DUNDER_ATTRS (sandbox escape)
  - never call `open()` (no filesystem I/O; data comes via MarketSnapshot)

# OUTPUT FORMAT (STRICT)

Emit EXACTLY four fenced code blocks, in this order, each with the
filename on the opening fence. No prose between blocks; no preamble.

```yaml manifest.yaml
<content>
```

```python evaluate.py
<content>
```

```python smoke_test.py
<content>
```

```markdown parameter_rationale.md
<content>
```
"""


USER_PROMPT_TEMPLATE = """\
Extract a strategy template from the source below.

template_id: {template_id}
chain_hint: {chain_hint}
source_type: {source_type}
source_ref: {source_ref}

# SOURCE
{source_text}
"""


REPAIR_PROMPT_TEMPLATE = """\
Your previous attempt failed validation. Diagnostic:

{error_class}: {error_message}

Your previous attempt was:

{previous_response}

Re-emit ALL FOUR files in the same strict format. Fix the specific
error above. Do not introduce new errors.
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(
        types_block=_types_block(),
        manifest_schema_block=_manifest_schema_block(),
        linter_rules_block=_linter_rules_block(),
    )


def build_user_prompt(
    *,
    template_id: str,
    chain_hint: str | None,
    source_type: str,
    source_ref: str,
    source_text: str,
) -> str:
    return USER_PROMPT_TEMPLATE.format(
        template_id=template_id,
        chain_hint=chain_hint or "(infer from source)",
        source_type=source_type,
        source_ref=source_ref,
        source_text=source_text,
    )


def build_repair_prompt(
    *,
    previous_response: str,
    error_class: str,
    error_message: str,
) -> str:
    return REPAIR_PROMPT_TEMPLATE.format(
        previous_response=previous_response,
        error_class=error_class,
        error_message=error_message,
    )
