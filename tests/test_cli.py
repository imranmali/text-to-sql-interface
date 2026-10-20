from __future__ import annotations

import json
from pathlib import Path

import pytest

from text2sql import cli


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_init_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "x.db"
    code, out = run(capsys, "--db", str(db), "init-db")
    assert code == 0 and db.exists() and str(db) in out
    code, _ = run(capsys, "--db", str(db), "init-db", "--force")
    assert code == 0


def test_ask_success(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--db", str(db_path), "ask", "How many products are there?")
    assert code == 0
    assert "Status:     success" in out
    assert "SELECT" in out.upper()
    assert "20" in out


def test_ask_blocked(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--db", str(db_path), "ask", "drop table products")
    assert code != 0
    assert "blocked" in out


def test_ask_json(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--db", str(db_path), "ask", "--json", "How many orders are there?")
    assert code == 0
    assert json.loads(out)["status"] == "success"


def test_ask_creates_missing_database(tmp_path: Path, capsys) -> None:
    db = tmp_path / "new.db"
    code, _ = run(capsys, "--db", str(db), "ask", "--no-execute", "How many customers are there?")
    assert code == 0 and db.exists()


def test_serve_passes_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {}
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.update(args=a, kwargs=kw))
    monkeypatch.delenv("TEXT2SQL_DB_PATH", raising=False)
    monkeypatch.delenv("TEXT2SQL_PROVIDER", raising=False)
    db = tmp_path / "s.db"
    assert cli.main(["--db", str(db), "serve", "--port", "9000"]) == 0
    assert calls["args"] == ("text2sql.api:app",)
    assert calls["kwargs"]["port"] == 9000
    import os

    assert os.environ["TEXT2SQL_DB_PATH"] == str(db)
    cli.get_settings.cache_clear()


def test_requires_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main([])
