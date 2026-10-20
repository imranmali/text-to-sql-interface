"""The guarded Text-to-SQL pipeline that ties every stage together."""

from __future__ import annotations

import logging

from text2sql.config import Settings, get_settings
from text2sql.executor import ExecutionError, SafeExecutor
from text2sql.guardrails.input_guard import InputGuardrail
from text2sql.guardrails.sql_guard import SQLGuardrail
from text2sql.hallucination.detector import HallucinationDetector
from text2sql.llm import LLMProvider, ProviderError, get_provider
from text2sql.models import (
    GuardrailResult,
    HallucinationReport,
    PipelineResponse,
    PipelineStatus,
    SQLGeneration,
)
from text2sql.schema import DatabaseSchema

logger = logging.getLogger(__name__)


class Text2SQLPipeline:
    """Question in, guarded and grounded answer out.

    Order: input guardrail -> generate -> SQL guardrail -> hallucination
    check (with up to ``max_regenerations`` corrective retries) -> execute ->
    result sanity checks. Nothing reaches the database until both the SQL
    guardrail and the grounding check have passed.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        schema: DatabaseSchema | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or get_provider(self.settings)
        full_schema = schema or DatabaseSchema.from_sqlite(self.settings.db_path)
        self.schema = full_schema.restrict(self.settings.allowed_tables)
        self.input_guard = InputGuardrail(max_length=self.settings.max_question_length)
        # Only an explicit allowlist is enforced here. Without one, a table that
        # does not exist is a grounding problem for the hallucination detector
        # (which can trigger a corrective retry), not a policy violation.
        self.sql_guard = SQLGuardrail(
            allowed_tables=self.settings.allowed_tables or None, max_rows=self.settings.max_rows
        )
        self.detector = HallucinationDetector(
            self.schema, db_path=self.settings.db_path, threshold=self.settings.confidence_threshold
        )
        self.executor = SafeExecutor(
            self.settings.db_path,
            timeout_s=self.settings.query_timeout_s,
            max_rows=self.settings.max_rows,
        )

    def run(self, question: str, execute: bool = True) -> PipelineResponse:
        response = PipelineResponse(
            question=question, status=PipelineStatus.ERROR, provider=self.provider.name
        )

        screened = self.input_guard.check(question)
        if not screened.allowed:
            response.status = PipelineStatus.BLOCKED
            response.violations = screened.violations
            return response

        feedback: list[str] | None = None
        for attempt in range(1, self.settings.max_regenerations + 2):
            response.attempts = attempt
            try:
                generation = self.provider.generate(question, self.schema, feedback=feedback)
            except ProviderError as exc:
                logger.warning("provider failed: %s", exc)
                response.error = str(exc)
                return response

            outcome = self._check(generation, response)
            if outcome is None:
                return response
            guard, report = outcome
            if not report.is_hallucinated:
                break
            feedback = report.feedback()
        else:
            response.status = PipelineStatus.HALLUCINATION
            return response

        response.executed_sql = guard.rewritten_sql or generation.sql
        if not execute:
            response.status = PipelineStatus.SUCCESS
            return response

        try:
            result = self.executor.execute(response.executed_sql)
        except ExecutionError as exc:
            response.error = f"{exc.kind}: {exc}"
            return response

        response.result = result
        response.warnings = self.detector.check_result(question, result)
        response.status = PipelineStatus.SUCCESS
        return response

    def _check(
        self, generation: SQLGeneration, response: PipelineResponse
    ) -> tuple[GuardrailResult, HallucinationReport] | None:
        """Run SQL guardrails and grounding; return None when the run must stop."""
        response.sql = generation.sql
        response.explanation = generation.explanation
        if not generation.sql:
            response.status = PipelineStatus.UNANSWERABLE
            return None

        guard = self.sql_guard.check(generation.sql)
        if not guard.allowed:
            # Generated SQL that violates policy is never retried: it is either
            # an attack that slipped past input screening or a provider bug.
            response.status = PipelineStatus.BLOCKED
            response.violations = guard.violations
            return None

        report = self.detector.analyze(generation.sql)
        response.hallucination = report
        response.confidence = report.confidence
        return guard, report
