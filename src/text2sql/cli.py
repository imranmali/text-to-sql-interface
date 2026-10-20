"""Command-line interface: ``text2sql init-db | ask | serve``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from text2sql.config import Settings, get_settings
from text2sql.models import PipelineResponse, PipelineStatus
from text2sql.sample_db import create_sample_database


def _print_response(resp: PipelineResponse) -> None:
    print(f"Status:     {resp.status.value}")
    if resp.sql:
        print(f"SQL:        {resp.sql}")
    if resp.executed_sql and resp.executed_sql != resp.sql:
        print(f"Executed:   {resp.executed_sql}")
    if resp.explanation:
        print(f"Explain:    {resp.explanation}")
    if resp.confidence is not None:
        print(f"Confidence: {resp.confidence:.2f}")
    for v in resp.violations:
        print(f"  [blocked] {v.type}: {v.message}")
    if resp.hallucination:
        for issue in resp.hallucination.issues:
            hint = f" (did you mean '{issue.suggestion}'?)" if issue.suggestion else ""
            print(f"  [{issue.severity.value}] {issue.type}: {issue.message}{hint}")
    for w in resp.warnings:
        print(f"  [warning] {w.type}: {w.message}")
    if resp.error:
        print(f"Error:      {resp.error}")
    if resp.result:
        print()
        print(" | ".join(resp.result.columns))
        for row in resp.result.rows:
            print(" | ".join("" if v is None else str(v) for v in row))
        more = " (truncated)" if resp.result.truncated else ""
        print(f"({resp.result.row_count} rows, {resp.result.duration_ms:.1f} ms){more}")


def _cmd_init_db(args: argparse.Namespace, settings: Settings) -> int:
    path = create_sample_database(settings.db_path, overwrite=args.force)
    print(f"Sample database ready at {path}")
    return 0


def _cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    from text2sql.pipeline import Text2SQLPipeline

    if not settings.db_path.exists():
        create_sample_database(settings.db_path)
    resp = Text2SQLPipeline(settings).run(args.question, execute=not args.no_execute)
    if args.json:
        print(json.dumps(resp.model_dump(mode="json"), indent=2))
    else:
        _print_response(resp)
    return 0 if resp.status is PipelineStatus.SUCCESS else 1


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    # The app reads its settings from the environment (it may be reloaded in
    # a subprocess), so hand the CLI overrides over that way.
    os.environ["TEXT2SQL_DB_PATH"] = str(settings.db_path)
    os.environ["TEXT2SQL_PROVIDER"] = settings.provider
    get_settings.cache_clear()
    uvicorn.run("text2sql.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text2sql", description="Text-to-SQL with guardrails and hallucination detection."
    )
    parser.add_argument("--db", type=Path, help="SQLite database path (env TEXT2SQL_DB_PATH)")
    parser.add_argument(
        "--provider",
        choices=["rule_based", "anthropic"],
        help="SQL generation provider (env TEXT2SQL_PROVIDER)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create the sample database")
    init.add_argument("--force", action="store_true", help="overwrite an existing database")
    init.set_defaults(func=_cmd_init_db)

    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the full response as JSON")
    ask.add_argument("--no-execute", action="store_true", help="generate and check SQL only")
    ask.set_defaults(func=_cmd_ask)

    serve = sub.add_parser("serve", help="run the HTTP API and web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.db:
        settings = replace(settings, db_path=args.db)
    if args.provider:
        settings = replace(settings, provider=args.provider)
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
