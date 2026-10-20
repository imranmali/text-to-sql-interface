# sql-generation Specification

## Purpose
Turns a natural-language question plus the live database schema into a candidate SQL query and a short explanation, using a configurable generation provider.

## Requirements

### Requirement: Schema-grounded generation
The system SHALL provide the generator with the database schema (tables, columns, types, keys) and the user question, and SHALL return a candidate SQL string and a natural-language explanation.

#### Scenario: Question answered with SQL
- **WHEN** a user asks "How many customers are there?" against the sample database
- **THEN** the generator returns a SQL query that reads from the `customers` table and an explanation of what the query does

### Requirement: Configurable provider
The system SHALL support selecting the generation provider via configuration, including a Claude provider and an offline deterministic provider that needs no network access or API key.

#### Scenario: Offline provider selected
- **WHEN** the provider is configured as `rule_based`
- **THEN** questions are answered without any network call, and the same question always yields the same SQL

#### Scenario: Claude provider without credentials
- **WHEN** the provider is configured as `anthropic` and no API credential is available
- **THEN** the system reports a clear configuration error instead of failing at query time with an unhandled exception

### Requirement: Corrective regeneration
The system SHALL accept feedback describing problems found in a previous attempt and SHALL use it when generating a replacement query.

#### Scenario: Regeneration with feedback
- **WHEN** the first candidate references a column that does not exist and the pipeline regenerates with that feedback
- **THEN** the generator receives the list of problems from the first attempt

### Requirement: Unanswerable questions
The generator SHALL signal when it cannot map a question to the schema rather than inventing SQL.

#### Scenario: Question outside the schema
- **WHEN** the offline provider receives a question that matches no table in the schema
- **THEN** it returns no SQL and an explanation that the question could not be answered from the schema
