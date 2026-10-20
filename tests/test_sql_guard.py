"""Tests for the AST-based SQL guardrail (spec: sql-guardrails)."""

from __future__ import annotations

import pytest
import sqlglot

from text2sql.guardrails.sql_guard import DANGEROUS_FUNCTIONS, SQLGuardrail, extract_tables

ALLOWED = ("customers", "orders", "products", "order_items")


@pytest.fixture
def guard() -> SQLGuardrail:
    return SQLGuardrail(allowed_tables=ALLOWED, max_rows=100)


def violation_types(guard: SQLGuardrail, sql: str) -> set[str]:
    result = guard.check(sql)
    assert not result.allowed, f"expected {sql!r} to be blocked"
    assert result.rewritten_sql is None
    return {v.type for v in result.violations}


# -- allowed queries -------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM customers",
        "select name, email from Customers where country = 'US'",
        "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
        "WHERE o.id IS NULL",
        "SELECT country, COUNT(*) AS n FROM customers GROUP BY country HAVING COUNT(*) > 1 "
        "ORDER BY n DESC",
        "WITH big AS (SELECT * FROM orders WHERE total > 100) SELECT COUNT(*) FROM big",
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) "
        "SELECT x FROM n",
        "SELECT name FROM customers WHERE id IN (SELECT customer_id FROM orders)",
        "SELECT * FROM (SELECT name, country FROM customers) AS sub",
        "SELECT name FROM customers UNION SELECT name FROM products",
        "SELECT id FROM customers INTERSECT SELECT customer_id FROM orders",
        "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders",
        "SELECT name, ROW_NUMBER() OVER (PARTITION BY country ORDER BY name) AS rn FROM customers",
        "SELECT name, CASE WHEN total > 100 THEN 'big' ELSE 'small' END FROM orders "
        "JOIN customers ON customers.id = orders.customer_id",
        "SELECT strftime('%Y-%m', created_at) AS month, SUM(total) FROM orders GROUP BY month",
        "SELECT date('now', '-30 days'), julianday('now') - julianday(created_at) FROM orders",
        "SELECT COALESCE(email, 'n/a'), IFNULL(country, '?'), LOWER(name) FROM customers",
        "SELECT name FROM customers WHERE name LIKE '%a%'",
        "SELECT 1",
        "SELECT name FROM customers;",
        "SELECT name FROM customers; -- trailing comment",
        "(SELECT name FROM customers)",
    ],
)
def test_allows_read_queries(guard: SQLGuardrail, sql: str) -> None:
    result = guard.check(sql)
    assert result.allowed, result.violations
    assert result.violations == []
    assert result.rewritten_sql is not None
    # The rewritten SQL must itself be a single valid statement.
    assert len(sqlglot.parse(result.rewritten_sql, read="sqlite")) == 1


def test_cte_alias_not_treated_as_table(guard: SQLGuardrail) -> None:
    assert guard.check("WITH t AS (SELECT 1 AS x) SELECT x FROM t").allowed


def test_cte_alias_resolves_across_siblings(guard: SQLGuardrail) -> None:
    sql = "WITH a AS (SELECT * FROM b), b AS (SELECT id FROM customers) SELECT * FROM a"
    assert guard.check(sql).allowed


def test_cte_alias_does_not_leak_into_outer_scope(guard: SQLGuardrail) -> None:
    sql = (
        "SELECT * FROM (WITH secret AS (SELECT 1) SELECT * FROM secret) UNION SELECT * FROM secret"
    )
    assert violation_types(guard, sql) == {"table_not_allowed"}


def test_allowlist_is_case_insensitive() -> None:
    guard = SQLGuardrail(allowed_tables=["Customers"])
    assert guard.check("SELECT * FROM CUSTOMERS").allowed
    assert guard.check("SELECT * FROM customers").allowed


@pytest.mark.parametrize("allowed", [None, [], ()])
def test_empty_allowlist_allows_any_user_table(allowed) -> None:
    guard = SQLGuardrail(allowed_tables=allowed)
    assert guard.check("SELECT * FROM anything_at_all").allowed


def test_empty_allowlist_still_blocks_system_tables() -> None:
    guard = SQLGuardrail()
    assert violation_types(guard, "SELECT sql FROM sqlite_master") == {"table_not_allowed"}


# -- spec scenarios --------------------------------------------------------------


def test_spec_stacked_statements(guard: SQLGuardrail) -> None:
    assert "multiple_statements" in violation_types(guard, "SELECT 1; DELETE FROM customers")


def test_spec_write_statement(guard: SQLGuardrail) -> None:
    assert "forbidden_statement" in violation_types(guard, "DROP TABLE customers")


@pytest.mark.parametrize("sql", ["PRAGMA writable_schema = 1", "ATTACH DATABASE 'x.db' AS x"])
def test_spec_pragma_or_attach(guard: SQLGuardrail, sql: str) -> None:
    assert "forbidden_statement" in violation_types(guard, sql)


def test_spec_garbage_sql(guard: SQLGuardrail) -> None:
    assert violation_types(guard, "SELEC name FRM customers") == {"parse_error"}


def test_spec_extension_loading(guard: SQLGuardrail) -> None:
    assert "dangerous_function" in violation_types(guard, "SELECT load_extension('evil.so')")


def test_spec_system_catalog_access(guard: SQLGuardrail) -> None:
    assert violation_types(guard, "SELECT sql FROM sqlite_master") == {"table_not_allowed"}


def test_spec_missing_limit(guard: SQLGuardrail) -> None:
    result = guard.check("SELECT name FROM customers")
    assert result.allowed
    assert result.rewritten_sql.endswith("LIMIT 100")


def test_spec_excessive_limit(guard: SQLGuardrail) -> None:
    result = guard.check("SELECT name FROM customers LIMIT 5000")
    assert result.allowed
    assert result.rewritten_sql == "SELECT name FROM customers LIMIT 100"


# -- blocked queries -------------------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", ";", "-- just a comment"])
def test_empty_sql(guard: SQLGuardrail, sql: str) -> None:
    assert violation_types(guard, sql) == {"empty_sql"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM",
        "SELECT name FROM customers WHERE",
        "SELECT 'unterminated FROM customers",
    ],
)
def test_parse_errors(guard: SQLGuardrail, sql: str) -> None:
    assert violation_types(guard, sql) == {"parse_error"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT name FROM customers; DROP TABLE customers",
        "SELECT 1; -- \nDROP TABLE customers",
        "SELECT 1 /* harmless */; UPDATE customers SET name = 'x'",
    ],
)
def test_stacked_statements(guard: SQLGuardrail, sql: str) -> None:
    assert "multiple_statements" in violation_types(guard, sql)


def test_comment_smuggled_statement_is_blocked(guard: SQLGuardrail) -> None:
    types = violation_types(guard, "SELECT 1; -- \nDROP TABLE x")
    assert {"multiple_statements", "forbidden_statement"} <= types


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "DELETE FROM customers WHERE id = 1",
        "UPDATE customers SET name = 'x'",
        "INSERT INTO customers (name) VALUES ('x')",
        "INSERT OR REPLACE INTO customers (id, name) VALUES (1, 'x')",
        "REPLACE INTO customers (id, name) VALUES (1, 'x')",
        "CREATE TABLE evil (id INTEGER)",
        "CREATE TEMP VIEW v AS SELECT * FROM customers",
        "CREATE TABLE copy AS SELECT * FROM customers",
        "ALTER TABLE customers ADD COLUMN pwned TEXT",
        "ALTER TABLE customers RENAME TO c2",
        "PRAGMA writable_schema = 1",
        "PRAGMA query_only = OFF",
        "PRAGMA table_info(customers)",
        "ATTACH DATABASE 'x.db' AS x",
        "ATTACH 'x.db' AS x",
        "DETACH DATABASE x",
        "VACUUM",
        "VACUUM INTO '/tmp/copy.db'",
        "BEGIN",
        "BEGIN TRANSACTION",
        "COMMIT",
        "ROLLBACK",
        "ANALYZE",
        "EXPLAIN SELECT * FROM customers",
        "SAVEPOINT sp1",
        "VALUES (1)",
    ],
)
def test_forbidden_statements(guard: SQLGuardrail, sql: str) -> None:
    assert "forbidden_statement" in violation_types(guard, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d",
        "WITH u AS (UPDATE customers SET name = 'x' RETURNING id) SELECT * FROM u",
        "WITH i AS (INSERT INTO customers (name) VALUES ('x') RETURNING id) SELECT * FROM i",
        "SELECT * INTO stolen FROM customers",
    ],
)
def test_nested_writes_are_forbidden(guard: SQLGuardrail, sql: str) -> None:
    assert "forbidden_statement" in violation_types(guard, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sql FROM sqlite_master",
        "SELECT * FROM sqlite_schema",
        "SELECT * FROM sqlite_temp_master",
        "SELECT * FROM sqlite_sequence",
        "SELECT * FROM SQLITE_MASTER",
        "SELECT * FROM main.sqlite_master",
        "SELECT name FROM customers WHERE name IN (SELECT name FROM sqlite_master)",
        "SELECT name FROM customers UNION SELECT sql FROM sqlite_master",
        "WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x",
        "SELECT * FROM dbstat",
        "SELECT * FROM pragma_table_list",
    ],
)
def test_system_tables_blocked(guard: SQLGuardrail, sql: str) -> None:
    assert "table_not_allowed" in violation_types(guard, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM secrets",
        "SELECT c.name FROM customers c JOIN users u ON u.id = c.id",
        "SELECT name FROM customers WHERE id IN (SELECT id FROM admins)",
        "SELECT name FROM customers UNION SELECT password FROM users",
        "WITH t AS (SELECT * FROM users) SELECT * FROM t",
        "SELECT * FROM other.customers_backup",
    ],
)
def test_non_allowlisted_tables_blocked(guard: SQLGuardrail, sql: str) -> None:
    assert violation_types(guard, sql) == {"table_not_allowed"}


@pytest.mark.parametrize("func", sorted(DANGEROUS_FUNCTIONS))
def test_each_dangerous_function_blocked(guard: SQLGuardrail, func: str) -> None:
    sql = f"SELECT {func}('x') FROM customers"
    assert "dangerous_function" in violation_types(guard, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT LOAD_EXTENSION('evil.so')",
        "SELECT name FROM customers WHERE readfile('/etc/passwd') IS NOT NULL",
        "SELECT name FROM customers WHERE id IN (SELECT writefile('/tmp/x', name) FROM customers)",
        "SELECT length(randomblob(1000000000)) FROM customers",
        "WITH t AS (SELECT sleep(10)) SELECT * FROM t",
        "SELECT * FROM pragma_table_info('customers')",
        "SELECT name FROM pragma_database_list()",
    ],
)
def test_dangerous_functions_anywhere(guard: SQLGuardrail, sql: str) -> None:
    assert "dangerous_function" in violation_types(guard, sql)


def test_collects_multiple_violations(guard: SQLGuardrail) -> None:
    types = violation_types(guard, "SELECT load_extension('x') FROM sqlite_master")
    assert types == {"dangerous_function", "table_not_allowed"}


# -- LIMIT enforcement -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT name FROM customers", "SELECT name FROM customers LIMIT 100"),
        ("SELECT name FROM customers LIMIT 5000", "SELECT name FROM customers LIMIT 100"),
        ("SELECT name FROM customers LIMIT 10", "SELECT name FROM customers LIMIT 10"),
        ("SELECT name FROM customers LIMIT 100", "SELECT name FROM customers LIMIT 100"),
        ("SELECT name FROM customers LIMIT 0", "SELECT name FROM customers LIMIT 0"),
        (
            "SELECT name FROM customers LIMIT 10 OFFSET 20",
            "SELECT name FROM customers LIMIT 10 OFFSET 20",
        ),
        (
            "SELECT name FROM customers LIMIT 500 OFFSET 20",
            "SELECT name FROM customers LIMIT 100 OFFSET 20",
        ),
        (
            "SELECT name FROM customers LIMIT 20, 500",
            "SELECT name FROM customers LIMIT 100 OFFSET 20",
        ),
        ("SELECT name FROM customers LIMIT -1", "SELECT name FROM customers LIMIT 100"),
        ("SELECT name FROM customers LIMIT ?", "SELECT name FROM customers LIMIT 100"),
        ("SELECT name FROM customers LIMIT 50 + 100", "SELECT name FROM customers LIMIT 100"),
        (
            "SELECT name FROM customers ORDER BY name DESC",
            "SELECT name FROM customers ORDER BY name DESC LIMIT 100",
        ),
    ],
)
def test_limit_rewrite(guard: SQLGuardrail, sql: str, expected: str) -> None:
    result = guard.check(sql)
    assert result.allowed
    assert result.rewritten_sql == expected


def test_limit_applies_to_outer_union(guard: SQLGuardrail) -> None:
    result = guard.check("SELECT name FROM customers UNION SELECT name FROM products")
    assert (
        result.rewritten_sql
        == "SELECT name FROM customers UNION SELECT name FROM products LIMIT 100"
    )
    parsed = sqlglot.parse_one(result.rewritten_sql, read="sqlite")
    assert parsed.args["limit"] is not None


def test_inner_limits_are_untouched(guard: SQLGuardrail) -> None:
    sql = "SELECT * FROM (SELECT name FROM customers LIMIT 5000) AS s"
    result = guard.check(sql)
    assert (
        result.rewritten_sql
        == "SELECT * FROM (SELECT name FROM customers LIMIT 5000) AS s LIMIT 100"
    )


def test_cte_query_gets_outer_limit(guard: SQLGuardrail) -> None:
    result = guard.check("WITH t AS (SELECT name FROM customers) SELECT * FROM t")
    assert result.rewritten_sql.endswith("SELECT * FROM t LIMIT 100")


def test_custom_max_rows() -> None:
    guard = SQLGuardrail(max_rows=5)
    assert guard.check("SELECT * FROM t LIMIT 6").rewritten_sql == "SELECT * FROM t LIMIT 5"
    assert guard.check("SELECT * FROM t LIMIT 3").rewritten_sql == "SELECT * FROM t LIMIT 3"


def test_invalid_max_rows() -> None:
    with pytest.raises(ValueError):
        SQLGuardrail(max_rows=0)


def test_blocked_query_has_no_rewrite(guard: SQLGuardrail) -> None:
    assert guard.check("DROP TABLE customers").rewritten_sql is None


# -- helpers ---------------------------------------------------------------------


def test_extract_tables_skips_ctes_and_functions() -> None:
    sql = (
        "WITH t AS (SELECT * FROM orders) "
        "SELECT * FROM t JOIN Customers c ON c.id = t.customer_id, json_each('[1]')"
    )
    assert extract_tables(sql) == {"orders", "customers"}
