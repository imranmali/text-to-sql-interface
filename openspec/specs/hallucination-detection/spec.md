# hallucination-detection Specification

## Purpose
Detects generated SQL that is not grounded in the real database, such as invented tables or columns, broken joins or implausible results, and quantifies confidence so callers can trust or reject an answer.

## Requirements

### Requirement: Unknown table detection
The system SHALL flag every table referenced by the SQL that does not exist in the schema and SHALL suggest the closest existing table name when one is similar.

#### Scenario: Misspelled table
- **WHEN** the SQL is `SELECT * FROM customer`
- **THEN** an `unknown_table` issue is reported for `customer` with suggestion `customers`

### Requirement: Unknown column detection
The system SHALL flag every column reference that does not exist in the referenced tables, resolving table aliases and CTE/derived-table output columns, and SHALL suggest the closest existing column.

#### Scenario: Invented column
- **WHEN** the SQL is `SELECT c.full_name FROM customers c` and `customers` has no `full_name` column
- **THEN** an `unknown_column` issue is reported with a suggestion such as `first_name` or `last_name`

#### Scenario: Alias and CTE columns are valid
- **WHEN** the SQL is `WITH t AS (SELECT id AS cid FROM customers) SELECT cid FROM t`
- **THEN** no unknown column issue is reported

### Requirement: Ambiguous column detection
The system SHALL flag unqualified column references that exist in more than one joined table.

#### Scenario: Ambiguous id
- **WHEN** the SQL is `SELECT id FROM customers JOIN orders ON customers.id = orders.customer_id`
- **THEN** an `ambiguous_column` issue is reported for `id`

### Requirement: Join plausibility
The system SHALL warn when a join condition compares columns not related by a declared foreign key and whose names do not suggest a relationship.

#### Scenario: Suspicious join
- **WHEN** the SQL joins `customers` and `products` on `customers.id = products.id`
- **THEN** a `suspicious_join` warning is reported

### Requirement: Database plan validation
The system SHALL ask the database to plan the query without executing it and SHALL report any planning error as an issue.

#### Scenario: Plan failure
- **WHEN** the query references a function the database does not provide
- **THEN** a `plan_error` issue is reported

### Requirement: Confidence scoring
The system SHALL produce a confidence score between 0 and 1 for each query, reduced by each detected issue according to its severity, and SHALL mark the query as hallucinated when any error-severity issue exists or the score is below the configured threshold.

#### Scenario: Clean query
- **WHEN** a query references only existing tables and columns and plans successfully
- **THEN** its confidence is at least the configured threshold and it is not marked hallucinated

#### Scenario: Grounding error
- **WHEN** a query references an unknown column
- **THEN** it is marked hallucinated

### Requirement: Result sanity checks
After execution, the system SHALL attach warnings when results are empty or when the question asks for a single aggregate but the result has many rows.

#### Scenario: Empty result
- **WHEN** an executed query returns zero rows
- **THEN** the response includes an `empty_result` warning
