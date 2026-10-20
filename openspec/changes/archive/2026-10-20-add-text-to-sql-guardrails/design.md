# Design

## Context

Greenfield repository. See `proposal.md` for motivation and the capability specs
under `specs/` for required behavior. The system must run fully offline (CI has
no API key), so every capability has to be exercisable without calling an LLM.

## Goals / Non-Goals

**Goals:**
- A layered, defence-in-depth pipeline where each layer is independently testable.
- Deterministic behavior in tests and CI via an offline provider.
- Stable module contracts so guardrails, hallucination detection, providers and
  the interface can be built in parallel.

**Non-Goals:**
- Multi-database dialect support beyond SQLite (the parser is dialect-aware, so
  this can be added later).
- Authentication, multi-tenancy, conversation memory.
- Fine-tuning or embedding-based schema retrieval.

## Decisions

### Pipeline order

```
question
  -> InputGuardrail.check          (block early, no LLM cost)
  -> provider.generate             (schema + question [+ feedback])
  -> SQLGuardrail.check            (parse, read-only, allowlist, LIMIT rewrite)
  -> HallucinationDetector.analyze (schema grounding, joins, EXPLAIN, score)
       hallucinated? -> provider.generate(feedback=issues) once, re-check
  -> SafeExecutor.execute          (read-only connection, timeout, row cap)
  -> HallucinationDetector.check_result (empty / shape warnings)
  -> PipelineResponse
```

Guardrails run before hallucination detection so that malicious SQL is never
sent to the database, even for `EXPLAIN`.

### Parsing with sqlglot (vs. regex)
SQL guardrails and grounding use `sqlglot` ASTs (`read="sqlite"`). Regex-based
SQL filters are trivially bypassed (comments, casing, nesting); an AST lets us
walk every node, including CTEs and subqueries, and rewrite `LIMIT` safely.
Regex is used only on natural-language input, where there is no grammar.

### Defence in depth for writes
Writes are blocked three times: input guardrail (intent), SQL guardrail (AST
statement type, nested DML), and the executor opens SQLite with
`file:...?mode=ro` URI plus `PRAGMA query_only = ON`. Any one layer failing
still leaves the database untouched.

### Timeouts
SQLite has no statement timeout, so the executor installs a
`set_progress_handler` callback that aborts once the deadline passes. This
works for in-process SQLite without threads.

### Providers
`LLMProvider` is a small protocol: `name` and
`generate(question, schema, feedback=None) -> SQLGeneration`.
- `AnthropicProvider` uses `client.messages.parse(...)` with a Pydantic output
  model (structured outputs) so we never regex SQL out of prose. Model defaults
  to `claude-opus-5-5`, configurable via `TEXT2SQL_MODEL`; effort is set to
  `low` because this is a bounded, single-call task. `stop_reason == "refusal"`
  is mapped to an unanswerable generation. The client is created lazily so the
  package imports without credentials.
- `RuleBasedProvider` maps keywords to schema tables/columns (count, average,
  sum, top-N, list, group-by "per"/"by") and is deterministic. It is the default
  so the project works out of the box and in CI.

### Hallucination scoring
Score starts at 1.0; each issue subtracts by severity (`error` 0.4,
`warning` 0.15, `info` 0.05), clamped to [0, 1]. A query is *hallucinated*
when any `error` issue exists or the score is below `confidence_threshold`
(default 0.5). Suggestions use `difflib.get_close_matches` (cutoff 0.6) - no
extra dependency. Plan validation runs `EXPLAIN QUERY PLAN` on the read-only
connection.

### Module contracts

Package `src/text2sql/`:

| Module | Public API |
|---|---|
| `config.py` | `Settings` (pydantic-settings style dataclass from env `TEXT2SQL_*`), `get_settings()` |
| `models.py` | `Severity`, `Violation`, `GuardrailResult`, `HallucinationIssue`, `HallucinationReport`, `SQLGeneration`, `QueryResult`, `PipelineStatus`, `PipelineResponse` |
| `schema.py` | `Column`, `ForeignKey`, `Table`, `DatabaseSchema` (`from_sqlite(path)`, `table(name)`, `to_prompt()`, `to_dict()`) |
| `sample_db.py` | `create_sample_database(path, overwrite=False)` |
| `llm/base.py` | `LLMProvider` protocol, `ProviderError` |
| `llm/rule_based.py` | `RuleBasedProvider` |
| `llm/anthropic_provider.py` | `AnthropicProvider` |
| `llm/__init__.py` | `get_provider(settings)` |
| `guardrails/input_guard.py` | `InputGuardrail(max_length).check(question) -> GuardrailResult` |
| `guardrails/sql_guard.py` | `SQLGuardrail(allowed_tables, max_rows).check(sql) -> GuardrailResult` (with `rewritten_sql`) |
| `hallucination/detector.py` | `HallucinationDetector(schema, db_path, threshold).analyze(sql) -> HallucinationReport`, `.check_result(question, result) -> list[HallucinationIssue]` |
| `executor.py` | `SafeExecutor(db_path, timeout_s, max_rows).execute(sql) -> QueryResult`, `ExecutionError` |
| `pipeline.py` | `Text2SQLPipeline(settings, provider=None).run(question, execute=True) -> PipelineResponse` |
| `api.py` | `create_app(settings=None) -> FastAPI`, module-level `app` |
| `cli.py` | `main(argv=None) -> int` (console script `text2sql`) |

### Interface
FastAPI for the HTTP API (typed request/response models, OpenAPI for free);
the web UI is a single static HTML file with vanilla JS - no build step. CLI
uses `argparse` to avoid another dependency.

### CI/CD
- `ci.yml`: on push/PR - ruff lint + format check, pytest with coverage on
  Python 3.11/3.12/3.13, OpenSpec validation, Docker build smoke test.
- `release.yml`: on push to `main` and `v*` tags - build and push the image to
  GHCR (`ghcr.io/<owner>/text-to-sql-interface`) using `GITHUB_TOKEN`.

## Risks / Trade-offs

- [Regex input guardrails have false positives/negatives] -> they are a first
  filter only; the AST SQL guardrail and read-only connection are authoritative.
- [Rule-based provider answers a narrow set of questions] -> it is a demo and
  test double; production deployments set `TEXT2SQL_PROVIDER=anthropic`.
- [Name-similarity join heuristic can warn on valid joins] -> it is a warning,
  not an error, and declared foreign keys always pass.
- [EXPLAIN adds a DB round-trip] -> it is cheap on SQLite and skipped when the
  SQL guardrail already blocked the query.

## Migration Plan

Greenfield: merge to `main`, the release workflow publishes the image. Rollback
is redeploying the previous image tag.
