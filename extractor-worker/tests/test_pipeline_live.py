"""Live end-to-end pipeline test — calls the real Anthropic API.

Skipped if ANTHROPIC_API_KEY is not set. When run, costs roughly
$0.20-0.50 per invocation depending on whether the model needs repair
attempts. Marked `live` per the pyproject's marker registry.

This is the test that proves the prompt + parser + validators + repair
loop actually compose against a real frontier model. No mock can.
"""

from __future__ import annotations

import os

import pytest
from extractor_worker.frontier import AnthropicClient
from extractor_worker.pipeline import extract_with_repair

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set; live frontier tests skipped",
    ),
]


# A tiny "paper" — one paragraph describing a trivial strategy. Keeps token
# cost low while exercising the full pipeline. The model is asked for a
# strategy with parameter ranges, so even on this minimal source it must
# emit a valid manifest + a valid evaluate.py.
_TINY_SOURCE = """\
Title: USDC Supply APY Rotation

Strategy: Supply USDC to the highest-APY lending pool on Base among a small
allowlist of blue-chip protocols (Aave V3, Moonwell). Rotate when the best
APY exceeds the current pool's APY by a configurable threshold.

Parameters:
  - apy_threshold: minimum APY spread to trigger rotation. Typical range
    0.5% to 5%. Higher values reduce churn but capture less yield.
  - min_pool_tvl_usd: minimum pool TVL for inclusion. Typical range $500K
    to $5M. Higher values reduce slippage but narrow the universe.

The strategy holds at most one position at a time. Decisions are emitted
hourly. No leverage. No tokens other than USDC.
"""


async def test_extraction_end_to_end_live():
    client = AnthropicClient()
    extracted = await extract_with_repair(
        client=client,
        template_id="TEST-LIVE-001",
        source_type="blog_url",
        source_ref="https://example.com/test-live-001",
        source_text=_TINY_SOURCE,
        chain_hint="base",
    )

    assert extracted.template_id == "TEST-LIVE-001"
    assert extracted.manifest.id == "TEST-LIVE-001"
    assert extracted.manifest.chain == "base"
    # The model should infer a low-risk APY rotation belongs to a stable-
    # focused protocol family; we don't assert the exact protocol because
    # the LLM has latitude. We DO assert the 4-file contract held.
    assert "def evaluate" in extracted.evaluate_py
    assert "MarketSnapshot" in extracted.evaluate_py or "market_data" in extracted.evaluate_py
    assert extracted.parameter_rationale_md.strip()
    assert extracted.attempts >= 1
    assert extracted.attempts <= 3
    assert extracted.total_input_tokens > 0
    assert extracted.total_output_tokens > 0
