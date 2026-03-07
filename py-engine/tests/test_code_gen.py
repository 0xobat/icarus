"""Tests for AI-002 strategy code-gen pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ai.code_gen import (
    StrategyCodeGenerator,
    ValidationResult,
    _build_codegen_prompt,
    _extract_code_from_response,
    _make_metadata_header,
    _retire_old_file,
    _strategy_file_name,
    validate_generated_code,
)
from strategies.ingestion import StrategyIngestor, StrategySpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    name: str = "Test Strategy",
    strategy_id: str = "STRAT-TEST",
    tier: int = 1,
    **kwargs,
) -> StrategySpec:
    return StrategySpec(
        name=name,
        id=strategy_id,
        tier=tier,
        risk_profile=kwargs.get("risk_profile", "low"),
        protocols=kwargs.get("protocols", ["aave"]),
        chains=kwargs.get("chains", ["base"]),
        entry_conditions=kwargs.get("entry_conditions", ["APR > 3%"]),
        exit_conditions=kwargs.get("exit_conditions", ["APR < 2%"]),
        constraints=kwargs.get("constraints", ["Max 40% in protocol"]),
        description=kwargs.get("description", "A test strategy"),
    )


VALID_GENERATED_CODE = '''
from __future__ import annotations
import uuid
from decimal import Decimal
from typing import Any
from monitoring.logger import get_logger
from portfolio.allocator import PortfolioAllocator
from portfolio.position_tracker import PositionTracker

class TestStrategyConfig:
    pass

class TestStrategy:
    def __init__(self, allocator, tracker):
        self.allocator = allocator
        self.tracker = tracker

    def evaluate(self, markets):
        return markets

    def should_act(self, context):
        return True

    def generate_orders(self, markets, correlation_id=None):
        return []
'''

INVALID_CODE_SYNTAX = "def broken(:\n    pass"

INVALID_CODE_MISSING_METHODS = '''
class TestStrategy:
    def evaluate(self, markets):
        return []
'''


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    content_block = SimpleNamespace(text=response_text)
    response = SimpleNamespace(content=[content_block])
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# Code validation tests
# ---------------------------------------------------------------------------

class TestValidateGeneratedCode:

    def test_valid_code_passes(self) -> None:
        result = validate_generated_code(VALID_GENERATED_CODE)
        assert result.valid
        assert result.class_name == "TestStrategy"
        assert len(result.errors) == 0

    def test_syntax_error_detected(self) -> None:
        result = validate_generated_code(INVALID_CODE_SYNTAX)
        assert not result.valid
        assert any("Syntax error" in e for e in result.errors)

    def test_missing_methods_detected(self) -> None:
        result = validate_generated_code(INVALID_CODE_MISSING_METHODS)
        assert not result.valid
        assert any("Missing required methods" in e for e in result.errors)

    def test_no_class_detected(self) -> None:
        result = validate_generated_code("x = 1\ny = 2\n")
        assert not result.valid
        assert any("No class" in e for e in result.errors)

    def test_empty_code(self) -> None:
        result = validate_generated_code("")
        assert not result.valid

    def test_validation_result_to_dict(self) -> None:
        r = ValidationResult(valid=True, errors=[], class_name="Foo")
        d = r.to_dict()
        assert d["valid"] is True
        assert d["class_name"] == "Foo"


# ---------------------------------------------------------------------------
# Code extraction tests
# ---------------------------------------------------------------------------

class TestExtractCode:

    def test_plain_code(self) -> None:
        code = "class Foo:\n    pass"
        assert _extract_code_from_response(code) == code

    def test_markdown_fences(self) -> None:
        wrapped = "```python\nclass Foo:\n    pass\n```"
        assert _extract_code_from_response(wrapped) == "class Foo:\n    pass"

    def test_bare_fences(self) -> None:
        wrapped = "```\nclass Foo:\n    pass\n```"
        assert _extract_code_from_response(wrapped) == "class Foo:\n    pass"


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------

class TestPromptConstruction:

    def test_contains_spec_details(self) -> None:
        spec = _make_spec()
        prompt = _build_codegen_prompt(spec, "# reference code")
        assert "Test Strategy" in prompt
        assert "STRAT-TEST" in prompt
        assert "aave" in prompt
        assert "evaluate" in prompt
        assert "should_act" in prompt
        assert "generate_orders" in prompt

    def test_contains_reference_code(self) -> None:
        spec = _make_spec()
        ref = "class AaveLendingStrategy:\n    pass"
        prompt = _build_codegen_prompt(spec, ref)
        assert "AaveLendingStrategy" in prompt


# ---------------------------------------------------------------------------
# File naming tests
# ---------------------------------------------------------------------------

class TestFileNaming:

    def test_strategy_id_to_filename(self) -> None:
        assert _strategy_file_name("STRAT-002") == "strat_002.py"
        assert _strategy_file_name("STRAT-TEST") == "strat_test.py"

    def test_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "test.py"
            fp.write_text("# old code")
            retired = _retire_old_file(fp)
            assert retired is not None
            assert retired.exists()
            assert retired.suffix == ".retired"
            assert not fp.exists()

    def test_retirement_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "nonexistent.py"
            assert _retire_old_file(fp) is None


# ---------------------------------------------------------------------------
# Metadata header tests
# ---------------------------------------------------------------------------

class TestMetadataHeader:

    def test_contains_fields(self) -> None:
        header = _make_metadata_header("STRAT-002", "abc123", "LidoStaking")
        assert "STRAT-002" in header
        assert "abc123" in header
        assert "LidoStaking" in header
        assert "AI-002" in header
        assert "generated_at" in header


# ---------------------------------------------------------------------------
# Code generator integration tests
# ---------------------------------------------------------------------------

class TestStrategyCodeGenerator:

    def test_successful_generation(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            spec = _make_spec()
            result = gen.generate(spec)
            assert result.success
            assert result.class_name == "TestStrategy"
            assert Path(result.file_path).exists()
            content = Path(result.file_path).read_text()
            assert "AI-002" in content
            assert "STRAT-TEST" in content

    def test_api_failure(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API down")
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            result = gen.generate(_make_spec())
            assert not result.success
            assert any("API call failed" in e for e in result.errors)

    def test_validation_failure(self) -> None:
        client = _mock_client(INVALID_CODE_SYNTAX)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            result = gen.generate(_make_spec())
            assert not result.success
            assert result.errors

    def test_retires_old_file(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = Path(tmpdir) / "strat_test.py"
            old_file.write_text("# old code")
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            result = gen.generate(_make_spec())
            assert result.success
            assert (Path(tmpdir) / "strat_test.py.retired").exists()

    def test_generated_hashes_tracked(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            gen.generate(_make_spec())
            assert "STRAT-TEST" in gen.generated_hashes

    def test_codegen_result_to_dict(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            result = gen.generate(_make_spec())
            d = result.to_dict()
            assert d["success"] is True
            assert d["strategy_id"] == "STRAT-TEST"


# ---------------------------------------------------------------------------
# Change detection generation tests
# ---------------------------------------------------------------------------

class TestGenerateChanged:

    def test_new_strategies_generated(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            ingestor = StrategyIngestor()
            md = """## Test Strategy
**Tier: 1** | **Risk Profile: Low Risk**

ID: LEND-999

**Protocols:** Aave
**Chains:** Base

**Entry Conditions:**
- APR > 3%

**Exit Conditions:**
- APR < 2%
"""
            results = gen.generate_changed(ingestor, md)
            assert len(results) >= 1
            assert any(r.strategy_id == "LEND-999" for r in results)

    def test_unchanged_not_regenerated(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            # Pre-populate stored hashes
            md = """## Test Strategy
**Tier: 1** | **Risk Profile: Low Risk**

ID: LEND-999

**Protocols:** Aave
**Chains:** Base

**Entry Conditions:**
- APR > 3%

**Exit Conditions:**
- APR < 2%
"""
            ingestor = StrategyIngestor()
            # First run — generates
            gen.generate_changed(ingestor, md)
            # Second run with same content — should not regenerate
            results = gen.generate_changed(ingestor, md)
            assert len(results) == 0

    def test_removed_strategies_retired(self) -> None:
        client = _mock_client(VALID_GENERATED_CODE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = StrategyCodeGenerator(
                client, strategies_dir=Path(tmpdir),
            )
            # Pre-populate: pretend we had a strategy that no longer exists
            old_file = Path(tmpdir) / "strat_old.py"
            old_file.write_text("# old code")

            ingestor = StrategyIngestor(stored_hashes={"STRAT-OLD": "oldhash"})
            md = """## New Strategy
**Tier: 1** | **Risk Profile: Low Risk**

ID: LEND-998

**Protocols:** Aave
**Chains:** Base

**Entry Conditions:**
- APR > 3%
"""
            results = gen.generate_changed(ingestor, md)
            # Should have: 1 retired (STRAT-OLD) + 1 generated (STRAT-NEW)
            retired = [r for r in results if "retired" in r.errors]
            generated = [r for r in results if r.success and "retired" not in r.errors]
            assert len(retired) == 1
            assert len(generated) == 1
