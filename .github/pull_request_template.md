## Summary

<!-- What does this PR do and why? One or two sentences. -->

## OpenSpec change

<!-- Link the change this PR implements, e.g. `openspec/changes/<change-id>/`.
     Write "n/a" for changes that do not alter behaviour (CI, docs, chores). -->

## Changes

-

## Testing

<!-- How was this verified? Commands run, new tests, manual checks. -->

```bash
ruff check . && ruff format --check .
pytest --cov=text2sql
```

## Checklist

- [ ] Tests added or updated for the new behaviour
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] `openspec validate --all --strict` passes
- [ ] Docs updated (README, specs, `CONTRIBUTING.md`) where relevant
- [ ] Commits follow Conventional Commits
