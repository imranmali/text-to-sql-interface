"""SQL generation providers and the factory that selects one from settings."""

from __future__ import annotations

from text2sql.config import Settings
from text2sql.llm.base import LLMProvider, ProviderError
from text2sql.llm.rule_based import RuleBasedProvider

PROVIDERS = ("rule_based", "anthropic")


def get_provider(settings: Settings) -> LLMProvider:
    """Build the provider named by ``settings.provider``.

    The Anthropic provider is imported lazily and creates its API client on
    first use, so selecting it never requires credentials at import time.
    """
    name = (settings.provider or "").strip().lower()
    if name == "rule_based":
        return RuleBasedProvider()
    if name == "anthropic":
        from text2sql.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=settings.model)
    raise ProviderError(
        f"Unknown provider {settings.provider!r}; expected one of: {', '.join(PROVIDERS)}"
    )


__all__ = ["PROVIDERS", "LLMProvider", "ProviderError", "RuleBasedProvider", "get_provider"]
