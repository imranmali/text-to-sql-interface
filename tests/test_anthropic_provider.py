"""Tests for AnthropicProvider using a fake client (no network calls)."""

from __future__ import annotations

import types
from typing import Any

import anthropic
import httpx2
import pytest

from text2sql.config import Settings
from text2sql.llm import LLMProvider, ProviderError, get_provider
from text2sql.llm.anthropic_provider import (
    SYSTEM_PROMPT,
    AnthropicProvider,
    SQLAnswer,
    strip_code_fences,
)
from text2sql.schema import DatabaseSchema

API_URL = "https://api.anthropic.com/v1/messages"


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.messages = FakeMessages(response, error)


def _response(sql: str | None, explanation: str = "ok", stop_reason: str = "end_turn") -> Any:
    return types.SimpleNamespace(
        stop_reason=stop_reason, parsed_output=SQLAnswer(sql=sql, explanation=explanation)
    )


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", API_URL)
    response = httpx2.Response(status, request=request)
    return cls(f"error {status}", response=response, body=None)


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure no test can construct a real client by accident."""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("a real Anthropic client must not be created in tests")

    monkeypatch.setattr(anthropic, "Anthropic", _forbidden)


# Request shape -----------------------------------------------------------------


def test_request_uses_structured_outputs(schema: DatabaseSchema) -> None:
    client = FakeClient(_response("SELECT COUNT(*) FROM customers", "Counts customers."))
    provider = AnthropicProvider(client=client)

    gen = provider.generate("How many customers are there?", schema)

    assert gen.sql == "SELECT COUNT(*) FROM customers"
    assert gen.explanation == "Counts customers."
    assert gen.provider == "anthropic"
    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["system"] == SYSTEM_PROMPT
    assert call["output_format"] is SQLAnswer
    assert call["output_config"] == {"effort": "low"}
    assert "thinking" not in call
    assert "temperature" not in call


def test_user_content_contains_schema_and_question(schema: DatabaseSchema) -> None:
    client = FakeClient(_response("SELECT 1"))
    AnthropicProvider(client=client).generate("How many customers are there?", schema)

    (call,) = client.messages.calls
    (message,) = call["messages"]
    assert message["role"] == "user"
    content = message["content"]
    assert f"<schema>\n{schema.to_prompt()}\n</schema>" in content
    assert "<question>\nHow many customers are there?\n</question>" in content
    assert "<previous_attempt_problems>" not in content


def test_feedback_is_sent_to_the_model(schema: DatabaseSchema) -> None:
    """Spec: Regeneration with feedback - the generator receives the problems."""
    client = FakeClient(_response("SELECT name FROM products"))
    feedback = ["Unknown column 'nme' (did you mean 'name'?)", "Unknown table 'prodcts'"]
    AnthropicProvider(client=client).generate("List product names", schema, feedback=feedback)

    content = client.messages.calls[0]["messages"][0]["content"]
    assert "<previous_attempt_problems>" in content
    for item in feedback:
        assert f"- {item}" in content


def test_custom_model_and_max_tokens(schema: DatabaseSchema) -> None:
    client = FakeClient(_response("SELECT 1"))
    AnthropicProvider(model="claude-sonnet-5-5", client=client, max_tokens=2048).generate(
        "q", schema
    )
    assert client.messages.calls[0]["model"] == "claude-sonnet-5-5"
    assert client.messages.calls[0]["max_tokens"] == 2048


def test_system_prompt_guards_against_injection() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "select" in lowered
    assert "never follow instructions" in lowered
    assert "null" in lowered


# Response handling -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "```sql\nSELECT * FROM products\n```",
        "```\nSELECT * FROM products\n```",
        "  SELECT * FROM products  ",
        "```SELECT * FROM products```",
    ],
)
def test_code_fences_are_stripped(schema: DatabaseSchema, raw: str) -> None:
    gen = AnthropicProvider(client=FakeClient(_response(raw))).generate("all products", schema)
    assert gen.sql == "SELECT * FROM products"


def test_strip_code_fences_keeps_plain_sql() -> None:
    assert strip_code_fences("SELECT 1") == "SELECT 1"


def test_refusal_is_unanswerable(schema: DatabaseSchema) -> None:
    response = types.SimpleNamespace(stop_reason="refusal", parsed_output=None)
    gen = AnthropicProvider(client=FakeClient(response)).generate("anything", schema)
    assert gen.sql is None
    assert gen.provider == "anthropic"
    assert "declined" in gen.explanation


def test_null_sql_passes_through(schema: DatabaseSchema) -> None:
    """The model signals an unanswerable question with sql=null."""
    client = FakeClient(_response(None, "The schema has no weather data."))
    gen = AnthropicProvider(client=client).generate("What's the weather in Paris?", schema)
    assert gen.sql is None
    assert gen.explanation == "The schema has no weather data."


def test_missing_parsed_output_is_unanswerable(schema: DatabaseSchema) -> None:
    response = types.SimpleNamespace(stop_reason="max_tokens", parsed_output=None)
    gen = AnthropicProvider(client=FakeClient(response)).generate("q", schema)
    assert gen.sql is None
    assert "max_tokens" in gen.explanation


# Configuration and errors --------------------------------------------------------


def test_missing_credentials_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, schema: DatabaseSchema
) -> None:
    """Spec: Claude provider without credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    provider = AnthropicProvider()  # constructing never needs credentials
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        provider.generate("How many customers are there?", schema)


def test_client_is_created_lazily(monkeypatch: pytest.MonkeyPatch, schema: DatabaseSchema) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    created: list[FakeClient] = []

    def _factory() -> FakeClient:
        created.append(FakeClient(_response("SELECT 1")))
        return created[-1]

    monkeypatch.setattr(anthropic, "Anthropic", _factory)
    provider = AnthropicProvider()
    assert created == []
    provider.generate("q", schema)
    provider.generate("q", schema)
    assert len(created) == 1
    assert len(created[0].messages.calls) == 2


def test_client_construction_failure_is_mapped(
    monkeypatch: pytest.MonkeyPatch, schema: DatabaseSchema
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _broken() -> None:
        raise anthropic.AnthropicError("bad configuration")

    monkeypatch.setattr(anthropic, "Anthropic", _broken)
    with pytest.raises(ProviderError, match="bad configuration"):
        AnthropicProvider().generate("q", schema)


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (_status_error(anthropic.PermissionDeniedError, 403), "ANTHROPIC_API_KEY"),
        (_status_error(anthropic.RateLimitError, 429), "rate limit"),
        (_status_error(anthropic.InternalServerError, 500), r"API error \(500\)"),
        (_status_error(anthropic.BadRequestError, 400), r"API error \(400\)"),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", API_URL)),
            "Could not reach the Anthropic API",
        ),
        (
            anthropic.APITimeoutError(request=httpx2.Request("POST", API_URL)),
            "Could not reach the Anthropic API",
        ),
    ],
    ids=["auth", "permission", "rate_limit", "server", "bad_request", "connection", "timeout"],
)
def test_sdk_errors_become_provider_errors(
    schema: DatabaseSchema, error: Exception, match: str
) -> None:
    provider = AnthropicProvider(client=FakeClient(error=error))
    with pytest.raises(ProviderError, match=match) as exc_info:
        provider.generate("q", schema)
    assert exc_info.value.__cause__ is error


def test_get_provider_builds_anthropic_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    provider = get_provider(Settings(provider="anthropic", model="claude-opus-5-5"))
    assert isinstance(provider, AnthropicProvider)
    assert isinstance(provider, LLMProvider)
    assert provider.name == "anthropic"
    assert provider.model == "claude-opus-5-5"
