"""Database schema introspection used for prompting and grounding checks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEM_TABLE_PREFIXES = ("sqlite_",)


def is_system_table(name: str) -> bool:
    return name.lower().startswith(SYSTEM_TABLE_PREFIXES)


@dataclass(frozen=True)
class Column:
    name: str
    type: str = ""
    primary_key: bool = False
    nullable: bool = True


@dataclass(frozen=True)
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    sample_values: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def has_column(self, name: str) -> bool:
        return name.lower() in (c.lower() for c in self.column_names)


@dataclass
class DatabaseSchema:
    """All user tables of a database, keyed case-insensitively by name."""

    tables: dict[str, Table] = field(default_factory=dict)

    @classmethod
    def from_sqlite(cls, db_path: str | Path, sample_rows: int = 3) -> DatabaseSchema:
        uri = f"file:{Path(db_path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
                if not is_system_table(row[0])
            ]
            tables: dict[str, Table] = {}
            for name in names:
                quoted = name.replace('"', '""')
                columns = [
                    Column(
                        name=row[1],
                        type=row[2] or "",
                        nullable=not row[3],
                        primary_key=bool(row[5]),
                    )
                    for row in conn.execute(f'PRAGMA table_info("{quoted}")')
                ]
                fks = [
                    ForeignKey(column=row[3], ref_table=row[2], ref_column=row[4])
                    for row in conn.execute(f'PRAGMA foreign_key_list("{quoted}")')
                ]
                samples: dict[str, list[Any]] = {}
                for col in columns:
                    if col.type.upper() in ("TEXT", "VARCHAR") or not col.type:
                        col_q = col.name.replace('"', '""')
                        rows = conn.execute(
                            f'SELECT DISTINCT "{col_q}" FROM "{quoted}" '
                            f'WHERE "{col_q}" IS NOT NULL LIMIT ?',
                            (sample_rows,),
                        ).fetchall()
                        samples[col.name] = [r[0] for r in rows]
                tables[name.lower()] = Table(
                    name=name, columns=columns, foreign_keys=fks, sample_values=samples
                )
            return cls(tables=tables)
        finally:
            conn.close()

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables.values()]

    def table(self, name: str) -> Table | None:
        return self.tables.get(name.lower())

    def has_table(self, name: str) -> bool:
        return name.lower() in self.tables

    def restrict(self, allowed: tuple[str, ...] | list[str]) -> DatabaseSchema:
        """Return a copy containing only ``allowed`` tables (all when empty)."""
        if not allowed:
            return self
        keep = {a.lower() for a in allowed}
        return DatabaseSchema({k: v for k, v in self.tables.items() if k in keep})

    def to_prompt(self) -> str:
        """Compact DDL-like description for LLM prompts."""
        lines = []
        for table in self.tables.values():
            cols = []
            for c in table.columns:
                desc = f"{c.name} {c.type}".strip()
                if c.primary_key:
                    desc += " PRIMARY KEY"
                cols.append(desc)
            for fk in table.foreign_keys:
                cols.append(f"FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})")
            lines.append(f"CREATE TABLE {table.name} (\n  " + ",\n  ".join(cols) + "\n);")
            examples = {k: v for k, v in table.sample_values.items() if v}
            if examples:
                shown = "; ".join(f"{k}: {v!r}" for k, v in examples.items())
                lines.append(f"-- example values: {shown}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": [
                {
                    "name": t.name,
                    "columns": [
                        {"name": c.name, "type": c.type, "primary_key": c.primary_key}
                        for c in t.columns
                    ],
                    "foreign_keys": [
                        {
                            "column": fk.column,
                            "ref_table": fk.ref_table,
                            "ref_column": fk.ref_column,
                        }
                        for fk in t.foreign_keys
                    ],
                }
                for t in self.tables.values()
            ]
        }
