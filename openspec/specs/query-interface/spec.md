# query-interface Specification

## Purpose
Exposes the guarded Text-to-SQL pipeline to people and programs through an HTTP API, a browser UI and a command-line tool.

## Requirements

### Requirement: Query endpoint
The system SHALL expose `POST /api/query` accepting `{"question": string, "execute": bool}` and returning the status, generated SQL, executed SQL, explanation, guardrail violations, hallucination report, confidence, and results.

#### Scenario: Successful query
- **WHEN** a client posts `{"question": "How many customers are there?"}`
- **THEN** the response is HTTP 200 with `status` `success`, the SQL, and the result rows

#### Scenario: Blocked query
- **WHEN** a client posts a prompt-injection question
- **THEN** the response is HTTP 200 with `status` `blocked` and the violations list, and no SQL is executed

#### Scenario: Invalid body
- **WHEN** a client posts a body without `question`
- **THEN** the response is HTTP 422

### Requirement: Schema and health endpoints
The system SHALL expose `GET /api/schema` describing allowed tables and columns and `GET /health` reporting service status and provider name.

#### Scenario: Health check
- **WHEN** a client calls `GET /health`
- **THEN** the response is HTTP 200 with `status` `ok`

### Requirement: Web UI
The system SHALL serve a browser page at `/` that lets a user ask a question and see SQL, guardrail findings, confidence and results.

#### Scenario: UI served
- **WHEN** a browser requests `/`
- **THEN** an HTML page containing a question input is returned

### Requirement: Command-line interface
The system SHALL provide a CLI that can initialise the sample database, ask a question and print the outcome, and start the API server.

#### Scenario: Ask from the CLI
- **WHEN** a user runs `text2sql ask "How many products are there?"`
- **THEN** the CLI prints the SQL and results and exits with status 0

#### Scenario: Blocked question from the CLI
- **WHEN** a user runs `text2sql ask "drop table products"`
- **THEN** the CLI prints the violations and exits with a non-zero status
