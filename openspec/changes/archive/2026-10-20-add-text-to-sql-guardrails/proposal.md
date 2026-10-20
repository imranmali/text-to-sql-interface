# Proposal

## Why

Letting people query a relational database in plain English is valuable, but an
LLM that writes SQL can be steered into destructive statements, leak data through
prompt injection, or confidently reference tables and columns that do not exist.
This project needs a Text-to-SQL interface whose every answer passes explicit,
testable safety and grounding checks before any SQL touches the database.

## What Changes

- Add a natural-language-to-SQL generator with pluggable LLM providers: a Claude
  (Anthropic API) provider and an offline, deterministic rule-based provider so the
  system runs and is testable without network access or API keys.
- Add input guardrails that screen user questions for prompt injection, oversize
  input, and requests for destructive operations before generation.
- Add SQL guardrails that only allow a single read-only `SELECT` statement, block
  write/DDL/administrative statements and dangerous functions, enforce a table
  allowlist, and cap result size with an enforced `LIMIT`.
- Add hallucination detection that grounds generated SQL against the real
  database schema (unknown tables, columns, ambiguous references, invalid joins),
  suggests close matches, validates the query plan with the database, and
  produces a confidence score; detected hallucinations trigger one corrective
  regeneration before the request is rejected.
- Add safe query execution over a read-only connection with a timeout and a row cap.
- Expose everything through a REST API, a minimal web UI, and a CLI, backed by a
  bundled sample database.
- Add tests, CI (lint, type-agnostic test matrix, coverage), container build and
  image publishing, README, and an MIT license.

## Capabilities

### New Capabilities

- `sql-generation`: turning a natural-language question plus schema context into
  a candidate SQL query and explanation via a configurable provider.
- `input-guardrails`: screening user questions before they reach the generator.
- `sql-guardrails`: enforcing read-only, single-statement, allowlisted, bounded SQL.
- `hallucination-detection`: grounding generated SQL against the actual schema and
  scoring confidence.
- `query-execution`: running approved SQL safely and returning bounded results.
- `query-interface`: the HTTP API, web UI and CLI that expose the pipeline.

### Modified Capabilities

None (greenfield project).

## Impact

- New Python package `text2sql` (Python 3.11+), with FastAPI, sqlglot, pydantic
  and the Anthropic SDK as runtime dependencies.
- New GitHub Actions workflows for CI and for publishing a container image to
  GitHub Container Registry.
- No existing code, APIs or data are affected.
