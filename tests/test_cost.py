"""Tests for aide.cost module."""

from aide.cost import DEFAULT_PRICING, estimate_cost


def test_estimate_cost_known_values():
    """Test with known token counts and verify exact dollar amount."""
    # 1000 input tokens at $3/M = $0.003
    # 500 output tokens at $15/M = $0.0075
    cost = estimate_cost(input_tokens=1000, output_tokens=500)
    assert cost == 0.0105


def test_estimate_cost_zeros():
    """All zeros should return 0.0."""
    assert estimate_cost(input_tokens=0, output_tokens=0) == 0.0


def test_estimate_cost_with_cache_tokens():
    """Test with only cache tokens to verify cache pricing."""
    # 10_000 cache_read at $0.30/M = $0.003
    # 5_000 cache_creation at $3.75/M = $0.01875
    cost = estimate_cost(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=10_000,
        cache_creation_tokens=5_000,
    )
    assert cost == 0.0217  # 0.003 + 0.01875 = 0.02175 -> rounds to 0.0217


def test_estimate_cost_all_token_types():
    """Test with all four token types."""
    cost = estimate_cost(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
        cache_creation_tokens=200_000,
    )
    # input: 1M * 3.00/M = 3.00
    # output: 100K * 15.00/M = 1.50
    # cache_read: 500K * 0.30/M = 0.15
    # cache_creation: 200K * 3.75/M = 0.75
    # total = 5.40
    assert cost == 5.4


def test_estimate_cost_rounding():
    """Verify rounding to 4 decimal places."""
    # 1 input token at $3/M = $0.000003 -> rounds to 0.0
    cost = estimate_cost(input_tokens=1, output_tokens=0)
    assert cost == 0.0

    # 333 input tokens at $3/M = $0.000999 -> rounds to 0.001
    cost = estimate_cost(input_tokens=333, output_tokens=0)
    assert cost == 0.001


def test_default_pricing_keys():
    """Verify DEFAULT_PRICING has the expected keys."""
    assert set(DEFAULT_PRICING.keys()) == {"input", "output", "cache_read", "cache_creation"}
