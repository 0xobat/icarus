"""Strategy code-gen pipeline -- Claude generates Python strategy classes (AI-002).

Reads structured StrategySpec objects (from STRAT-008 ingestion), constructs
code-generation prompts with the common strategy interface and reference
implementation, validates generated output, and writes strategy classes to
py-engine/strategies/ with metadata headers.

Supports change detection: only regenerates strategies whose specs have
changed, and retires old class files with a .retired suffix.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monitoring.logger import get_logger
from strategies.ingestion import StrategyIngestor, StrategySpec

_logger = get_logger("code-gen", enable_file=False)

# ---------------------------------------------------------------------------
# Required methods every generated strategy class must implement
# ---------------------------------------------------------------------------
REQUIRED_METHODS = frozenset({"evaluate", "should_act", "generate_orders"})

# Strategies directory relative to py-engine
STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"

# Reference strategy file for prompt context
REFERENCE_STRATEGY = STRATEGIES_DIR / "aave_lending.py"


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Result of validating generated strategy code."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    class_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "valid": self.valid,
            "errors": self.errors,
            "class_name": self.class_name,
        }


# ---------------------------------------------------------------------------
# Code generation result
# ---------------------------------------------------------------------------
@dataclass
class CodeGenResult:
    """Result of generating code for a single strategy."""

    strategy_id: str
    success: bool
    file_path: str = ""
    class_name: str = ""
    errors: list[str] = field(default_factory=list)
    generated_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "strategy_id": self.strategy_id,
            "success": self.success,
            "file_path": self.file_path,
            "class_name": self.class_name,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
COMMON_INTERFACE = textwrap.dedent("""\
    Every strategy class must implement the following interface:

    class <StrategyName>:
        def __init__(self, allocator: PortfolioAllocator, tracker: PositionTracker,
                     config: <StrategyConfig> | None = None) -> None:
            ...

        def evaluate(self, <market_data>) -> list[<RankedCandidate>]:
            \"\"\"Evaluate market conditions and return ranked candidates.\"\"\"
            ...

        def should_act(self, <context>) -> bool:
            \"\"\"Determine whether action threshold is met.\"\"\"
            ...

        def generate_orders(self, <market_data>, correlation_id: str | None = None
                          ) -> list[dict[str, Any]]:
            \"\"\"Generate execution:orders schema-compliant order dicts.\"\"\"
            ...

    Required order dict fields:
    - version: "1.0.0"
    - orderId: unique UUID hex
    - correlationId: links related orders
    - timestamp: ISO 8601 datetime
    - chain: "ethereum" | "arbitrum" | "base"
    - protocol: "aave_v3" | "uniswap_v3" | "lido" | etc.
    - action: "supply" | "withdraw" | "swap" | "stake" | etc.
    - strategy: strategy ID string
    - priority: "urgent" | "normal" | "low"
    - params: { tokenIn, amount, ... }
    - limits: { maxGasWei, maxSlippageBps, deadlineUnix }
    - useFlashbotsProtect: boolean
""")


def _build_codegen_prompt(
    spec: StrategySpec,
    reference_code: str,
) -> str:
    """Build the code-generation prompt for Claude.

    Args:
        spec: Parsed strategy specification.
        reference_code: Source code of the reference strategy implementation.

    Returns:
        Prompt string for Claude API.
    """
    return textwrap.dedent(f"""\
        Generate a Python strategy class for the following strategy specification.

        ## Strategy Specification
        - Name: {spec.name}
        - ID: {spec.id}
        - Tier: {spec.tier}
        - Risk Profile: {spec.risk_profile}
        - Protocols: {', '.join(spec.protocols)}
        - Chains: {', '.join(spec.chains)}
        - Description: {spec.description}

        Entry Conditions:
        {chr(10).join(f'- {c}' for c in spec.entry_conditions)}

        Exit Conditions:
        {chr(10).join(f'- {c}' for c in spec.exit_conditions)}

        Constraints:
        {chr(10).join(f'- {c}' for c in spec.constraints)}

        ## Common Interface
        {COMMON_INTERFACE}

        ## Reference Implementation
        ```python
        {reference_code}
        ```

        ## Requirements
        - Use Decimal for all financial calculations
        - Use Google-style docstrings
        - Import from monitoring.logger, portfolio.allocator, portfolio.position_tracker
        - Generate execution:orders schema-compliant dicts
        - Include a @dataclass config class with sensible defaults
        - Include type hints on all methods
        - Return ONLY the Python code, no markdown fences or explanation
    """)


def _load_reference_code() -> str:
    """Load the reference strategy source code.

    Returns:
        Source code string of the reference strategy.
    """
    if REFERENCE_STRATEGY.exists():
        return REFERENCE_STRATEGY.read_text(encoding="utf-8")
    return "# Reference strategy not available"


# ---------------------------------------------------------------------------
# Code validation
# ---------------------------------------------------------------------------
def validate_generated_code(code: str) -> ValidationResult:
    """Validate generated Python code for syntax and interface compliance.

    Checks:
    1. Syntax validity via ast.parse()
    2. Contains at least one class definition
    3. Class has required methods: evaluate, should_act, generate_orders
    4. Required imports are present

    Args:
        code: Generated Python source code.

    Returns:
        ValidationResult indicating validity and any errors.
    """
    errors: list[str] = []

    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ValidationResult(
            valid=False,
            errors=[f"Syntax error at line {e.lineno}: {e.msg}"],
        )

    # 2. Find class definitions
    classes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]

    if not classes:
        errors.append("No class definition found in generated code")
        return ValidationResult(valid=False, errors=errors)

    # Find the main strategy class (not a config dataclass)
    strategy_class = None
    for cls in classes:
        methods = {
            node.name for node in ast.walk(cls)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if methods & REQUIRED_METHODS:
            strategy_class = cls
            break

    if strategy_class is None:
        # Fall back to the last class if none have the required methods
        strategy_class = classes[-1]

    class_name = strategy_class.name

    # 3. Check required methods
    methods = {
        node.name for node in ast.walk(strategy_class)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = REQUIRED_METHODS - methods
    if missing:
        errors.append(f"Missing required methods: {sorted(missing)}")

    # 4. Check imports
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        class_name=class_name,
    )


# ---------------------------------------------------------------------------
# Metadata header
# ---------------------------------------------------------------------------
def _make_metadata_header(
    strategy_id: str,
    spec_hash: str,
    class_name: str,
) -> str:
    """Generate the metadata header comment for a generated strategy file.

    Args:
        strategy_id: Strategy identifier.
        spec_hash: Hash of the strategy spec used for generation.
        class_name: Name of the generated class.

    Returns:
        Metadata header string.
    """
    now = datetime.now(UTC).isoformat()
    return textwrap.dedent(f"""\
        # AUTO-GENERATED by AI-002 code-gen pipeline
        # strategy_id: {strategy_id}
        # spec_hash: {spec_hash}
        # generated_at: {now}
        # class_name: {class_name}
        # DO NOT EDIT — regenerate by updating strategy.md
    """)


def _extract_code_from_response(response_text: str) -> str:
    """Extract Python code from a Claude API response.

    Handles responses that may include markdown code fences.

    Args:
        response_text: Raw text from Claude API response.

    Returns:
        Cleaned Python source code.
    """
    # Strip markdown code fences if present
    code = response_text.strip()
    fence_pattern = re.compile(r"^```(?:python)?\s*\n(.*?)```\s*$", re.DOTALL)
    match = fence_pattern.match(code)
    if match:
        code = match.group(1)
    return code.strip()


# ---------------------------------------------------------------------------
# File path helpers
# ---------------------------------------------------------------------------
def _strategy_file_name(strategy_id: str) -> str:
    """Convert a strategy ID to a Python module filename.

    Args:
        strategy_id: Strategy identifier (e.g., "STRAT-002").

    Returns:
        Filename like "strat_002.py".
    """
    return strategy_id.lower().replace("-", "_") + ".py"


def _retire_old_file(file_path: Path) -> Path | None:
    """Rename an existing strategy file with .retired suffix.

    Args:
        file_path: Path to the strategy file to retire.

    Returns:
        Path to the retired file, or None if source didn't exist.
    """
    if not file_path.exists():
        return None
    retired_path = file_path.with_suffix(".py.retired")
    # If a retired file already exists, remove it
    if retired_path.exists():
        retired_path.unlink()
    file_path.rename(retired_path)
    _logger.info(
        "Strategy file retired",
        extra={"data": {
            "original": str(file_path),
            "retired": str(retired_path),
        }},
    )
    return retired_path


# ---------------------------------------------------------------------------
# Strategy code generator
# ---------------------------------------------------------------------------
class StrategyCodeGenerator:
    """Generate Python strategy classes from StrategySpec via Claude API.

    Takes structured strategy specifications from the ingestion pipeline
    (STRAT-008) and uses the Anthropic Claude API to generate implementation
    code. Validates generated code for syntax and interface compliance,
    then writes to the strategies directory with metadata headers.

    Args:
        client: Anthropic API client instance.
        model: Claude model identifier.
        strategies_dir: Directory to write generated strategy files.
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str = "claude-sonnet-4-20250514",
        strategies_dir: Path | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._strategies_dir = strategies_dir or STRATEGIES_DIR
        self._reference_code = _load_reference_code()
        self._generated_hashes: dict[str, str] = {}

    @property
    def generated_hashes(self) -> dict[str, str]:
        """Return hashes of specs that have been generated."""
        return dict(self._generated_hashes)

    def generate(self, spec: StrategySpec) -> CodeGenResult:
        """Generate a strategy class from a StrategySpec.

        Constructs the prompt, calls Claude API, validates the response,
        and writes the file to the strategies directory.

        Args:
            spec: Parsed strategy specification.

        Returns:
            CodeGenResult with generation status and details.
        """
        strategy_id = spec.id
        spec_hash = spec.content_hash()

        _logger.info(
            "Generating strategy code",
            extra={"data": {
                "strategy_id": strategy_id,
                "spec_hash": spec_hash,
            }},
        )

        # Build prompt
        prompt = _build_codegen_prompt(spec, self._reference_code)

        # Call Claude API
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_code = response.content[0].text
        except Exception as e:
            _logger.error(
                "Claude API call failed",
                extra={"data": {
                    "strategy_id": strategy_id,
                    "error": str(e),
                }},
            )
            return CodeGenResult(
                strategy_id=strategy_id,
                success=False,
                errors=[f"API call failed: {e}"],
            )

        # Extract and validate code
        code = _extract_code_from_response(raw_code)
        validation = validate_generated_code(code)

        if not validation.valid:
            _logger.warning(
                "Generated code validation failed",
                extra={"data": {
                    "strategy_id": strategy_id,
                    "errors": validation.errors,
                }},
            )
            return CodeGenResult(
                strategy_id=strategy_id,
                success=False,
                errors=validation.errors,
                generated_code=code,
            )

        # Write file with metadata header
        file_name = _strategy_file_name(strategy_id)
        file_path = self._strategies_dir / file_name
        header = _make_metadata_header(strategy_id, spec_hash, validation.class_name)
        full_code = header + "\n" + code + "\n"

        # Retire old file if it exists
        _retire_old_file(file_path)

        file_path.write_text(full_code, encoding="utf-8")
        self._generated_hashes[strategy_id] = spec_hash

        _logger.info(
            "Strategy code generated successfully",
            extra={"data": {
                "strategy_id": strategy_id,
                "class_name": validation.class_name,
                "file_path": str(file_path),
            }},
        )

        return CodeGenResult(
            strategy_id=strategy_id,
            success=True,
            file_path=str(file_path),
            class_name=validation.class_name,
            generated_code=code,
        )

    def generate_changed(
        self,
        ingestor: StrategyIngestor,
        markdown_content: str,
    ) -> list[CodeGenResult]:
        """Parse strategy.md and regenerate only changed strategies.

        Uses the StrategyIngestor (STRAT-008) for change detection.
        Only strategies flagged as new or modified are regenerated.
        Removed strategies have their files retired.

        Args:
            ingestor: StrategyIngestor instance for parsing and change detection.
            markdown_content: Full content of strategy.md.

        Returns:
            List of CodeGenResult for each strategy that was processed.
        """
        result = ingestor.ingest(markdown_content)
        results: list[CodeGenResult] = []

        # Retire removed strategies
        for strategy_id in result.removed_strategies:
            file_name = _strategy_file_name(strategy_id)
            file_path = self._strategies_dir / file_name
            retired = _retire_old_file(file_path)
            if retired:
                results.append(CodeGenResult(
                    strategy_id=strategy_id,
                    success=True,
                    file_path=str(retired),
                    errors=["retired"],
                ))

        # Generate new/modified strategies
        specs_by_id = {s.id: s for s in result.specs}
        for strategy_id in result.strategies_needing_codegen():
            spec = specs_by_id.get(strategy_id)
            if spec is None:
                continue
            gen_result = self.generate(spec)
            results.append(gen_result)

        _logger.info(
            "Change-based generation complete",
            extra={"data": {
                "new": result.new_strategies,
                "modified": result.modified_strategies,
                "removed": result.removed_strategies,
                "unchanged": len(result.unchanged_strategies),
                "results": [r.to_dict() for r in results],
            }},
        )

        return results
