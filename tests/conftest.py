"""Shared fixtures: a fresh copy of the sample database per test session."""

from __future__ import annotations

from pathlib import Path

import pytest

from text2sql.config import Settings
from text2sql.sample_db import create_sample_database
from text2sql.schema import DatabaseSchema


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create_sample_database(tmp_path_factory.mktemp("db") / "sample.db")


@pytest.fixture(scope="session")
def schema(db_path: Path) -> DatabaseSchema:
    return DatabaseSchema.from_sqlite(db_path)


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return Settings(db_path=db_path, provider="rule_based")
