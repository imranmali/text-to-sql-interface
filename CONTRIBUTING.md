# Contributing

Thanks for helping improve the Text-to-SQL interface. This guide covers how
work is branched, specified, committed and reviewed.

## Branching model

- `main` is protected: no direct pushes. Everything lands through a pull
  request that passes CI.
- Each unit of work lives on its own branch named `feature/<name>`, e.g.
  `feature/sql-guardrails`, `feature/ci-cd`. Use `fix/<name>` for bug fixes.
- Larger efforts made of several feature branches are combined on an
  integration branch. Feature branches are merged into it with `--no-ff` so
  each feature stays visible as a single merge in history:

  ```bash
  git checkout <integration-branch>
  git merge --no-ff feature/<name>
  ```

- The integration branch (or a single feature branch, for small changes) is
  then opened as a pull request into `main`.

## Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<optional scope>): <summary>` in the imperative mood, with a body that
explains *why* when it is not obvious.

| Type       | Use for                                     | Example                                                     |
| ---------- | ------------------------------------------- | ----------------------------------------------------------- |
| `feat`     | New user-facing behaviour                   | `feat(guardrails): block multi-statement SQL`               |
| `fix`      | Bug fixes                                   | `fix(executor): enforce row limit on UNION queries`         |
| `docs`     | Documentation only                          | `docs: document TEXT2SQL_* environment variables`           |
| `test`     | Adding or fixing tests                      | `test(hallucination): cover unknown-column detection`       |
| `ci`       | CI/CD workflows                             | `ci: run tests on Python 3.13`                              |
| `chore`    | Tooling, dependencies, housekeeping         | `chore: bump ruff to 0.7`                                   |
| `refactor` | Code changes that do not alter behaviour    | `refactor(schema): extract foreign-key loading`             |

Other accepted types: `build`, `perf`, `style`. Mark breaking changes with `!`
after the type (`feat!: ...`) and a `BREAKING CHANGE:` footer.

## OpenSpec workflow

Behaviour changes are specified with [OpenSpec](https://github.com/Fission-AI/OpenSpec)
before they are implemented. Specs live in `openspec/specs/`; proposed changes
live in `openspec/changes/<change-id>/` (proposal, design, tasks and spec
deltas) until they are archived.

| Step      | Slash command (Claude Code) | CLI equivalent                           |
| --------- | --------------------------- | ---------------------------------------- |
| Propose   | `/opsx:propose`             | `openspec new change <change-id>`        |
| Validate  | -                           | `openspec validate <change-id> --strict` |
| Implement | `/opsx:apply`               | work through `tasks.md`, ticking tasks   |
| Archive   | `/opsx:archive`             | `openspec archive <change-id>`           |

1. **Propose** a change and write its proposal, design, tasks and spec deltas.
2. **Validate** it with `openspec validate --all --strict` (CI runs the same
   command).
3. **Apply** it: implement the tasks on a feature branch, with tests.
4. **Archive** it once merged, which folds the spec deltas into
   `openspec/specs/`.

Install the CLI with `npm install -g @fission-ai/openspec`. Set
`OPENSPEC_TELEMETRY=0` to opt out of telemetry.

Changes that do not alter behaviour (CI, docs, dependency bumps) do not need
an OpenSpec change.

## Local development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check .                   # lint
ruff format .                  # format
pytest                         # tests
pytest --cov=text2sql --cov-report=term-missing   # tests with coverage
```

CI requires at least 85% coverage, a clean `ruff check .` and
`ruff format --check .`, and a passing `openspec validate --all --strict`.

To build and run the container locally:

```bash
docker build -t text2sql .
docker run --rm -p 8000:8000 text2sql
curl http://localhost:8000/health
```

## Pull request checklist

Before requesting review, make sure that:

- [ ] The branch is up to date with its base and named `feature/<name>`.
- [ ] Behaviour changes have a validated OpenSpec change linked in the PR.
- [ ] New behaviour is covered by tests and `pytest` passes locally.
- [ ] `ruff check .` and `ruff format --check .` are clean.
- [ ] `openspec validate --all --strict` passes.
- [ ] Docs (README, specs, this guide) are updated where relevant.
- [ ] Commits follow Conventional Commits.
