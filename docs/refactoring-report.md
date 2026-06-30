# Refactoring Report: scoring threshold consistency

## Target scope and purpose

- Contract: `REFACTOR_WORKFLOW.md`
- Target scope: scoring-threshold refactor surface identified in the current worktree:
  - `ai_researcher/config.py`
  - `ai_researcher/scorer.py`
  - `ai_researcher/collector.py`
  - `ai_researcher/reporter.py`
  - directly related test/config/docs files
- Purpose: keep public APIs and observable report behavior stable except for correcting inconsistent relevance-threshold handling. Improve maintainability, readability, testability, and boundary-value safety around `reporting.score_threshold`.

## Baseline before changes

Baseline was checked against a clean HEAD clone at `/tmp/ai-researcher-baseline-codex-20260623-0108`.

| Command | Baseline result | Current result | Classification |
| --- | --- | --- | --- |
| `pytest` | FAIL: `ModuleNotFoundError: No module named 'ai_researcher'` during collection | PASS: `11 passed` after adding `pytest.ini` | Existing test invocation issue fixed |
| `PYTHONPATH=. pytest` | PASS: `8 passed` | PASS: `11 passed` | No regression |
| `python -m compileall ai_researcher tests` | PASS | PASS | No regression |
| `git diff --check` | PASS | PASS | No whitespace errors |
| `python -m mypy ai_researcher tests` | FAIL: same 19 errors in product/test code and missing stubs | FAIL: same 19 errors | Existing unconfigured type-check failure, not introduced here |
| `python -m ruff check ai_researcher tests` | FAIL: `No module named ruff` | FAIL: `No module named ruff` | Tool unavailable |
| `docker compose build` | Not code-reachable in this environment | FAIL before build: Docker context uses unsupported `npipe`; `--context default` has no `/var/run/docker.sock` | Environment/Docker daemon unavailable |

No project-specific lint/type/build configuration was found beyond `pytest.ini`.

## Improvement backlog

| ID | Decision | Priority | Problem and evidence | Expected effect | Risk | Plan and tests |
| --- | --- | --- | --- | --- | --- | --- |
| ST-001 | Adopted | P0 | `scorer.py` returned rounded `score` but computed `relevance` from the unrounded internal score; `collector.py` then recomputed relevance side effects from rounded `article["score"]`. Boundary cases such as raw score `2.9996` with threshold `3.0` could disagree. | One published score value drives score, relevance, candidate recording, and source-hit counting. | Boundary values within rounding distance may change, intentionally correcting inconsistent behavior. | Compute `rounded_score` once in `score_article()`, use it for `score` and `relevance`, and let `collect()` use returned `relevance`. Covered by `tests/test_scoring_threshold.py`. |
| ST-002 | Adopted | P1 | `collector.py` and `reporter.py` duplicated inline `float(config.get(..., 2.5))`; `scorer.py` hardcoded `2.5`. | Central default and resolver reduce drift. | Low; default stays `2.5`. | Add `DEFAULT_SCORE_THRESHOLD`, route callers through `score_threshold()`, pass threshold into `score_article()`. |
| ST-TEST-001 | Adopted | P1 | Existing tests only covered feedback features; no direct test for scoring threshold behavior. | Regression coverage for the refactor surface. | Low; tests use local SQLite temp DB and monkeypatch fetcher. | Add `tests/test_scoring_threshold.py`. |
| ST-TEST-002 | Adopted | P1 | Initial threshold tests did not cover the rounded-boundary bug. | Direct guard for the highest-risk boundary. | Low. | Add a case where normal scoring inputs produce returned `score == 3.0` and `relevance == 1` at threshold `3.0`. |
| ST-DOC-001 | Adopted | P2 | `ARCHITECTURE.md` referred to `config.sources.json`; actual file is `config/sources.json`. | Accurate architecture documentation. | Low. | Correct the path. |
| ST-003 | Deferred | P2 | `score_threshold()` does not validate non-finite/invalid values. | Better config error messages. | Could be a breaking behavior change for currently accepted invalid config values. | Defer until validation policy is specified. |
| ST-004 | Deferred | P2 | Item-level malformed numeric values in `raw` can fail a whole collection run. | Better resilience to bad source data. | May hide data-quality problems and expands error-handling policy. | Defer to a dedicated fetch/scoring robustness task. |
| ST-005 | Deferred | P2 | Existing DB `score`/`relevance` use `MAX()` in upsert and can preserve stale high scores. | Clearer current-vs-best score semantics. | High observable behavior risk for historical DBs. | Defer until persistence semantics are specified. |
| ST-006 | Rejected for this scope | P3 | Parallel fetch, HTTP client replacement, formatter/linter dependency setup, and truncation helper consolidation are useful but outside the threshold refactor surface. | Avoids unrelated churn. | Scope creep and public behavior changes. | Record as future work only. |

## Changes made

### `ai_researcher/config.py`

- Added `DEFAULT_SCORE_THRESHOLD = 2.5`.
- Updated `score_threshold()` to use the shared default.

### `ai_researcher/scorer.py`

- Added optional keyword-only `threshold` parameter to `score_article()`, defaulting to `DEFAULT_SCORE_THRESHOLD`.
- Added `rounded_score = round(score, 3)`.
- Returned `score` and `relevance` now both use `rounded_score`.

### `ai_researcher/collector.py`

- Replaced duplicated inline threshold extraction with `score_threshold(config)`.
- Passed the resolved threshold into `score_article()`.
- Candidate/source-hit side effects now use returned `article["relevance"]`.

### `ai_researcher/reporter.py`

- Replaced duplicated inline threshold extraction with `score_threshold(config)`.
- Kept SQL filtering on saved rounded `score >= threshold` to preserve the existing report-selection contract.

### `pytest.ini`

- Added `pythonpath = .` so `pytest` from repo root imports `ai_researcher` without ad hoc environment variables.
- Added `testpaths = tests` to keep test discovery scoped.

### `tests/test_scoring_threshold.py`

- Covers default threshold `2.5` versus configured threshold `3.0`.
- Covers the rounded-boundary case where returned score is `3.0` and relevance is `1`.
- Covers `collect()` using configured threshold for stored relevance and source hit counts.

### `ARCHITECTURE.md`

- Corrected `config.sources.json` to `config/sources.json`.

## Review log

| Step | Agent role | Result | Notes |
| --- | --- | --- | --- |
| Architecture/responsibility analysis | `refactor_analyzer` | Complete | Identified rounded-score threshold inconsistency, config dependency concerns, and deferred DB semantics. |
| Duplication/readability analysis | `refactor_analyzer` | Complete | Identified duplicated threshold extraction and missing dedicated tests. |
| Type/boundary/testability analysis | `refactor_analyzer` | Complete | Identified boundary risk, missing tests, existing mypy/import limitations, and deferred invalid-config handling. |
| ST-TEST-001 implementation | `refactor_worker` | PASS | Added `pytest.ini` and threshold tests; did not edit production modules. |
| ST-001 implementation | `refactor_worker` | PASS | Updated `scorer.py` and `collector.py`; implementation did not self-approve. |
| ST-TEST-002 implementation | `refactor_worker` | PASS | Added rounded-boundary test. |
| ST-001 independent review | `code_reviewer` | PASS | No findings; verified shared default, threshold parameter, rounded-score relevance, collector side effects, and reporter resolver. |
| Test/config independent review | `code_reviewer` | PASS | No findings; verified deterministic tests and no skipped/weakened tests. |
| Final review before report | `code_reviewer` | FAIL | Code passed; blockers were missing `docs/refactoring-report.md` and an `ARCHITECTURE.md` path typo. Both were fixed in this report pass. |
| Final test before report | `test_runner` | FAIL | `pytest`, `compileall`, and `git diff --check` passed; final status failed because this report was missing and unconfigured `mypy` has existing failures. |
| Final review after report | `code_reviewer` | FAIL | Report exists and required sections are present, but `.gitignore` still hid `docs/refactoring-report.md`; fixed by unignoring this file. |
| Final test after report | `test_runner` | FAIL | `pytest`, `compileall`, `git diff --check`, and report existence passed; `docker compose build` failed before executing a build because Docker is unavailable in this environment. |
| Final review after git visibility fix | `code_reviewer` | PASS | No findings; verified report sections, git visibility, scoped changes, docs, tests, and public behavior. |
| Final test after git visibility fix | `test_runner` | PASS | In-process/configured checks passed; Docker build failure classified as environment because it fails before project build steps. |

## Test commands and results

Current worktree verification:

- `pytest`
  - Result: PASS, `11 passed in 0.89s`
- `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider`
  - Result: PASS, `11 passed in 0.89s`
- `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_scoring_threshold.py`
  - Result: PASS, `3 passed`
- `python -m compileall ai_researcher tests`
  - Result: PASS
- `python -m py_compile ai_researcher/config.py ai_researcher/scorer.py ai_researcher/collector.py ai_researcher/reporter.py tests/test_scoring_threshold.py`
  - Result: PASS
- `git diff --check`
  - Result: PASS
- `docker compose config`
  - Result: PASS
- `docker compose build`
  - Result: FAIL before build execution, `Failed to initialize: protocol not available`
- `docker --context default compose build`
  - Result: FAIL before build execution, `dial unix /var/run/docker.sock: connect: no such file or directory`
- `docker.exe compose config --quiet`
  - Result: FAIL before Docker/Compose evaluation, WSL `UtilBindVsockAnyPort` socket error
- `python -m ruff check ai_researcher tests`
  - Result: unavailable, `No module named ruff`
- `python -m mypy ai_researcher tests`
  - Result: FAIL, 19 errors reproduced identically on HEAD baseline; classified as existing/unconfigured type-check failure, not caused by this refactor.

## Pre-existing failures

- Plain `pytest` failed before this work because the package root was not on `sys.path`; `pytest.ini` fixes that test invocation issue.
- `python -m mypy ai_researcher tests` fails on HEAD and current with the same 19 errors, including product-code type issues, test type issues, and missing stubs for optional/external libraries. No project mypy configuration exists.
- `ruff` is not installed in the environment and there is no project ruff configuration.
- Docker build cannot start in this environment. The current Linux Docker context points at `npipe:////./pipe/dockerDesktopLinuxEngine`, which is unsupported here (`protocol not available`), `--context default` points at `/var/run/docker.sock`, which does not exist, and Windows `docker.exe` fails with a WSL vsock initialization error.

## Residual risks and recommendations

- `score_threshold()` still accepts whatever `float()` accepts, including unusual values such as `nan` or `inf`. Add explicit validation in a separate config-policy change.
- `db.upsert_article()` preserves maximum score/relevance for existing URLs. That may be intentional historical behavior, but it should be documented or split into current and best score semantics.
- `score_article()` can still fail on malformed numeric popularity fields. A future robustness task should decide whether to ignore invalid item fields or record item-level errors.
- Add a project-level type-check/lint configuration before treating global `mypy` or `ruff` availability as a release gate.

## Completion checklist

| Requirement | Status | Evidence |
| --- | --- | --- |
| Adopted improvements implemented or reasonably deferred | PASS | ST-001, ST-002, ST-TEST-001, ST-TEST-002, ST-DOC-001 implemented; higher-risk items deferred above. |
| Each implementation received independent review | PASS | ST-001 review PASS; test/config review PASS. |
| Final review PASS | PASS | Final code reviewer reported no findings after `docs/refactoring-report.md` became git-visible. |
| No new test failures from changes | PASS | `pytest` PASS; baseline comparison recorded. |
| Required tests/lint/type/build | PASS for runnable configured checks; Docker build blocked by environment | Tests, compile, diff check, report existence, git visibility, and compose config pass. Docker build cannot reach a Docker daemon in this environment before project code is built. |
| Public API/observable behavior preserved | PASS | `score_article()` adds only optional keyword-only threshold; default remains `2.5`; report SQL behavior preserved. |
| `docs/refactoring-report.md` exists | PASS | This file. |
