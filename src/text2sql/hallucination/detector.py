"""Schema-grounding checks that detect hallucinated tables, columns and joins in SQL."""

from __future__ import annotations

import difflib
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

from text2sql.models import HallucinationIssue, HallucinationReport, QueryResult, Severity
from text2sql.schema import DatabaseSchema

SEVERITY_PENALTY = {Severity.ERROR: 0.4, Severity.WARNING: 0.15, Severity.INFO: 0.05}

TABLE_SUGGESTION_CUTOFF = 0.6
COLUMN_SUGGESTION_CUTOFF = 0.5

# Questions phrased like this expect a single aggregate value back.
SINGLE_AGGREGATE_RE = re.compile(
    r"^\s*(how many|what is the (total|average|sum|number|count)"
    r"|what's the (total|average)|count )",
    re.IGNORECASE,
)

# Scopes allowed to see their parent's sources (correlated subqueries).
_CORRELATED_SCOPES = (ScopeType.SUBQUERY, ScopeType.SET_OPERATION)
_MAX_DEPTH = 32


def _closest(name: str, candidates: Iterable[str], cutoff: float) -> str | None:
    """Closest candidate to ``name`` (case-insensitive), in the candidate's own spelling."""
    by_lower: dict[str, str] = {}
    for c in candidates:
        by_lower.setdefault(c.lower(), c)
    match = difflib.get_close_matches(name.lower(), list(by_lower), n=1, cutoff=cutoff)
    return by_lower[match[0]] if match else None


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s"):
        return name[:-1]
    return name


@dataclass
class _SourceInfo:
    """What is known about one FROM/JOIN source: its real table and output columns."""

    table: str | None  # canonical schema table name, when the source is a real table
    columns: dict[str, str] | None  # lower -> display name; None when unknowable


@dataclass
class _Analysis:
    issues: list[HallucinationIssue] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)

    def add(self, issue: HallucinationIssue) -> None:
        key = (issue.type, issue.reference, issue.message)
        if all((i.type, i.reference, i.message) != key for i in self.issues):
            self.issues.append(issue)


class HallucinationDetector:
    """Checks SQL against a known schema and scores how grounded it is."""

    def __init__(
        self,
        schema: DatabaseSchema,
        db_path: str | Path | None = None,
        threshold: float = 0.5,
        dialect: str = "sqlite",
    ) -> None:
        self.schema = schema
        self.db_path = Path(db_path) if db_path is not None else None
        self.threshold = threshold
        self.dialect = dialect

    # -- public API ---------------------------------------------------------------

    def analyze(self, sql: str) -> HallucinationReport:
        """Return grounding issues, a confidence score and the referenced objects."""
        result = _Analysis()
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except (SqlglotError, ValueError) as e:
            statements = []
            reason = str(e).splitlines()[0] if str(e) else type(e).__name__
        else:
            reason = "no SQL statement found"
        if not statements:
            result.add(
                HallucinationIssue(
                    type="parse_error",
                    message=f"SQL could not be parsed: {reason}",
                    severity=Severity.ERROR,
                )
            )
            return self._report(result)

        for tree in statements:
            self._check_tables(tree, result)
            try:
                scopes = traverse_scope(tree)
            except Exception:  # sqlglot internals; fall back to a simpler walk
                scopes = None
            if scopes:
                self._check_columns(scopes, result)
                self._check_joins(scopes, result)
            else:
                self._check_columns_simple(tree, result)

        if self.db_path is not None and not any(
            i.severity == Severity.ERROR for i in result.issues
        ):
            self._check_plan(sql, result)
        return self._report(result)

    def check_result(self, question: str, result: QueryResult) -> list[HallucinationIssue]:
        """Post-execution sanity warnings about the shape of the result."""
        if result.row_count == 0:
            return [
                HallucinationIssue(
                    type="empty_result",
                    message="The query returned no rows; filters may not match the data.",
                    severity=Severity.WARNING,
                )
            ]
        wants_single = bool(SINGLE_AGGREGATE_RE.search(question)) or (
            "total number of" in question.lower()
        )
        if wants_single and result.row_count > 1:
            return [
                HallucinationIssue(
                    type="unexpected_shape",
                    message=(
                        "The question asks for a single value but the query returned "
                        f"{result.row_count} rows."
                    ),
                    severity=Severity.WARNING,
                )
            ]
        return []

    # -- scoring ------------------------------------------------------------------

    def _report(self, result: _Analysis) -> HallucinationReport:
        penalty = sum(SEVERITY_PENALTY[i.severity] for i in result.issues)
        confidence = round(min(1.0, max(0.0, 1.0 - penalty)), 3)
        hallucinated = confidence < self.threshold or any(
            i.severity == Severity.ERROR for i in result.issues
        )
        return HallucinationReport(
            issues=result.issues,
            confidence=confidence,
            is_hallucinated=hallucinated,
            referenced_tables=sorted(result.tables),
            referenced_columns=sorted(result.columns),
        )

    # -- tables -------------------------------------------------------------------

    def _check_tables(self, tree: exp.Expression, result: _Analysis) -> None:
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                continue  # table-valued function such as json_each(...)
            name = table.name
            known = self.schema.table(name)
            if known is not None:
                result.tables.add(known.name)
            elif name.lower() not in cte_names:
                result.add(
                    HallucinationIssue(
                        type="unknown_table",
                        message=f"Table '{name}' does not exist in the database.",
                        severity=Severity.ERROR,
                        reference=name,
                        suggestion=_closest(name, self.schema.table_names, TABLE_SUGGESTION_CUTOFF),
                    )
                )

    # -- columns (scope based) ----------------------------------------------------

    def _source_info(self, source: object, depth: int = 0) -> _SourceInfo:
        if isinstance(source, exp.Table):
            table = (
                self.schema.table(source.name) if isinstance(source.this, exp.Identifier) else None
            )
            if table is None:
                return _SourceInfo(None, None)
            return _SourceInfo(table.name, {c.lower(): c for c in table.column_names})
        if isinstance(source, Scope):
            return _SourceInfo(None, self._scope_output(source, depth + 1))
        return _SourceInfo(None, None)

    def _scope_output(self, scope: Scope, depth: int = 0) -> dict[str, str] | None:
        """Output column names of a CTE / derived table / union scope."""
        if depth > _MAX_DEPTH:
            return None
        declared = _declared_columns(scope.expression)
        if declared is not None:
            return declared
        expression = scope.expression
        if isinstance(expression, exp.SetOperation):
            return (
                self._scope_output(scope.union_scopes[0], depth + 1) if scope.union_scopes else None
            )
        if not isinstance(expression, exp.Select):
            return None
        names: dict[str, str] = {}
        sources = _lower_keys(scope.selected_sources)
        for item in expression.expressions:
            if isinstance(item, exp.Star):
                expanded = [self._source_info(src, depth).columns for _, src in sources.values()]
            elif isinstance(item, exp.Column) and isinstance(item.this, exp.Star):
                entry = sources.get(item.table.lower())
                expanded = [self._source_info(entry[1], depth).columns if entry else None]
            else:
                names.setdefault(item.alias_or_name.lower(), item.alias_or_name)
                continue
            for cols in expanded:
                if cols is None:
                    return None
                for lower, display in cols.items():
                    names.setdefault(lower, display)
        return names

    def _check_columns(self, scopes: list[Scope], result: _Analysis) -> None:
        for scope in scopes:
            chain = _scope_chain(scope)
            aliases = _select_aliases(scope.expression)
            for column in scope.columns:
                if isinstance(column.this, exp.Star):
                    continue
                if column.table:
                    self._check_qualified(column, chain, result)
                elif column.name.lower() not in aliases:
                    self._check_unqualified(column, scope, chain, result)

    def _check_qualified(self, column: exp.Column, chain: list[Scope], result: _Analysis) -> None:
        qualifier, name = column.table, column.name
        for scope in chain:
            entry = _lower_keys(scope.selected_sources).get(qualifier.lower())
            if entry is None:
                continue
            info = self._source_info(entry[1])
            if info.columns is None:
                return
            if name.lower() in info.columns:
                if info.table:
                    result.columns.add(f"{info.table}.{info.columns[name.lower()]}")
                return
            where = f"table '{info.table}'" if info.table else f"'{qualifier}'"
            result.add(
                HallucinationIssue(
                    type="unknown_column",
                    message=f"Column '{name}' does not exist in {where}.",
                    severity=Severity.ERROR,
                    reference=f"{qualifier}.{name}",
                    suggestion=_closest(name, info.columns.values(), COLUMN_SUGGESTION_CUTOFF),
                )
            )
            return
        result.add(
            HallucinationIssue(
                type="unknown_column",
                message=f"Column '{qualifier}.{name}' refers to unknown table or alias "
                f"'{qualifier}'.",
                severity=Severity.ERROR,
                reference=f"{qualifier}.{name}",
                suggestion=_closest(qualifier, self._chain_aliases(chain), 0.6),
            )
        )

    def _check_unqualified(
        self, column: exp.Column, scope: Scope, chain: list[Scope], result: _Analysis
    ) -> None:
        name = column.name
        candidates: list[str] = []
        searched = False
        for level, current in enumerate(chain):
            matches: list[_SourceInfo] = []
            unknowable = False
            for _, source in current.selected_sources.values():
                searched = True
                info = self._source_info(source)
                if info.columns is None:
                    unknowable = True
                elif name.lower() in info.columns:
                    matches.append(info)
                else:
                    candidates.extend(info.columns.values())
            if matches:
                if level == 0 and len(matches) > 1 and not _in_using(scope, name):
                    tables = ", ".join(m.table or "?" for m in matches)
                    result.add(
                        HallucinationIssue(
                            type="ambiguous_column",
                            message=f"Column '{name}' is ambiguous; it exists in {tables}.",
                            severity=Severity.WARNING,
                            reference=name,
                        )
                    )
                for m in matches:
                    if m.table and m.columns is not None:
                        result.columns.add(f"{m.table}.{m.columns[name.lower()]}")
                return
            if unknowable:
                return
        if not searched:
            return  # e.g. ORDER BY of a UNION: nothing to resolve against
        result.add(
            HallucinationIssue(
                type="unknown_column",
                message=f"Column '{name}' does not exist in any table in scope.",
                severity=Severity.ERROR,
                reference=name,
                suggestion=_closest(name, candidates, COLUMN_SUGGESTION_CUTOFF),
            )
        )

    def _chain_aliases(self, chain: list[Scope]) -> list[str]:
        return [alias for scope in chain for alias in scope.selected_sources]

    # -- columns (fallback when scope building fails) -----------------------------

    def _check_columns_simple(self, tree: exp.Expression, result: _Analysis) -> None:
        aliases: dict[str, str | None] = {}
        for table in tree.find_all(exp.Table):
            known = (
                self.schema.table(table.name) if isinstance(table.this, exp.Identifier) else None
            )
            aliases[table.alias_or_name.lower()] = known.name if known else None
        opaque = any(True for _ in tree.find_all(exp.Subquery, exp.CTE)) or None in aliases.values()
        select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}
        for column in tree.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                continue
            name = column.name
            if column.table:
                table_name = aliases.get(column.table.lower())
                tables = [table_name] if table_name else []
            elif opaque or name.lower() in select_aliases:
                continue
            else:
                tables = [t for t in aliases.values() if t]
            known = [self.schema.table(t) for t in tables]
            columns = [c for t in known if t for c in t.column_names]
            if not tables:
                continue
            if name.lower() in (c.lower() for c in columns):
                continue
            result.add(
                HallucinationIssue(
                    type="unknown_column",
                    message=f"Column '{name}' does not exist in {', '.join(tables)}.",
                    severity=Severity.ERROR,
                    reference=column.sql(),
                    suggestion=_closest(name, columns, COLUMN_SUGGESTION_CUTOFF),
                )
            )

    # -- joins --------------------------------------------------------------------

    def _check_joins(self, scopes: list[Scope], result: _Analysis) -> None:
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                continue
            for join in scope.expression.args.get("joins") or []:
                condition = join.args.get("on")
                if condition is None:
                    continue
                for eq in condition.find_all(exp.EQ):
                    left, right = eq.this, eq.expression
                    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                        continue
                    a, b = self._resolve(left, scope), self._resolve(right, scope)
                    if a is None or b is None or a[0].lower() == b[0].lower():
                        continue
                    if not self._related(a, b):
                        result.add(
                            HallucinationIssue(
                                type="suspicious_join",
                                message=(
                                    f"Join condition {left.sql()} = {right.sql()} links "
                                    f"{a[0]}.{a[1]} and {b[0]}.{b[1]}, which are not related "
                                    "by a foreign key or naming convention."
                                ),
                                severity=Severity.WARNING,
                                reference=eq.sql(),
                            )
                        )

    def _resolve(self, column: exp.Column, scope: Scope) -> tuple[str, str] | None:
        """Real ``(table, column)`` a column reference points at, if unambiguous."""
        sources = _lower_keys(scope.selected_sources)
        if column.table:
            entries = [sources[column.table.lower()]] if column.table.lower() in sources else []
        else:
            entries = list(sources.values())
        found = []
        for _, source in entries:
            info = self._source_info(source)
            if info.table and info.columns and column.name.lower() in info.columns:
                found.append((info.table, info.columns[column.name.lower()]))
        return found[0] if len(found) == 1 else None

    def _related(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        (ta, ca), (tb, cb) = (a[0].lower(), a[1].lower()), (b[0].lower(), b[1].lower())
        for (t1, c1), (t2, c2) in (((ta, ca), (tb, cb)), ((tb, cb), (ta, ca))):
            table = self.schema.table(t1)
            for fk in table.foreign_keys if table else []:
                if (
                    fk.column.lower() == c1
                    and fk.ref_table.lower() == t2
                    and fk.ref_column.lower() == c2
                ):
                    return True
            # orders.customer_id = customers.id
            if c2 == "id" and c1 in (f"{_singular(t2)}_id", f"{t2}_id"):
                return True
        # order_items.product_id = cart.product_id
        return ca == cb and ca != "id"

    # -- database plan ------------------------------------------------------------

    def _check_plan(self, sql: str, result: _Analysis) -> None:
        assert self.db_path is not None
        statement = sql.strip().rstrip(";").strip()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()
        except sqlite3.Error as e:
            result.add(
                HallucinationIssue(
                    type="plan_error",
                    message=f"The database could not plan the query: {e}",
                    severity=Severity.ERROR,
                )
            )
        finally:
            if conn is not None:
                conn.close()


# -- helpers ----------------------------------------------------------------------


def _lower_keys(sources: dict[str, tuple]) -> dict[str, tuple]:
    return {k.lower(): v for k, v in sources.items()}


def _scope_chain(scope: Scope) -> list[Scope]:
    """The scope plus the outer scopes a correlated subquery may reference."""
    chain = [scope]
    current = scope
    while current.parent is not None and current.scope_type in _CORRELATED_SCOPES:
        current = current.parent
        chain.append(current)
    return chain


def _select_aliases(expression: exp.Expression) -> set[str]:
    if not isinstance(expression, exp.Select):
        return set()
    return {e.alias.lower() for e in expression.expressions if isinstance(e, exp.Alias)}


def _declared_columns(expression: exp.Expression) -> dict[str, str] | None:
    """Column list declared on a CTE or derived table alias, e.g. ``t(a, b)``."""
    node = expression
    while isinstance(node.parent, exp.SetOperation):
        node = node.parent
    parent = node.parent
    if isinstance(parent, exp.CTE | exp.Subquery):
        alias = parent.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.columns:
            return {c.name.lower(): c.name for c in alias.columns}
    return None


def _in_using(scope: Scope, name: str) -> bool:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return False
    for join in expression.args.get("joins") or []:
        for ident in join.args.get("using") or []:
            if ident.name.lower() == name.lower():
                return True
    return False
