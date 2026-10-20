# Spec Delta

## Purpose

Runs SQL that has passed all guardrails against the database in a way that cannot modify data and cannot run unbounded.

## ADDED Requirements

### Requirement: Read-only connection
The system SHALL execute queries over a connection that the database itself enforces as read-only.

#### Scenario: Write attempt bypassing guardrails
- **WHEN** a write statement is executed directly through the executor
- **THEN** the database rejects it and no data changes

### Requirement: Execution timeout
The system SHALL abort queries that exceed the configured timeout and report a timeout error.

#### Scenario: Long-running query
- **WHEN** a query runs longer than the configured timeout
- **THEN** execution is aborted and an error of type `timeout` is returned

### Requirement: Bounded structured results
The system SHALL return column names, rows and row count, never more than the configured maximum rows, and SHALL report the execution time.

#### Scenario: Successful query
- **WHEN** `SELECT name FROM products LIMIT 3` is executed
- **THEN** the result has columns `["name"]`, three rows, row count 3 and a non-negative duration
