"""Read-only, time-boxed, row-capped SQL execution against SQLite.

This is the last line of defence: even if every guardrail upstream failed,
the connection is opened with ``mode=ro`` and ``PRAGMA query_only = ON`` so
SQLite itself refuses writes, and a progress handler aborts queries that run
past the deadline (SQLite has no native statement timeout).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from text2sql.models import QueryResult

#: Number of SQLite VM instructions between progress-handler callbacks.
PROGRESS_INTERVAL = 1000

ERROR_KINDS = frozenset({"timeout", "execution_error", "database_not_found"})


class ExecutionError(RuntimeError):
    """A query could not be executed.

    ``kind`` is one of ``"timeout"``, ``"execution_error"`` or
    ``"database_not_found"``.
    """

    def __init__(self, message: str, kind: str = "execution_error") -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "execution_error"


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return value


class SafeExecutor:
    """Executes a single SQL statement over a read-only SQLite connection."""

    def __init__(self, db_path: str | Path, timeout_s: float = 5.0, max_rows: int = 100) -> None:
        self.db_path = Path(db_path)
        self.timeout_s = float(timeout_s)
        self.max_rows = int(max_rows)

    def _connect(self) -> sqlite3.Connection:
        resolved = self.db_path.resolve()
        if not resolved.is_file():
            raise ExecutionError(f"Database not found: {self.db_path}", kind="database_not_found")
        try:
            conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, check_same_thread=False)
        except sqlite3.Error as exc:  # pragma: no cover - e.g. unreadable file
            raise ExecutionError(
                f"Could not open database: {exc}", kind="database_not_found"
            ) from exc
        conn.execute("PRAGMA query_only = ON")
        return conn

    def execute(self, sql: str) -> QueryResult:
        """Run ``sql`` and return at most ``max_rows`` rows.

        Raises :class:`ExecutionError` on timeout, database errors (including
        any write attempt) or a missing database file.
        """
        conn = self._connect()
        timed_out = False
        start = time.monotonic()
        deadline = start + self.timeout_s

        def _check_deadline() -> int:
            nonlocal timed_out
            if time.monotonic() > deadline:
                timed_out = True
                return 1  # non-zero aborts the running statement
            return 0

        try:
            conn.set_progress_handler(_check_deadline, PROGRESS_INTERVAL)
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                columns = [d[0] for d in cursor.description] if cursor.description else []
                fetched = cursor.fetchmany(self.max_rows + 1) if columns else []
            except sqlite3.OperationalError as exc:
                if timed_out or "interrupted" in str(exc).lower():
                    raise ExecutionError(
                        f"Query exceeded the {self.timeout_s:g}s timeout and was aborted.",
                        kind="timeout",
                    ) from exc
                raise ExecutionError(f"SQL execution failed: {exc}") from exc
            except sqlite3.Error as exc:
                raise ExecutionError(f"SQL execution failed: {exc}") from exc
            duration_ms = (time.monotonic() - start) * 1000.0
        finally:
            conn.close()

        truncated = len(fetched) > self.max_rows
        rows = [[_to_jsonable(v) for v in row] for row in fetched[: self.max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=round(duration_ms, 3),
        )
