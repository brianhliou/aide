"""Tests for aide.cost module."""

from aide.cost import (
    DEFAULT_OPENAI_PRICING,
    DEFAULT_PRICING,
    MODEL_PRICING,
    OPENAI_MODEL_PRICING,
    _pricing_for_model,
    estimate_cost,
)


class TestEstimateCost:
    def test_known_values_default(self):
        """Sonnet pricing when no model specified."""
        cost = estimate_cost(input_tokens=1000, output_tokens=500)
        assert cost == 0.0105

    def test_zeros(self):
        assert estimate_cost(input_tokens=0, output_tokens=0) == 0.0

    def test_cache_tokens_default(self):
        cost = estimate_cost(
            input_tokens=0, output_tokens=0,
            cache_read_tokens=10_000, cache_creation_tokens=5_000,
        )
        assert cost == 0.0217

    def test_all_token_types_default(self):
        cost = estimate_cost(
            input_tokens=1_000_000, output_tokens=100_000,
            cache_read_tokens=500_000, cache_creation_tokens=200_000,
        )
        assert cost == 5.4

    def test_rounding(self):
        assert estimate_cost(input_tokens=1, output_tokens=0) == 0.0
        assert estimate_cost(input_tokens=333, output_tokens=0) == 0.001


class TestPerModelPricing:
    def test_opus_pricing(self):
        cost = estimate_cost(
            input_tokens=1_000_000, output_tokens=100_000,
            cache_read_tokens=500_000, cache_creation_tokens=200_000,
            model="claude-opus-4-6",
        )
        # input: 1M * 15/M = 15.00
        # output: 100K * 75/M = 7.50
        # cache_read: 500K * 1.50/M = 0.75
        # cache_creation: 200K * 18.75/M = 3.75
        assert cost == 27.0

    def test_sonnet_pricing(self):
        cost = estimate_cost(
            input_tokens=1_000_000, output_tokens=100_000,
            model="claude-sonnet-4-5-20250929",
        )
        # input: 1M * 3/M = 3.00, output: 100K * 15/M = 1.50
        assert cost == 4.5

    def test_haiku_pricing(self):
        cost = estimate_cost(
            input_tokens=1_000_000, output_tokens=100_000,
            model="claude-haiku-4-5-20251001",
        )
        # input: 1M * 0.80/M = 0.80, output: 100K * 4/M = 0.40
        assert cost == 1.2

    def test_unknown_model_uses_sonnet(self):
        cost = estimate_cost(
            input_tokens=1_000_000, output_tokens=0,
            model="some-future-model",
        )
        assert cost == 3.0  # Sonnet input rate

    def test_none_model_uses_sonnet(self):
        cost = estimate_cost(input_tokens=1_000_000, output_tokens=0, model=None)
        assert cost == 3.0

    def test_opus_5x_sonnet(self):
        """Opus should be 5x Sonnet for all token types."""
        tokens = dict(
            input_tokens=1_000_000, output_tokens=1_000_000,
            cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000,
        )
        sonnet = estimate_cost(**tokens, model="claude-sonnet-4-5-20250929")
        opus = estimate_cost(**tokens, model="claude-opus-4-6")
        assert opus == sonnet * 5


class TestOpenAIPricing:
    def test_codex_gpt_5_5_pricing(self):
        cost = estimate_cost(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="gpt-5.5",
            provider="codex",
        )
        # input: 1M * 5/M = 5.00, output: 100K * 30/M = 3.00
        assert cost == 8.0

    def test_codex_cached_input_subtracts_from_uncached_input(self):
        cost = estimate_cost(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=400_000,
            model="gpt-5.5",
            provider="codex",
        )
        # uncached input: 600K * 5/M = 3.00
        # cached input: 400K * 0.50/M = 0.20
        # output: 100K * 30/M = 3.00
        assert cost == 6.2

    def test_openai_model_variants(self):
        assert _pricing_for_model("gpt-5.5", provider="openai") == OPENAI_MODEL_PRICING["gpt-5.5"]
        assert _pricing_for_model("gpt-5.4", provider="openai") == OPENAI_MODEL_PRICING["gpt-5.4"]
        assert (
            _pricing_for_model("gpt-5.4-mini", provider="openai")
            == OPENAI_MODEL_PRICING["gpt-5.4-mini"]
        )
        assert (
            _pricing_for_model("gpt-5.3-codex", provider="codex")
            == OPENAI_MODEL_PRICING["gpt-5.3-codex"]
        )

    def test_unknown_openai_model_uses_codex_default(self):
        cost = estimate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            model="future-codex-model",
            provider="codex",
        )
        assert cost == 1.75


class TestPricingForModel:
    def test_opus_variants(self):
        assert _pricing_for_model("claude-opus-4-6") == MODEL_PRICING["opus"]
        assert _pricing_for_model("claude-opus-4-5-20251101") == MODEL_PRICING["opus"]

    def test_sonnet_variants(self):
        assert _pricing_for_model("claude-sonnet-4-5-20250929") == MODEL_PRICING["sonnet"]

    def test_haiku_variants(self):
        assert _pricing_for_model("claude-haiku-4-5-20251001") == MODEL_PRICING["haiku"]

    def test_none_returns_default(self):
        assert _pricing_for_model(None) == DEFAULT_PRICING

    def test_unknown_returns_default(self):
        assert _pricing_for_model("gpt-4") == DEFAULT_PRICING

    def test_unknown_openai_returns_openai_default(self):
        assert _pricing_for_model("unknown", provider="openai") == DEFAULT_OPENAI_PRICING


class TestPricingConstants:
    def test_default_is_sonnet(self):
        assert DEFAULT_PRICING == MODEL_PRICING["sonnet"]

    def test_all_families_present(self):
        assert set(MODEL_PRICING.keys()) == {"opus", "sonnet", "haiku"}

    def test_all_pricing_has_required_keys(self):
        required = {"input", "output", "cache_read", "cache_creation"}
        for family, pricing in MODEL_PRICING.items():
            assert set(pricing.keys()) == required, f"{family} missing keys"
        for family, pricing in OPENAI_MODEL_PRICING.items():
            assert set(pricing.keys()) == required, f"{family} missing keys"
