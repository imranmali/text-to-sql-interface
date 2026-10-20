import sqlite3

from text2sql.models import HallucinationIssue, HallucinationReport, PipelineResponse
from text2sql.sample_db import create_sample_database
from text2sql.schema import DatabaseSchema, is_system_table


def test_sample_database_tables(schema: DatabaseSchema) -> None:
    assert sorted(schema.table_names) == ["customers", "order_items", "orders", "products"]


def test_columns_and_keys(schema: DatabaseSchema) -> None:
    customers = schema.table("CUSTOMERS")
    assert customers is not None
    assert customers.has_column("first_name")
    assert not customers.has_column("full_name")
    assert any(c.primary_key and c.name == "id" for c in customers.columns)
    orders = schema.table("orders")
    assert orders is not None
    fk = orders.foreign_keys[0]
    assert (fk.column, fk.ref_table, fk.ref_column) == ("customer_id", "customers", "id")


def test_sample_database_is_deterministic(tmp_path) -> None:
    a = create_sample_database(tmp_path / "a.db")
    b = create_sample_database(tmp_path / "b.db")
    query = "SELECT SUM(total_amount), COUNT(*) FROM orders"
    with sqlite3.connect(a) as ca, sqlite3.connect(b) as cb:
        assert ca.execute(query).fetchone() == cb.execute(query).fetchone()


def test_create_does_not_overwrite_by_default(tmp_path) -> None:
    path = create_sample_database(tmp_path / "s.db")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM order_items")
    create_sample_database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0
    create_sample_database(path, overwrite=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] > 0


def test_prompt_and_dict(schema: DatabaseSchema) -> None:
    prompt = schema.to_prompt()
    assert "CREATE TABLE orders" in prompt
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id)" in prompt
    data = schema.to_dict()
    assert {t["name"] for t in data["tables"]} == set(schema.table_names)


def test_restrict(schema: DatabaseSchema) -> None:
    assert schema.restrict(()) is schema
    assert schema.restrict(["Products"]).table_names == ["products"]


def test_system_tables() -> None:
    assert is_system_table("sqlite_master")
    assert not is_system_table("customers")


def test_report_feedback_and_response_json() -> None:
    report = HallucinationReport(
        issues=[
            HallucinationIssue(
                type="unknown_table", message="Unknown table 'customer'", suggestion="customers"
            )
        ],
        confidence=0.6,
        is_hallucinated=True,
    )
    assert report.feedback() == ["Unknown table 'customer' (did you mean 'customers'?)"]
    resp = PipelineResponse(question="q", status="hallucination", hallucination=report)
    assert '"status":"hallucination"' in resp.model_dump_json()
