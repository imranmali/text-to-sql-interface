"""Tests for the deterministic RuleBasedProvider (OpenSpec sql-generation)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from text2sql.llm.base import LLMProvider
from text2sql.llm.rule_based import UNANSWERABLE, RuleBasedProvider
from text2sql.schema import Column, DatabaseSchema, Table


@pytest.fixture(scope="module")
def provider() -> RuleBasedProvider:
    return RuleBasedProvider()


def _run(db_path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _norm(sql: str) -> str:
    return " ".join(sql.split())


# Each case: question, expected SQL (exact, deterministic), check on the rows.
CASES: list[tuple[str, str, Any]] = [
    (
        "How many customers are there?",
        "SELECT COUNT(*) AS count FROM customers",
        lambda rows: rows == [(50,)],
    ),
    (
        "How many orders per status?",
        "SELECT status, COUNT(*) AS count FROM orders GROUP BY status ORDER BY count DESC",
        lambda rows: (
            sum(r[1] for r in rows) == 200
            and {r[0] for r in rows} <= {"delivered", "shipped", "pending", "cancelled"}
        ),
    ),
    (
        "Average order total amount",
        "SELECT ROUND(AVG(total_amount), 2) AS avg_total_amount FROM orders",
        lambda rows: len(rows) == 1 and rows[0][0] > 0,
    ),
    (
        "Total stock of products",
        "SELECT SUM(stock) AS total_stock FROM products",
        lambda rows: len(rows) == 1 and rows[0][0] >= 0,
    ),
    (
        "Top 5 most expensive products",
        "SELECT * FROM products ORDER BY price DESC LIMIT 5",
        lambda rows: len(rows) == 5 and rows[0][1] == "Laptop Pro 14",
    ),
    (
        "List products in the Books category",
        "SELECT * FROM products WHERE category = 'Books'",
        lambda rows: len(rows) == 3 and all(r[2] == "Books" for r in rows),
    ),
    (
        "List customers from Germany",
        "SELECT * FROM customers WHERE country = 'Germany'",
        lambda rows: rows and all(r[5] == "Germany" for r in rows),
    ),
    (
        "How many orders in 2024?",
        "SELECT COUNT(*) AS count FROM orders WHERE strftime('%Y', order_date) = '2024'",
        lambda rows: 0 < rows[0][0] < 200,
    ),
    (
        "Show all products",
        "SELECT * FROM products",
        lambda rows: len(rows) == 20,
    ),
    (
        "Cheapest product price",
        "SELECT MIN(price) AS min_price FROM products",
        lambda rows: rows == [(3.99,)],
    ),
    (
        "count customers by country",
        "SELECT country, COUNT(*) AS count FROM customers GROUP BY country ORDER BY count DESC",
        lambda rows: sum(r[1] for r in rows) == 50,
    ),
    (
        "How many orders per customer?",
        "SELECT customer_id, COUNT(*) AS count FROM orders GROUP BY customer_id "
        "ORDER BY count DESC",
        lambda rows: sum(r[1] for r in rows) == 200,
    ),
    (
        "How many order items are there?",
        "SELECT COUNT(*) AS count FROM order_items",
        lambda rows: rows[0][0] > 200,
    ),
    (
        "Average price per category",
        "SELECT category, ROUND(AVG(price), 2) AS avg_price FROM products GROUP BY category "
        "ORDER BY avg_price DESC",
        lambda rows: len(rows) == 5,
    ),
    (
        "What is the most expensive product?",
        "SELECT * FROM products ORDER BY price DESC LIMIT 1",
        lambda rows: len(rows) == 1 and rows[0][3] == 1299.0,
    ),
    (
        "3 cheapest products",
        "SELECT * FROM products ORDER BY price ASC LIMIT 3",
        lambda rows: [r[3] for r in rows] == sorted(r[3] for r in rows),
    ),
    (
        "List delivered orders",
        "SELECT * FROM orders WHERE status = 'delivered'",
        lambda rows: rows and all(r[3] == "delivered" for r in rows),
    ),
    (
        "Show orders with status 'shipped'",
        "SELECT * FROM orders WHERE status = 'shipped'",
        lambda rows: rows and all(r[3] == "shipped" for r in rows),
    ),
    (
        "Which customers are from Paris?",
        "SELECT * FROM customers WHERE (city = 'Paris' OR country = 'Paris')",
        lambda rows: rows and all(r[4] == "Paris" for r in rows),
    ),
    (
        "List products sorted by price descending",
        "SELECT * FROM products ORDER BY price DESC",
        lambda rows: len(rows) == 20 and rows[0][3] == 1299.0,
    ),
    (
        "Total quantity of order items per product",
        "SELECT product_id, SUM(quantity) AS total_quantity FROM order_items "
        "GROUP BY product_id ORDER BY total_quantity DESC",
        lambda rows: len(rows) > 1,
    ),
    (
        "How many customers signed up in 2024?",
        "SELECT COUNT(*) AS count FROM customers WHERE strftime('%Y', signup_date) = '2024'",
        lambda rows: rows[0][0] > 0,
    ),
    (
        "What is the average price?",
        "SELECT ROUND(AVG(price), 2) AS avg_price FROM products",
        lambda rows: rows[0][0] > 0,
    ),
]


@pytest.mark.parametrize(("question", "expected", "check"), CASES, ids=[c[0] for c in CASES])
def test_generated_sql_executes_on_sample_db(
    provider: RuleBasedProvider,
    schema: DatabaseSchema,
    db_path: Path,
    question: str,
    expected: str,
    check: Any,
) -> None:
    gen = provider.generate(question, schema)
    assert gen.provider == "rule_based"
    assert gen.sql is not None
    assert _norm(gen.sql) == _norm(expected)
    assert gen.explanation.endswith(".")
    assert check(_run(db_path, gen.sql))


def test_question_answered_with_sql(provider: RuleBasedProvider, schema: DatabaseSchema) -> None:
    """Spec: Question answered with SQL."""
    gen = provider.generate("How many customers are there?", schema)
    assert gen.sql is not None
    assert "FROM customers" in gen.sql
    assert "customers" in gen.explanation


@pytest.mark.parametrize(
    "question",
    ["What's the weather in Paris?", "Tell me a joke", "", "Who won the 2022 world cup?"],
)
def test_unanswerable_question_returns_no_sql(
    provider: RuleBasedProvider, schema: DatabaseSchema, question: str
) -> None:
    """Spec: Question outside the schema."""
    gen = provider.generate(question, schema)
    assert gen.sql is None
    assert gen.explanation == UNANSWERABLE
    assert gen.provider == "rule_based"


def test_same_question_always_yields_same_sql(schema: DatabaseSchema) -> None:
    """Spec: Offline provider selected - deterministic output."""
    questions = [c[0] for c in CASES]
    first = [RuleBasedProvider().generate(q, schema).sql for q in questions]
    second = [RuleBasedProvider().generate(q, schema).sql for q in questions]
    assert first == second


def test_offline_without_network(
    monkeypatch: pytest.MonkeyPatch, provider: RuleBasedProvider, schema: DatabaseSchema
) -> None:
    """Spec: Offline provider selected - no network call is made."""
    import socket

    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider.generate("How many products are there?", schema).sql is not None


def test_feedback_is_acknowledged(provider: RuleBasedProvider, schema: DatabaseSchema) -> None:
    """Spec: Regeneration with feedback - the generator receives the problems."""
    plain = provider.generate("How many customers are there?", schema)
    gen = provider.generate(
        "How many customers are there?",
        schema,
        feedback=["Column 'nme' does not exist (did you mean 'name'?)"],
    )
    assert gen.sql == plain.sql
    assert "1 reported problem(s)" in gen.explanation


def test_implements_provider_protocol(provider: RuleBasedProvider) -> None:
    assert isinstance(provider, LLMProvider)
    assert provider.name == "rule_based"


def test_works_from_an_arbitrary_schema(provider: RuleBasedProvider) -> None:
    """The provider is driven by the schema it is given, not the sample DB."""
    schema = DatabaseSchema(
        tables={
            "employees": Table(
                name="employees",
                columns=[
                    Column("id", "INTEGER", primary_key=True),
                    Column("full_name", "TEXT"),
                    Column("department", "TEXT"),
                    Column("salary", "REAL"),
                    Column("hire_date", "TEXT"),
                ],
                sample_values={"department": ["Sales", "Engineering"]},
            )
        }
    )
    assert (
        provider.generate("Average salary per department", schema).sql
        == "SELECT department, ROUND(AVG(salary), 2) AS avg_salary FROM employees "
        "GROUP BY department ORDER BY avg_salary DESC"
    )
    assert (
        provider.generate("How many employees in Engineering?", schema).sql
        == "SELECT COUNT(*) AS count FROM employees WHERE department = 'Engineering'"
    )
    assert (
        provider.generate("How many employees were hired in 2023?", schema).sql
        == "SELECT COUNT(*) AS count FROM employees WHERE strftime('%Y', hire_date) = '2023'"
    )
    assert provider.generate("How many customers are there?", schema).sql is None


def test_single_quotes_in_literals_are_escaped(provider: RuleBasedProvider) -> None:
    schema = DatabaseSchema(
        tables={
            "shops": Table(
                name="shops",
                columns=[Column("id", "INTEGER", primary_key=True), Column("city", "TEXT")],
            )
        }
    )
    gen = provider.generate("List shops in the O'Fallon city", schema)
    assert gen.sql == "SELECT * FROM shops WHERE city = 'O''Fallon'"
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE shops (id INTEGER PRIMARY KEY, city TEXT)")
        conn.execute("INSERT INTO shops (city) VALUES ('O''Fallon')")
        assert conn.execute(gen.sql).fetchall() == [(1, "O'Fallon")]
    finally:
        conn.close()


def test_falls_back_to_distinctive_column_when_no_table_named(
    provider: RuleBasedProvider, schema: DatabaseSchema
) -> None:
    gen = provider.generate("What is the maximum stock?", schema)
    assert gen.sql == "SELECT MAX(stock) AS max_stock FROM products"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "List products in the 'Books' category",
            "SELECT * FROM products WHERE category = 'Books'",
        ),
        (
            "customers whose country is 'United States'",
            "SELECT * FROM customers WHERE country = 'United States'",
        ),
        ("orders with status of cancelled", "SELECT * FROM orders WHERE status = 'cancelled'"),
        (
            "Average total amount of orders in 2025 by status",
            "SELECT status, ROUND(AVG(total_amount), 2) AS avg_total_amount FROM orders "
            "WHERE strftime('%Y', order_date) = '2025' GROUP BY status "
            "ORDER BY avg_total_amount DESC",
        ),
    ],
)
def test_filter_values_are_extracted_conservatively(
    provider: RuleBasedProvider, schema: DatabaseSchema, question: str, expected: str
) -> None:
    assert provider.generate(question, schema).sql == expected
