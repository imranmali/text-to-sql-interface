"""Tests for provider selection (OpenSpec sql-generation: configurable provider)."""

from __future__ import annotations

from pathlib import Path

import pytest

from text2sql.config import Settings
from text2sql.llm import LLMProvider, ProviderError, RuleBasedProvider, get_provider


def test_rule_based_provider_selected(db_path: Path) -> None:
    provider = get_provider(Settings(db_path=db_path, provider="rule_based"))
    assert isinstance(provider, RuleBasedProvider)
    assert isinstance(provider, LLMProvider)
    assert provider.name == "rule_based"


def test_provider_name_is_case_insensitive(db_path: Path) -> None:
    provider = get_provider(Settings(db_path=db_path, provider=" Rule_Based "))
    assert isinstance(provider, RuleBasedProvider)


def test_unknown_provider_raises(db_path: Path) -> None:
    with pytest.raises(ProviderError, match="Unknown provider 'gpt'"):
        get_provider(Settings(db_path=db_path, provider="gpt"))
