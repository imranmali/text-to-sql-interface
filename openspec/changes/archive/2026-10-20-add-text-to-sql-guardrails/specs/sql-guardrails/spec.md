# Spec Delta

## Purpose

Enforces that any generated SQL is a single, read-only, bounded query over allowed tables before it can reach the database.

## ADDED Requirements

### Requirement: Single statement only
The system SHALL reject SQL that contains more than one statement.

#### Scenario: Stacked statements
- **WHEN** the generated SQL is `SELECT 1; DELETE FROM customers`
- **THEN** it is blocked with a violation of type `multiple_statements`

### Requirement: Read-only statements only
The system SHALL allow only `SELECT` queries (including `WITH ... SELECT` and set operations) and SHALL reject any write, DDL, transaction or administrative statement, including writes nested inside CTEs.

#### Scenario: Write statement
- **WHEN** the generated SQL is `DROP TABLE customers`
- **THEN** it is blocked with a violation of type `forbidden_statement`

#### Scenario: Pragma or attach
- **WHEN** the generated SQL is `PRAGMA writable_schema = 1` or `ATTACH DATABASE 'x.db' AS x`
- **THEN** it is blocked

### Requirement: Unparseable SQL rejected
The system SHALL reject SQL that cannot be parsed.

#### Scenario: Garbage SQL
- **WHEN** the generated SQL is `SELEC name FRM customers`
- **THEN** it is blocked with a violation of type `parse_error`

### Requirement: Dangerous functions blocked
The system SHALL reject queries that call functions able to touch the filesystem, load extensions or degrade service (for example `load_extension`, `readfile`, `writefile`, `randomblob` with large sizes, `sleep`).

#### Scenario: Extension loading
- **WHEN** the generated SQL is `SELECT load_extension('evil.so')`
- **THEN** it is blocked with a violation of type `dangerous_function`

### Requirement: Table allowlist
The system SHALL reject queries referencing tables outside the configured allowlist, and SHALL always block internal system tables such as `sqlite_master`.

#### Scenario: System catalog access
- **WHEN** the generated SQL is `SELECT sql FROM sqlite_master`
- **THEN** it is blocked with a violation of type `table_not_allowed`

### Requirement: Bounded result size
The system SHALL ensure every approved query returns at most the configured maximum rows by adding a `LIMIT` when absent and lowering any larger `LIMIT`.

#### Scenario: Missing limit
- **WHEN** an approved query has no `LIMIT` and the maximum is 100
- **THEN** the SQL that is executed ends with `LIMIT 100`

#### Scenario: Excessive limit
- **WHEN** an approved query has `LIMIT 5000` and the maximum is 100
- **THEN** the executed SQL uses `LIMIT 100`
