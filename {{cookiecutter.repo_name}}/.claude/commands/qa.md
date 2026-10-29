---
description: "Run the full local quality gate for cookie-cutter-dbx — lint, tests, coverage — and triage whatever fails. Triggers: /qa, \"run the tests\", \"run lint\", \"check everything passes\", \"is this ready to commit\", \"pre-commit check\", \"why is lint failing\", \"why is the build red\"."
argument-hint: "[--quick | --wheel | --fix]"
allowed-tools: Read, Edit, Glob, Grep, Bash
---

# /qa — the local quality gate

**Modes:** `--quick` skip slow tests · `--wheel` also test the built wheel ·
`--fix` triage and repair failures (otherwise report only).

## 1. Run

```bash
make lint      # black, isort, flake8, pylint, mypy, autoflake
make test      # pytest + coverage into test-reports/
```

`--quick` → `make test-quick` (skips `@pytest.mark.slow`).
`--wheel` → also `make test-wheel-locally`: builds a wheel, installs it in a throwaway
venv and tests the **installed** package. Required for any packaging or dependency
change — it is the only thing that catches a module missing from the wheel.

Paste the **real transcripts** (P0 #3). Never summarize what you expect them to say.

## 2. Triage

`make lint` **rewrites files** — `black`, `isort` and `autoflake` edit in place, so a
first non-zero exit often just means "files were reformatted". Re-run to confirm it
settles, and **re-read any file it touched** before editing it (P0 #1).

| Failure | Likely cause |
|---|---|
| `D101`/`D102`/`D103` | missing docstring on a public class, method or function. **Required** — `flake8-docstrings` enforces these even though `pylint` disables its own check |
| `C901` / radon | cyclomatic complexity over 10 — decompose the function |
| `F401` unused import | `autoflake` will strip it; if it is a deliberate re-export, make it referenced |
| `ModuleNotFoundError` | run `make install` — the `src/` layout needs the editable install |
| mypy noise | it runs with no `[tool.mypy]` section, so defaults only — do not over-fit to it |
| passes alone, fails in suite | shared state, not flakiness: module-level mutable, session fixture, or an import side effect |

Then check what the green actually covers:

```bash
python -m pytest --cov=src --cov-report=term-missing
```

`MINIMUM_TEST_COVERAGE_PERCENT` is `90`, so the build fails below 90% overall — but a green
suite with the changed lines uncovered still proves nothing about them. Look at the report.

## 3. Fix (`--fix` only)

One hypothesis, one targeted change, rerun. **Three attempts maximum**, then stop and
report what you tried and what you now believe.

Never clear a gate by weakening it (P0 #10): no `# noqa`, no new ignore, no deleted
assertion, no lowered coverage floor, no test edited to match the code. Fix the cause
or report the blocker.

Without `--fix`, report findings and stop — do not edit.

End with `In one sentence: lint = <X>; tests = <N passed/M failed>; coverage of changes = <assessed/not assessed>.`
