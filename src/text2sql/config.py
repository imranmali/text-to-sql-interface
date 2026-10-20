"""Runtime settings, read from ``TEXT2SQL_*`` environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_DB_PATH = Path("data/sample.db")


def _env(name: str, default: str) -> str:
    return os.environ.get(f"TEXT2SQL_{name}", default)


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(f"TEXT2SQL_{name}", "")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """Configuration for the whole pipeline.

    ``allowed_tables`` empty means "every user table in the schema"; system
    tables are always denied regardless.
    """

    db_path: Path = DEFAULT_DB_PATH
    provider: str = "rule_based"
    model: str = "claude-opus-5-5"
    max_rows: int = 100
    max_question_length: int = 1000
    query_timeout_s: float = 5.0
    confidence_threshold: float = 0.5
    max_regenerations: int = 1
    allowed_tables: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=Path(_env("DB_PATH", str(DEFAULT_DB_PATH))),
            provider=_env("PROVIDER", "rule_based").lower(),
            model=_env("MODEL", "claude-opus-5-5"),
            max_rows=int(_env("MAX_ROWS", "100")),
            max_question_length=int(_env("MAX_QUESTION_LENGTH", "1000")),
            query_timeout_s=float(_env("QUERY_TIMEOUT_S", "5")),
            confidence_threshold=float(_env("CONFIDENCE_THRESHOLD", "0.5")),
            max_regenerations=int(_env("MAX_REGENERATIONS", "1")),
            allowed_tables=_env_list("ALLOWED_TABLES"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
