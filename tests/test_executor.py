"""Tests for the read-only SafeExecutor (OpenSpec query-execution)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from text2sql.executor import ExecutionError, SafeExecutor


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# Requirement: Read-only connection -------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM products",
        "UPDATE products SET price = 0",
        "INSERT INTO products (name, category, price, stock) VALUES ('x', 'y', 1, 1)",
        "DROP TABLE products",
    ],
)
def test_write_attempt_is_rejected_and_data_unchanged(db_path: Path, sql: str) -> None:
    before = _count(db_path, "products")
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(db_path).execute(sql)
    assert exc_info.value.kind == "execution_error"
    assert _count(db_path, "products") == before


def test_create_table_is_rejected(db_path: Path) -> None:
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(db_path).execute("CREATE TABLE x(a)")
    assert exc_info.value.kind == "execution_error"
    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert "x" not in names


# Requirement: Execution timeout ----------------------------------------------


def test_long_running_query_times_out(db_path: Path) -> None:
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    started = time.monotonic()
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(db_path, timeout_s=0.2).execute(sql)
    elapsed = time.monotonic() - started
    assert exc_info.value.kind == "timeout"
    assert "timeout" in str(exc_info.value).lower()
    assert elapsed < 2.0


# Requirement: Bounded structured results -------------------------------------


def test_successful_query(db_path: Path) -> None:
    result = SafeExecutor(db_path).execute("SELECT name FROM products LIMIT 3")
    assert result.columns == ["name"]
    assert len(result.rows) == 3
    assert result.row_count == 3
    assert result.truncated is False
    assert result.duration_ms >= 0
    assert all(isinstance(row, list) for row in result.rows)


def test_rows_are_capped_and_truncation_reported(db_path: Path) -> None:
    result = SafeExecutor(db_path, max_rows=5).execute("SELECT * FROM orders")
    assert result.row_count == 5
    assert len(result.rows) == 5
    assert result.truncated is True
    assert result.columns == ["id", "customer_id", "order_date", "status", "total_amount"]


def test_exactly_max_rows_is_not_truncated(db_path: Path) -> None:
    result = SafeExecutor(db_path, max_rows=3).execute("SELECT id FROM products LIMIT 3")
    assert result.row_count == 3
    assert result.truncated is False


def test_bytes_are_hex_encoded_and_json_serialisable(db_path: Path) -> None:
    result = SafeExecutor(db_path).execute("SELECT X'DEADBEEF' AS blob, 1 AS n, NULL AS z")
    assert result.rows == [["deadbeef", 1, None]]
    json.dumps(result.model_dump())


# Error kinds -------------------------------------------------------------------


def test_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(missing).execute("SELECT 1")
    assert exc_info.value.kind == "database_not_found"
    assert not missing.exists()


def test_syntax_error(db_path: Path) -> None:
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(db_path).execute("SELEC name FROM products")
    assert exc_info.value.kind == "execution_error"


def test_unknown_table(db_path: Path) -> None:
    with pytest.raises(ExecutionError) as exc_info:
        SafeExecutor(db_path).execute("SELECT * FROM no_such_table")
    assert exc_info.value.kind == "execution_error"


def test_unknown_kind_defaults_to_execution_error() -> None:
    assert ExecutionError("boom", kind="weird").kind == "execution_error"
    assert ExecutionError("boom").kind == "execution_error"
