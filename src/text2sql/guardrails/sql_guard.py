"""AST-based guardrail that only lets single, read-only, bounded queries through.

Every security decision is made on the ``sqlglot`` syntax tree, never on the raw
SQL text, so comments, casing and nesting cannot be used to smuggle statements.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from text2sql.models import GuardrailResult, Violation
from text2sql.schema import is_system_table


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    # Node classes vary between sqlglot releases; resolve the ones that exist.
    return tuple(getattr(exp, n) for n in names if isinstance(getattr(exp, n, None), type))


# Roots that are plain read queries. A ``WITH`` clause hangs off these nodes.
_READ_ROOTS = _node_types("Select", "Union", "Intersect", "Except", "SetOperation")

# Anything that writes, changes schema, controls transactions or administers the
# database. ``Command`` is sqlglot's fallback for statements it does not model
# (VACUUM, REPLACE INTO, EXPLAIN, ...), so it is forbidden as well.
_FORBIDDEN_NODES = _node_types(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Drop",
    "Create",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Command",
    "Pragma",
    "Attach",
    "Detach",
    "Transaction",
    "Commit",
    "Rollback",
    "Analyze",
    "Set",
    "Use",
    "Into",
    "Copy",
    "LoadData",
    "Grant",
    "Revoke",
    "Kill",
    "Cache",
    "Uncache",
    "Refresh",
)

# Empty statements produced by stray semicolons or trailing comments.
_EMPTY_NODES = _node_types("Semicolon")

# Functions that can touch the filesystem, load code, leak build info or burn CPU/memory.
DANGEROUS_FUNCTIONS = frozenset(
    {
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
        "zeroblob",
        "randomblob",
        "sqlite_compileoption_get",
        "sqlite_compileoption_used",
        "sleep",
    }
)

# Prefix of SQLite's table-valued pragma functions, e.g. ``pragma_table_info``.
_PRAGMA_PREFIX = "pragma_"

# Virtual tables that expose database internals without the ``sqlite_`` prefix.
_INTERNAL_TABLES = frozenset({"dbstat"})


def _violation(type_: str, message: str) -> Violation:
    return Violation(type=type_, message=message)


def _function_names(node: exp.Func) -> set[str]:
    """Every name a function node may be known by (raw call name and sqlglot name)."""
    names = {type(node).__name__.lower()}
    if isinstance(node, exp.Anonymous):
        names.add(node.name.lower())
    sql_name = getattr(node, "sql_name", None)
    if callable(sql_name):
        names.add(sql_name().lower())
    return names


def _is_dangerous(names: set[str]) -> bool:
    return bool(names & DANGEROUS_FUNCTIONS) or any(n.startswith(_PRAGMA_PREFIX) for n in names)


def _query_ctes(node: exp.Expression) -> list[exp.CTE]:
    # ``Query.ctes`` exists in recent sqlglot; fall back to the raw ``with`` arg.
    ctes = getattr(node, "ctes", None)
    if ctes is not None:
        return list(ctes)
    with_ = node.args.get("with") or node.args.get("with_")
    return list(with_.expressions) if with_ else []


def _is_cte_reference(table: exp.Table) -> bool:
    """True when an unqualified table name resolves to a CTE in an enclosing scope.

    SQLite resolves a name against every CTE of an enclosing ``WITH`` (including
    siblings declared later), so walking the ancestors is sufficient.
    """
    if table.args.get("db") or table.args.get("catalog"):
        return False
    name = table.name.lower()
    parent = table.parent
    while parent is not None:
        if any(cte.alias_or_name.lower() == name for cte in _query_ctes(parent)):
            return True
        parent = parent.parent
    return False


def _table_nodes(tree: exp.Expression) -> Iterable[exp.Table]:
    """Real table references, skipping table-valued functions (checked as functions)."""
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func) or not table.name:
            continue
        yield table


def extract_tables(sql: str, dialect: str = "sqlite") -> set[str]:
    """Lower-cased names of physical tables referenced by ``sql`` (CTE names excluded)."""
    tables: set[str] = set()
    for statement in sqlglot.parse(sql, read=dialect):
        if statement is None:
            continue
        tables.update(t.name.lower() for t in _table_nodes(statement) if not _is_cte_reference(t))
    return tables


class SQLGuardrail:
    """Validate generated SQL and rewrite it so it returns at most ``max_rows`` rows."""

    def __init__(
        self,
        allowed_tables: Iterable[str] | None = None,
        max_rows: int = 100,
        dialect: str = "sqlite",
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        self.allowed_tables = frozenset(t.lower() for t in allowed_tables or ())
        self.max_rows = max_rows
        self.dialect = dialect

    def check(self, sql: str) -> GuardrailResult:
        """Return a blocked result with violations, or an ok result with ``rewritten_sql``."""
        if not sql or not sql.strip():
            return GuardrailResult.blocked([_violation("empty_sql", "SQL is empty.")])

        try:
            parsed = sqlglot.parse(sql, read=self.dialect)
        except SqlglotError as exc:  # ParseError, TokenError, ...
            return GuardrailResult.blocked(
                [_violation("parse_error", f"SQL could not be parsed: {_first_line(exc)}")]
            )

        statements = [s for s in parsed if s is not None and not isinstance(s, _EMPTY_NODES)]
        if not statements:
            return GuardrailResult.blocked([_violation("empty_sql", "SQL has no statement.")])

        violations: list[Violation] = []
        if len(statements) > 1:
            violations.append(
                _violation(
                    "multiple_statements",
                    f"Only a single statement is allowed, found {len(statements)}.",
                )
            )
        for statement in statements:
            violations.extend(self._statement_violations(statement))
        if violations:
            return GuardrailResult.blocked(_dedupe(violations))

        try:
            rewritten = self._enforce_limit(statements[0]).sql(dialect=self.dialect)
        except SqlglotError as exc:  # pragma: no cover - generation rarely fails
            return GuardrailResult.blocked(
                [_violation("parse_error", f"SQL could not be rewritten: {_first_line(exc)}")]
            )
        return GuardrailResult.ok(rewritten_sql=rewritten)

    # -- checks ---------------------------------------------------------------

    def _statement_violations(self, statement: exp.Expression) -> list[Violation]:
        violations: list[Violation] = []

        if not _is_read_query(statement):
            violations.append(
                _violation(
                    "forbidden_statement",
                    f"Only SELECT queries are allowed, got {_statement_kind(statement)}.",
                )
            )

        for node in statement.walk():
            if node is not statement and isinstance(node, _FORBIDDEN_NODES):
                violations.append(
                    _violation(
                        "forbidden_statement",
                        f"Nested {_statement_kind(node)} is not allowed inside a query.",
                    )
                )
            elif isinstance(node, exp.Func) and _is_dangerous(names := _function_names(node)):
                shown = node.name if isinstance(node, exp.Anonymous) else min(names)
                violations.append(
                    _violation("dangerous_function", f"Function '{shown.lower()}' is not allowed.")
                )

        for table in _table_nodes(statement):
            name = table.name.lower()
            if is_system_table(name) or name.startswith(_PRAGMA_PREFIX) or name in _INTERNAL_TABLES:
                violations.append(
                    _violation("table_not_allowed", f"System table '{name}' is not accessible.")
                )
            elif (
                self.allowed_tables
                and name not in self.allowed_tables
                and not _is_cte_reference(table)
            ):
                violations.append(
                    _violation("table_not_allowed", f"Table '{name}' is not in the allowlist.")
                )
        return violations

    # -- rewriting ------------------------------------------------------------

    def _enforce_limit(self, query: exp.Expression) -> exp.Expression:
        """Cap the outermost query at ``max_rows`` while keeping any OFFSET."""
        query = query.copy()
        limit = query.args.get("limit")
        current = limit.expression if isinstance(limit, exp.Limit) else None
        if (
            isinstance(current, exp.Literal)
            and current.is_int
            and int(current.this) <= self.max_rows
        ):
            return query
        # Missing, too large, or not a plain integer (parameter, expression, FETCH,
        # negative which means "unbounded" in SQLite): replace with the cap.
        query.set("limit", exp.Limit(expression=exp.Literal.number(self.max_rows)))
        return query


def _is_read_query(statement: exp.Expression) -> bool:
    # ``(SELECT ...)`` parses as a Subquery wrapping the real query.
    while isinstance(statement, exp.Subquery):
        statement = statement.this
    return isinstance(statement, _READ_ROOTS)


def _statement_kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    return node.key.upper()


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def _dedupe(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for v in violations:
        if (v.type, v.message) not in seen:
            seen.add((v.type, v.message))
            unique.append(v)
    return unique
