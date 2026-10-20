"""The interface every SQL generation provider implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from text2sql.models import SQLGeneration
from text2sql.schema import DatabaseSchema


class ProviderError(RuntimeError):
    """Raised when a provider is misconfigured or its backend fails."""


@runtime_checkable
class LLMProvider(Protocol):
    """Turns a question plus schema into candidate SQL.

    ``feedback`` carries problems found in a previous attempt (guardrail
    violations or hallucination issues) so the provider can correct itself.
    Providers return ``SQLGeneration(sql=None, ...)`` when a question cannot
    be answered from the schema instead of inventing SQL.
    """

    name: str

    def generate(
        self,
        question: str,
        schema: DatabaseSchema,
        feedback: list[str] | None = None,
    ) -> SQLGeneration: ...
