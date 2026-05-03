"""Cost estimation for AI coding sessions based on API pricing."""

from __future__ import annotations

# Per-model pricing (dollars per million tokens).
MODEL_PRICING = {
    "opus": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_creation": 18.75,
    },
    "sonnet": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "haiku": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_creation": 1.00,
    },
}

# Backwards-compatible Claude names.
CLAUDE_MODEL_PRICING = MODEL_PRICING
DEFAULT_PRICING = MODEL_PRICING["sonnet"]

# Standard short-context OpenAI API pricing.
OPENAI_MODEL_PRICING = {
    "gpt-5.5": {
        "input": 5.00,
        "output": 30.00,
        "cache_read": 0.50,
        "cache_creation": 0.0,
    },
    "gpt-5.4": {
        "input": 2.50,
        "output": 15.00,
        "cache_read": 0.25,
        "cache_creation": 0.0,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "output": 4.50,
        "cache_read": 0.075,
        "cache_creation": 0.0,
    },
    "gpt-5.4-nano": {
        "input": 0.20,
        "output": 1.25,
        "cache_read": 0.02,
        "cache_creation": 0.0,
    },
    "gpt-5.3-codex": {
        "input": 1.75,
        "output": 14.00,
        "cache_read": 0.175,
        "cache_creation": 0.0,
    },
}

DEFAULT_OPENAI_PRICING = OPENAI_MODEL_PRICING["gpt-5.3-codex"]
OPENAI_PROVIDERS = {"codex", "openai"}


def _pricing_for_model(
    model: str | None,
    provider: str = "claude",
) -> dict[str, float]:
    """Return the pricing dict for a provider/model string."""
    provider_lower = provider.lower()
    if provider_lower in OPENAI_PROVIDERS:
        return _openai_pricing_for_model(model)

    if not model:
        return DEFAULT_PRICING
    model_lower = model.lower()
    for family in ("opus", "sonnet", "haiku"):
        if family in model_lower:
            return MODEL_PRICING[family]
    return DEFAULT_PRICING


def _openai_pricing_for_model(model: str | None) -> dict[str, float]:
    if not model:
        return DEFAULT_OPENAI_PRICING

    model_lower = model.lower()
    for family in (
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
        "gpt-5.5",
        "gpt-5.4",
    ):
        if family in model_lower:
            return OPENAI_MODEL_PRICING[family]
    return DEFAULT_OPENAI_PRICING


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    model: str | None = None,
    provider: str = "claude",
) -> float:
    """Estimate cost in USD based on token counts, provider, and model.

    Claude logs store non-cached input tokens separately from cache reads.
    Codex/OpenAI logs store total input tokens plus cached input tokens, so
    cached input is subtracted from billable uncached input for OpenAI pricing.
    """
    pricing = _pricing_for_model(model, provider=provider)
    provider_lower = provider.lower()
    billable_input_tokens = input_tokens
    if provider_lower in OPENAI_PROVIDERS:
        billable_input_tokens = max(input_tokens - cache_read_tokens, 0)

    cost = (
        billable_input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["cache_read"] / 1_000_000
        + cache_creation_tokens * pricing["cache_creation"] / 1_000_000
    )
    return round(cost, 4)
