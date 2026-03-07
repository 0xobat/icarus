"""Strategy ingestion -- parses STRATEGY.md into structured specs (STRAT-008).

Reads the human-authored STRATEGY.md markdown file, extracts per-strategy
structured specifications, validates required fields, detects changes against
previously stored versions, and flags strategies for code-gen or retirement.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("strategy-ingestion", enable_file=False)

# ---------------------------------------------------------------------------
# Known protocols and valid tier values
# ---------------------------------------------------------------------------

KNOWN_PROTOCOLS = frozenset({
    "aave", "aave_v3", "uniswap", "uniswap_v3",
    "lido", "compound", "curve", "balancer",
    "maker", "sushiswap", "yearn", "convex",
    "flashbots",
})

KNOWN_CHAINS = frozenset({
    "ethereum", "sepolia", "arbitrum", "base",
    "optimism", "polygon",
})

VALID_TIERS = frozenset({1, 2, 3})

REQUIRED_SPEC_FIELDS = frozenset({
    "name", "id", "tier",
})


# ---------------------------------------------------------------------------
# Strategy spec dataclass
# ---------------------------------------------------------------------------

@dataclass
class StrategySpec:
    """Structured representation of a single strategy from STRATEGY.md."""

    name: str
    id: str
    tier: int
    risk_profile: str = ""
    protocols: list[str] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    entry_conditions: list[str] = field(default_factory=list)
    exit_conditions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategySpec:
        """Construct from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def content_hash(self) -> str:
        """Compute a hash of the spec content for change detection."""
        d = self.to_dict()
        # Stable serialization: sort keys
        import json
        raw = json.dumps(d, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Change detection result
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    """Result of comparing parsed specs against stored versions."""

    specs: list[StrategySpec]
    new_strategies: list[str] = field(default_factory=list)
    modified_strategies: list[str] = field(default_factory=list)
    removed_strategies: list[str] = field(default_factory=list)
    unchanged_strategies: list[str] = field(default_factory=list)
    validation_errors: dict[str, list[str]] = field(default_factory=dict)

    def has_changes(self) -> bool:
        """Check if any strategies were added, modified, or removed."""
        return bool(
            self.new_strategies or self.modified_strategies or self.removed_strategies
        )

    def strategies_needing_codegen(self) -> list[str]:
        """Return IDs of strategies that need code generation."""
        return self.new_strategies + self.modified_strategies

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "specs": [s.to_dict() for s in self.specs],
            "new": self.new_strategies,
            "modified": self.modified_strategies,
            "removed": self.removed_strategies,
            "unchanged": self.unchanged_strategies,
            "validation_errors": self.validation_errors,
        }


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def _normalize_id(name: str) -> str:
    """Generate a strategy ID from a name if not explicitly provided.

    Args:
        name: Human-readable strategy name.

    Returns:
        Normalized ID like "aave-lending-optimization".
    """
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return normalized


def _extract_tier(text: str) -> int | None:
    """Extract tier number from text like 'Tier 1' or 'tier: 2'.

    Args:
        text: Text that may contain tier information.

    Returns:
        Tier number (1-3) or None if not found.
    """
    match = re.search(r"[Tt]ier\s*:?\s*(\d)", text)
    if match:
        tier = int(match.group(1))
        if tier in VALID_TIERS:
            return tier
    return None


def _extract_list_items(text: str) -> list[str]:
    """Extract markdown list items from a text block.

    Args:
        text: Text containing markdown list items.

    Returns:
        List of item strings.
    """
    items: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        match = re.match(r"^[-*]\s+(.+)", line)
        if match:
            items.append(match.group(1).strip())
    return items


def _extract_protocols(text: str) -> list[str]:
    """Extract recognized protocol names from text.

    Args:
        text: Text that may reference DeFi protocols.

    Returns:
        List of recognized protocol names.
    """
    found: list[str] = []
    text_lower = text.lower()
    for proto in KNOWN_PROTOCOLS:
        # Match protocol name with word boundaries
        pattern = re.escape(proto).replace("_", "[_ ]")
        if re.search(pattern, text_lower):
            found.append(proto)
    # Also match common variations
    if "aave" in text_lower and "aave_v3" not in found and "aave" not in found:
        found.append("aave")
    if "uniswap" in text_lower and "uniswap_v3" not in found and "uniswap" not in found:
        found.append("uniswap")
    return sorted(set(found))


def _extract_chains(text: str) -> list[str]:
    """Extract recognized chain names from text.

    Args:
        text: Text that may reference blockchain networks.

    Returns:
        List of recognized chain names.
    """
    found: list[str] = []
    text_lower = text.lower()
    for chain in KNOWN_CHAINS:
        if chain in text_lower:
            found.append(chain)
    return sorted(set(found))


def _extract_risk_profile(text: str) -> str:
    """Extract risk profile description from text.

    Args:
        text: Text that may contain risk information.

    Returns:
        Risk profile string like "low", "medium", "high", or extracted text.
    """
    text_lower = text.lower()
    if "low risk" in text_lower or "low-risk" in text_lower:
        return "low"
    if "high risk" in text_lower or "high-risk" in text_lower or "higher risk" in text_lower:
        return "high"
    if "medium risk" in text_lower or "medium-risk" in text_lower or "moderate" in text_lower:
        return "medium"
    return ""


def parse_strategy_md(content: str) -> list[StrategySpec]:
    """Parse STRATEGY.md markdown content into a list of StrategySpec objects.

    Expects strategies defined as H2 (##) or H3 (###) sections with metadata
    in the body. Extracts name, tier, protocols, chains, conditions, etc.

    Args:
        content: Full content of STRATEGY.md.

    Returns:
        List of parsed StrategySpec objects.
    """
    specs: list[StrategySpec] = []

    # Split into sections by H2 or H3 headers
    # Each section that looks like a strategy definition gets parsed
    sections = re.split(r"(?=^#{2,3}\s+)", content, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract header
        header_match = re.match(r"^#{2,3}\s+(.+)", section)
        if not header_match:
            continue

        header = header_match.group(1).strip()

        # Skip non-strategy sections (like "Overview", "Risk Management", etc.)
        # Strategies typically have tier info or protocol references
        body = section[header_match.end():]
        tier = _extract_tier(section)

        # If no tier found, try inferring from context
        if tier is None:
            # Check if this looks like a strategy section at all
            has_protocol = bool(_extract_protocols(body))
            has_conditions = bool(re.search(
                r"(?:entry|exit|condition|trigger|when|if)", body, re.IGNORECASE,
            ))
            if not has_protocol and not has_conditions:
                continue
            # Default to tier 2 if it looks like a strategy but no tier specified
            tier = 2

        # Extract strategy ID -- look for explicit ID pattern or generate
        id_match = re.search(r"(?:ID|id|Strategy ID):\s*(\S+)", body)
        strategy_id = id_match.group(1) if id_match else _normalize_id(header)

        # Extract sub-sections
        entry_conditions: list[str] = []
        exit_conditions: list[str] = []
        constraints: list[str] = []
        description_lines: list[str] = []

        # Parse sub-sections within the strategy body
        sub_sections = re.split(r"(?=^#{3,4}\s+|\*\*[A-Z])", body, flags=re.MULTILINE)

        for sub in sub_sections:
            sub_lower = sub.lower()
            items = _extract_list_items(sub)
            if "entry" in sub_lower or "trigger" in sub_lower or "when to enter" in sub_lower:
                entry_conditions.extend(items)
            elif "exit" in sub_lower or "close" in sub_lower or "when to exit" in sub_lower:
                exit_conditions.extend(items)
            elif "constraint" in sub_lower or "limit" in sub_lower or "rule" in sub_lower:
                constraints.extend(items)
            else:
                # First non-list paragraph as description
                for line in sub.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        if not line.startswith("*") or not line.endswith("*"):
                            description_lines.append(line)

        # Also extract list items directly from body if no sub-sections found
        if not entry_conditions and not exit_conditions:
            all_items = _extract_list_items(body)
            for item in all_items:
                item_lower = item.lower()
                if "entry" in item_lower or "enter" in item_lower or "when" in item_lower:
                    entry_conditions.append(item)
                elif "exit" in item_lower or "close" in item_lower or "stop" in item_lower:
                    exit_conditions.append(item)
                elif "limit" in item_lower or "max" in item_lower or "constraint" in item_lower:
                    constraints.append(item)

        protocols = _extract_protocols(section)
        chains = _extract_chains(section)
        risk_profile = _extract_risk_profile(section)

        spec = StrategySpec(
            name=header,
            id=strategy_id,
            tier=tier,
            risk_profile=risk_profile,
            protocols=protocols,
            chains=chains or ["ethereum"],
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            constraints=constraints,
            description=" ".join(description_lines[:3]) if description_lines else "",
        )
        specs.append(spec)

    return specs


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------

def validate_spec(spec: StrategySpec) -> tuple[bool, list[str]]:
    """Validate a single StrategySpec for required fields and value ranges.

    Args:
        spec: The strategy spec to validate.

    Returns:
        Tuple of (valid, list_of_errors).
    """
    errors: list[str] = []
    if not spec.name:
        errors.append("name is required")
    if not spec.id:
        errors.append("id is required")
    if spec.tier not in VALID_TIERS:
        errors.append(f"tier must be one of {sorted(VALID_TIERS)}, got {spec.tier}")
    for proto in spec.protocols:
        if proto not in KNOWN_PROTOCOLS:
            errors.append(f"Unrecognized protocol: {proto}")
    for chain in spec.chains:
        if chain not in KNOWN_CHAINS:
            errors.append(f"Unrecognized chain: {chain}")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Strategy ingestor
# ---------------------------------------------------------------------------

class StrategyIngestor:
    """Parses STRATEGY.md and detects changes against stored versions.

    Manages the full ingestion pipeline: parse markdown, validate specs,
    compare against previous state, and flag strategies for code-gen
    or retirement.

    Args:
        stored_hashes: Previously stored spec hashes keyed by strategy ID.
            Used for change detection. Pass empty dict for first run.
    """

    def __init__(self, stored_hashes: dict[str, str] | None = None) -> None:
        self._stored_hashes: dict[str, str] = stored_hashes or {}
        self._last_specs: dict[str, StrategySpec] = {}

    @property
    def stored_hashes(self) -> dict[str, str]:
        """Return the current stored hashes."""
        return dict(self._stored_hashes)

    @property
    def last_specs(self) -> dict[str, StrategySpec]:
        """Return specs from the most recent ingestion."""
        return dict(self._last_specs)

    def ingest(self, markdown_content: str) -> IngestionResult:
        """Parse STRATEGY.md, validate, and detect changes.

        Args:
            markdown_content: Full content of STRATEGY.md.

        Returns:
            IngestionResult with parsed specs, change classifications,
            and any validation errors.
        """
        # Step 1: Parse markdown into specs
        specs = parse_strategy_md(markdown_content)

        # Step 2: Validate all specs
        valid_specs: list[StrategySpec] = []
        validation_errors: dict[str, list[str]] = {}

        for spec in specs:
            is_valid, errors = validate_spec(spec)
            if is_valid:
                valid_specs.append(spec)
            else:
                validation_errors[spec.id or spec.name] = errors
                _logger.warning(
                    "Strategy spec validation failed",
                    extra={"data": {
                        "strategy": spec.id or spec.name,
                        "errors": errors,
                    }},
                )

        # Step 3: Detect changes
        current_ids = {spec.id for spec in valid_specs}
        stored_ids = set(self._stored_hashes.keys())

        new_strategies: list[str] = []
        modified_strategies: list[str] = []
        unchanged_strategies: list[str] = []
        removed_strategies: list[str] = sorted(stored_ids - current_ids)

        current_hashes: dict[str, str] = {}

        for spec in valid_specs:
            h = spec.content_hash()
            current_hashes[spec.id] = h

            if spec.id not in stored_ids:
                new_strategies.append(spec.id)
            elif h != self._stored_hashes.get(spec.id):
                modified_strategies.append(spec.id)
            else:
                unchanged_strategies.append(spec.id)

        # Step 4: Update stored state
        self._stored_hashes = current_hashes
        self._last_specs = {spec.id: spec for spec in valid_specs}

        result = IngestionResult(
            specs=valid_specs,
            new_strategies=new_strategies,
            modified_strategies=modified_strategies,
            removed_strategies=removed_strategies,
            unchanged_strategies=unchanged_strategies,
            validation_errors=validation_errors,
        )

        # Step 5: Log results
        if result.has_changes():
            _logger.info(
                "Strategy changes detected",
                extra={"data": {
                    "new": new_strategies,
                    "modified": modified_strategies,
                    "removed": removed_strategies,
                    "unchanged_count": len(unchanged_strategies),
                }},
            )
        else:
            _logger.debug(
                "No strategy changes detected",
                extra={"data": {"strategy_count": len(valid_specs)}},
            )

        # Step 6: Flag removed strategies for retirement
        for strategy_id in removed_strategies:
            _logger.info(
                "Strategy flagged for retirement",
                extra={"data": {"strategy_id": strategy_id}},
            )

        return result

    def ingest_file(self, file_path: str) -> IngestionResult:
        """Parse a STRATEGY.md file from disk.

        Args:
            file_path: Path to STRATEGY.md file.

        Returns:
            IngestionResult with parsed specs and change classifications.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        return self.ingest(content)
