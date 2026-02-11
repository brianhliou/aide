"""Cost estimation for Claude Code sessions based on API pricing."""

from __future__ import annotations

# Default pricing per million tokens (Sonnet-level)
DEFAULT_PRICING = {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_creation": 3.75,
}


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate cost in USD based on token counts and default API pricing.

    Returns float rounded to 4 decimal places.
    """
    cost = (
        input_tokens * DEFAULT_PRICING["input"] / 1_000_000
        + output_tokens * DEFAULT_PRICING["output"] / 1_000_000
        + cache_read_tokens * DEFAULT_PRICING["cache_read"] / 1_000_000
        + cache_creation_tokens * DEFAULT_PRICING["cache_creation"] / 1_000_000
    )
    return round(cost, 4)
