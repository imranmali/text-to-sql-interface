from pathlib import Path

import pytest

from text2sql.hallucination.detector import SEVERITY_PENALTY, HallucinationDetector
from text2sql.models import QueryResult, Severity
from text2sql.schema import DatabaseSchema


@pytest.fixture
def detector(schema: DatabaseSchema, db_path: Path) -> HallucinationDetector:
    return HallucinationDetector(schema, db_path=db_path)


@pytest.fixture
def offline(schema: DatabaseSchema) -> HallucinationDetector:
    """Detector without a database, so only static checks run."""
    return HallucinationDetector(schema)


def issue_types(report) -> list[str]:
    return [i.type for i in report.issues]


# -- unknown tables ------------------------------------------------------------------


def test_misspelled_table_suggests_closest(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT * FROM customer")
    assert issue_types(report) == ["unknown_table"]
    issue = report.issues[0]
    assert issue.reference == "customer"
    assert issue.suggestion == "customers"
    assert issue.severity == Severity.ERROR
    assert report.is_hallucinated


def test_unknown_table_without_close_match(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT * FROM spaceships")
    assert issue_types(report) == ["unknown_table"]
    assert report.issues[0].suggestion is None


def test_table_names_are_case_insensitive(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT FIRST_NAME FROM Customers")
    assert report.issues == []
    assert report.referenced_tables == ["customers"]


def test_cte_name_is_not_an_unknown_table(detector: HallucinationDetector) -> None:
    report = detector.analyze("WITH big AS (SELECT * FROM orders) SELECT COUNT(*) FROM big")
    assert report.issues == []


# -- unknown columns -----------------------------------------------------------------


def test_invented_column_on_alias(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT c.full_name FROM customers c")
    assert issue_types(report) == ["unknown_column"]
    issue = report.issues[0]
    assert issue.reference == "c.full_name"
    assert issue.suggestion in ("first_name", "last_name")
    assert report.is_hallucinated


def test_unknown_column_qualified_with_table_name(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT customers.full_name FROM customers")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].suggestion in ("first_name", "last_name")


def test_unknown_unqualified_column(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT name, colour FROM products")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].reference == "colour"


def test_column_from_wrong_table(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT o.email FROM orders o")
    assert issue_types(report) == ["unknown_column"]


def test_qualifier_not_in_from_clause(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT z.name FROM products")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].reference == "z.name"


def test_alias_and_cte_columns_are_valid(detector: HallucinationDetector) -> None:
    report = detector.analyze("WITH t AS (SELECT id AS cid FROM customers) SELECT cid FROM t")
    assert report.issues == []
    assert not report.is_hallucinated


def test_unknown_column_of_cte(offline: HallucinationDetector) -> None:
    report = offline.analyze("WITH t AS (SELECT id AS cid FROM customers) SELECT id FROM t")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].suggestion == "cid"


def test_cte_star_exposes_source_columns(offline: HallucinationDetector) -> None:
    assert offline.analyze("WITH t AS (SELECT * FROM customers) SELECT email FROM t").issues == []
    report = offline.analyze("WITH t AS (SELECT * FROM customers) SELECT emale FROM t")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].suggestion == "email"


def test_cte_declared_column_list(offline: HallucinationDetector) -> None:
    report = offline.analyze("WITH t(a) AS (SELECT id FROM products) SELECT a, b FROM t")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].reference == "b"


def test_derived_table_columns(detector: HallucinationDetector) -> None:
    sql = "SELECT x.a FROM (SELECT id AS a FROM products) x WHERE x.a > 3"
    assert detector.analyze(sql).issues == []
    report = detector.analyze("SELECT x.id FROM (SELECT id AS a FROM products) x")
    assert issue_types(report) == ["unknown_column"]


def test_recursive_cte(detector: HallucinationDetector) -> None:
    sql = (
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) "
        "SELECT n FROM t"
    )
    assert detector.analyze(sql).issues == []


def test_select_alias_in_group_order_and_having(detector: HallucinationDetector) -> None:
    sql = (
        "SELECT category AS cat, COUNT(*) AS n FROM products "
        "GROUP BY cat HAVING n > 1 ORDER BY n DESC"
    )
    assert detector.analyze(sql).issues == []


# -- valid complex queries -----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM orders",
        "SELECT c.first_name, c.last_name, SUM(o.total_amount) AS spent "
        "FROM customers c JOIN orders o ON o.customer_id = c.id "
        "GROUP BY c.id ORDER BY spent DESC LIMIT 5",
        "SELECT p.name, SUM(oi.quantity) AS units FROM order_items oi "
        "JOIN products p ON p.id = oi.product_id "
        "JOIN orders o ON o.id = oi.order_id WHERE o.status = 'shipped' GROUP BY p.name",
        "SELECT * FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
        "SELECT name FROM products p WHERE price > "
        "(SELECT AVG(price) FROM products p2 WHERE p2.category = p.category)",
        "SELECT name FROM products UNION SELECT first_name FROM customers ORDER BY name",
        "SELECT name, ROW_NUMBER() OVER (PARTITION BY category ORDER BY price DESC) AS rn "
        "FROM products",
        "SELECT strftime('%Y', order_date) AS yr, SUM(total_amount) FROM orders GROUP BY yr",
        "SELECT city, COUNT(*) FROM customers WHERE country = 'Canada' GROUP BY city",
        "SELECT a.product_id FROM order_items a JOIN order_items b ON a.product_id = b.product_id",
    ],
)
def test_valid_queries_have_no_issues(detector: HallucinationDetector, sql: str) -> None:
    report = detector.analyze(sql)
    assert report.issues == [], report.issues
    assert report.confidence == 1.0
    assert not report.is_hallucinated


def test_referenced_tables_and_columns(detector: HallucinationDetector) -> None:
    report = detector.analyze(
        "SELECT c.first_name, o.total_amount FROM customers c JOIN orders o ON o.customer_id = c.id"
    )
    assert report.referenced_tables == ["customers", "orders"]
    assert report.referenced_columns == [
        "customers.first_name",
        "customers.id",
        "orders.customer_id",
        "orders.total_amount",
    ]


# -- ambiguity and joins -------------------------------------------------------------


def test_ambiguous_id(offline: HallucinationDetector) -> None:
    report = offline.analyze(
        "SELECT id FROM customers JOIN orders ON customers.id = orders.customer_id"
    )
    assert issue_types(report) == ["ambiguous_column"]
    assert report.issues[0].reference == "id"
    assert report.issues[0].severity == Severity.WARNING


def test_select_alias_is_not_ambiguous(offline: HallucinationDetector) -> None:
    report = offline.analyze(
        "SELECT customers.id AS id FROM customers "
        "JOIN orders ON customers.id = orders.customer_id ORDER BY id"
    )
    assert report.issues == []


def test_suspicious_join(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT * FROM customers JOIN products ON customers.id = products.id")
    assert issue_types(report) == ["suspicious_join"]
    assert report.issues[0].severity == Severity.WARNING


def test_join_by_naming_convention_without_fk(schema: DatabaseSchema) -> None:
    # order_items.product_id has an FK, but the naming rule alone should also accept it.
    stripped = DatabaseSchema(
        {k: type(v)(name=v.name, columns=v.columns) for k, v in schema.tables.items()}
    )
    detector = HallucinationDetector(stripped)
    ok = detector.analyze("SELECT * FROM order_items oi JOIN products p ON oi.product_id = p.id")
    assert ok.issues == []
    bad = detector.analyze("SELECT * FROM orders o JOIN products p ON o.customer_id = p.id")
    assert issue_types(bad) == ["suspicious_join"]


# -- database plan -------------------------------------------------------------------


def test_plan_error_for_unknown_function(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT nosuchfunc(name) FROM products")
    assert issue_types(report) == ["plan_error"]
    assert "nosuchfunc" in report.issues[0].message
    assert report.is_hallucinated


def test_plan_skipped_when_static_errors_exist(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT nosuchfunc(full_name) FROM customers")
    assert issue_types(report) == ["unknown_column"]


def test_plan_check_does_not_modify_database(detector: HallucinationDetector) -> None:
    report = detector.analyze("DELETE FROM orders")
    assert "plan_error" not in issue_types(report)  # planned only, never executed
    again = detector.analyze("SELECT COUNT(*) FROM orders")
    assert again.issues == []


def test_parse_error(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELEC * FROM")
    assert issue_types(report) == ["parse_error"]
    assert report.is_hallucinated
    assert detector.analyze("").issues[0].type == "parse_error"


# -- scoring -------------------------------------------------------------------------


def test_clean_query_confidence(detector: HallucinationDetector) -> None:
    report = detector.analyze("SELECT name, price FROM products WHERE price > 10")
    assert report.confidence == 1.0
    assert report.confidence >= detector.threshold
    assert not report.is_hallucinated


def test_one_warning_confidence(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT * FROM customers JOIN products ON customers.id = products.id")
    assert report.confidence == 0.85
    assert not report.is_hallucinated


def test_one_error_is_hallucinated(offline: HallucinationDetector) -> None:
    report = offline.analyze("SELECT full_name FROM customers")
    assert report.confidence == pytest.approx(1.0 - SEVERITY_PENALTY[Severity.ERROR])
    assert report.is_hallucinated


def test_confidence_is_clamped_at_zero(offline: HallucinationDetector) -> None:
    report = offline.analyze(
        "SELECT * FROM nope1 JOIN nope2 ON nope1.x = nope2.y JOIN nope3 ON nope3.z = nope1.x"
    )
    assert report.confidence == 0.0
    assert report.is_hallucinated


def test_threshold_applies_to_warnings(schema: DatabaseSchema) -> None:
    strict = HallucinationDetector(schema, threshold=0.9)
    report = strict.analyze("SELECT * FROM customers JOIN products ON customers.id = products.id")
    assert report.confidence == 0.85
    assert report.is_hallucinated


# -- result checks -------------------------------------------------------------------


def test_empty_result_warning(detector: HallucinationDetector) -> None:
    issues = detector.check_result("Which customers live on Mars?", QueryResult(row_count=0))
    assert [i.type for i in issues] == ["empty_result"]
    assert issues[0].severity == Severity.WARNING


@pytest.mark.parametrize(
    "question",
    [
        "How many orders were placed?",
        "What is the total revenue?",
        "what's the average price",
        "What is the total number of customers?",
    ],
)
def test_unexpected_shape_for_single_aggregate(
    detector: HallucinationDetector, question: str
) -> None:
    result = QueryResult(columns=["n"], rows=[[1], [2]], row_count=2)
    issues = detector.check_result(question, result)
    assert [i.type for i in issues] == ["unexpected_shape"]


def test_result_checks_pass_for_plausible_results(detector: HallucinationDetector) -> None:
    single = QueryResult(columns=["n"], rows=[[42]], row_count=1)
    assert detector.check_result("How many orders were placed?", single) == []
    many = QueryResult(columns=["name"], rows=[["a"], ["b"]], row_count=2)
    assert detector.check_result("List all product names", many) == []


@pytest.mark.parametrize(
    "question",
    [
        "How many orders per status?",
        "What is the total number of customers per city?",
        "How many products by category?",
        "What is the average price for each category?",
    ],
)
def test_breakdown_questions_allow_many_rows(
    detector: HallucinationDetector, question: str
) -> None:
    many = QueryResult(columns=["k", "n"], rows=[["a", 1], ["b", 2]], row_count=2)
    assert detector.check_result(question, many) == []


def test_fallback_when_scope_building_fails(
    offline: HallucinationDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    import text2sql.hallucination.detector as detector_module

    def boom(_tree):
        raise RuntimeError("scope failure")

    monkeypatch.setattr(detector_module, "traverse_scope", boom)
    assert offline.analyze("SELECT c.first_name FROM customers c").issues == []
    report = offline.analyze("SELECT c.full_name FROM customers c")
    assert issue_types(report) == ["unknown_column"]
    assert report.issues[0].suggestion in ("first_name", "last_name")
