# Tasks

## 1. Foundation

- [x] 1.1 Create `pyproject.toml`, `src/text2sql` package layout and dev tooling (ruff, pytest) and verify `pip install -e ".[dev]"` succeeds
- [x] 1.2 Implement shared models (`models.py`) and settings (`config.py`) and verify they import and serialize to JSON
- [x] 1.3 Implement schema introspection (`schema.py`) and the sample database (`sample_db.py`) and verify with `tests/test_schema.py`
- [x] 1.4 Define the `LLMProvider` protocol (`llm/base.py`) and verify it imports

## 2. Input guardrails

- [x] 2.1 Implement empty/length checks and verify `tests/test_input_guard.py` covers the empty and oversize scenarios
- [x] 2.2 Implement prompt-injection, destructive-intent and embedded-SQL detection and verify the attack and false-positive scenarios in `tests/test_input_guard.py`

## 3. SQL guardrails

- [x] 3.1 Implement parse, single-statement and read-only checks (including nested DML, PRAGMA, ATTACH) and verify `tests/test_sql_guard.py`
- [x] 3.2 Implement dangerous-function and table-allowlist checks (system tables always blocked) and verify `tests/test_sql_guard.py`
- [x] 3.3 Implement LIMIT enforcement and rewriting and verify missing/excessive limit scenarios in `tests/test_sql_guard.py`

## 4. Hallucination detection

- [x] 4.1 Implement unknown table/column detection with alias and CTE resolution and suggestions, verified by `tests/test_hallucination.py`
- [x] 4.2 Implement ambiguous-column and suspicious-join detection, verified by `tests/test_hallucination.py`
- [x] 4.3 Implement `EXPLAIN QUERY PLAN` validation, confidence scoring and result sanity checks, verified by `tests/test_hallucination.py`

## 5. Generation and execution

- [x] 5.1 Implement the deterministic `RuleBasedProvider` and verify `tests/test_rule_based.py` covers counts, aggregates, top-N, listing and unanswerable questions
- [x] 5.2 Implement `AnthropicProvider` with structured outputs and lazy client creation and verify `tests/test_anthropic_provider.py` with a mocked client
- [x] 5.3 Implement `SafeExecutor` (read-only URI, `query_only`, timeout, row cap) and verify `tests/test_executor.py`

## 6. Pipeline and interfaces

- [x] 6.1 Implement `Text2SQLPipeline` with one corrective regeneration and verify `tests/test_pipeline.py`
- [x] 6.2 Implement the FastAPI app (`/api/query`, `/api/schema`, `/health`, `/`) and verify `tests/test_api.py`
- [x] 6.3 Implement the static web UI and verify `GET /` returns the page in `tests/test_api.py`
- [x] 6.4 Implement the `text2sql` CLI (`init-db`, `ask`, `serve`) and verify `tests/test_cli.py`

## 7. Delivery

- [x] 7.1 Add `ci.yml` (lint, tests with coverage on 3.11-3.13, OpenSpec validate, Docker build) and verify it passes on the pull request
- [x] 7.2 Add `Dockerfile` and `release.yml` publishing to GHCR and verify the image builds locally or in CI
- [x] 7.3 Add `README.md`, `LICENSE` (MIT) and `CONTRIBUTING.md` and verify README commands run as written
- [x] 7.4 Run the full suite (`ruff check`, `ruff format --check`, `pytest`) and `openspec validate --strict` and verify all pass

## Workflow follow-up

- Archive the change with `openspec archive add-text-to-sql-guardrails` once implementation is verified, and verify `openspec/specs/` contains the six capabilities.
- Open a pull request to `main`, wait for green CI, then merge.
