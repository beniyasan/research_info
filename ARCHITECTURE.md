# Architecture & Analysis

This document describes how **AI Researcher** is structured, how data flows
through the pipeline, the responsibility of each module, and the refactoring
that was applied to remove a scoring-threshold inconsistency.

## Overview

AI Researcher is a single-process Python CLI (`ai_researcher.cli`) backed by a
local SQLite database (`data/research.db`). It periodically collects AI-related
articles from public sources, scores them, asks an LLM to select the best ones
for a Japanese-language Markdown report, records adoption history, evolves its
own keyword/source lists from real report contribution, notifies Discord, and
syncs reports to Google Drive.

There is no web server and no external queue; orchestration is driven by cron
(`config/crontab`) inside a Docker container, calling CLI subcommands.

## Pipeline (data flow)

```
sources.json ──seed──▶ SQLite (sources, keywords)
                          │
            collect ──▶ fetchers ──▶ scorer ──▶ articles (+ candidates)
                          │
             report ──▶ candidate query ──▶ llm_selector ──▶ selected
                          │                                      │
                          │                 article_verifier (Gemini grounding)
                          │                                      │
                          ├──▶ reporter (Markdown) ──▶ reports/<period>/<date>.md
                          ├──▶ notifier ──▶ Discord (webhook / bot cards)
                          └──▶ drive_sync ──▶ Google Drive
                          │
            evolve  ──▶ promote/demote keywords, sources, candidates
        discord-bot ──▶ preferences (feedback influences future selection)
 discover-sources ──▶ source_discovery (Gemini Search) ──▶ candidate sources
```

The high-level `run-daily` / `run-scheduled` commands chain
`init → seed → collect → report → evolve` and apply weekly/monthly cadence.

## Module responsibilities

- `cli.py` — Argparse entry point. Maps subcommands (`init`, `seed`, `collect`,
  `report`, `evolve`, `notify-test`, `discord-bot`, `feedback-summary`,
  `drive-auth`, `drive-sync`, `discover-sources`, `run-daily`, `run-scheduled`)
  to module functions and prints JSON results.
- `config.py` — Loads `config/sources.json`, seeds sources/keywords into the DB,
  and exposes `score_threshold()` plus the canonical `DEFAULT_SCORE_THRESHOLD`.
- `db.py` — Owns the full SQLite schema, connection setup (WAL, foreign keys,
  busy timeout), lightweight migrations, and all CRUD/aggregation helpers.
- `fetchers.py` — Per-source-type fetchers (`rss`, `qiita`, `hn_algolia`,
  `html_index`, `deeplearning_batch`) normalized into a common article dict.
- `scorer.py` — Heuristic relevance scoring (keyword/title/body matches,
  category trust, source priority, recency, popularity) and candidate-keyword
  extraction. Returns `score` and a `relevance` flag computed against a
  configurable threshold.
- `collector.py` — Drives a collection run: fetch → score → upsert articles →
  record candidate domains/authors/keywords → write `data/raw/<date>/`.
- `reporter.py` — Selects period bounds, queries candidate articles, calls the
  LLM selector and verifier, renders Markdown, and triggers notify + Drive sync.
- `llm_selector.py` — Builds the selection prompt (including learned
  preferences), runs the configured Vertex/Gemini command, parses the JSON
  selection, and falls back to a heuristic selection on failure.
- `article_verifier.py` — Verifies selected articles via Gemini Search grounding
  and records verification status/confidence.
- `source_discovery.py` — Uses Gemini Search to discover candidate feeds and
  stores them for later promotion by `evolve`.
- `preferences.py` — Records Discord feedback, computes feature stats, and
  builds preference context that biases future LLM selection.
- `evolver.py` — Promotes/demotes keywords, sources, and candidates based on
  hit/adoption counts and age; writes an evolution report.
- `notifier.py` — Posts Discord summaries and per-article cards via webhook or
  bot REST, with retry-after handling.
- `discord_bot.py` — Gateway bot that renders feedback cards and persists
  ratings/comments (requires `discord.py`).
- `drive_sync.py` — Google Drive OAuth and Markdown upload/replace.
- `gemini_grounding.py` / `gemini_selector.py` — Vertex AI request helpers.
- `utils.py` — Shared helpers: HTTP fetch, HTML stripping, date parsing, URL
  canonicalization, stable IDs, and clustering keys.

## Storage model

SQLite tables (created idempotently in `db.SCHEMA`): `sources`, `keywords`,
`articles`, `candidates`, `candidate_events`, `runs`, `report_items`,
`drive_files`, `source_discoveries`, `article_verifications`,
`article_feedback`, `article_feedback_events`. `db._migrate()` adds newer
`articles` columns on existing databases.

## Refactoring applied

### Centralized the score threshold (behavioral fix)

The relevance threshold was defined in **four** places that could disagree:

- `config/sources.json` set `reporting.score_threshold = 3.0`.
- `collector.py` and `reporter.py` each re-read the config inline with a
  hardcoded `2.5` default.
- `scorer.score_article()` hardcoded `relevance = 1 if score >= 2.5`,
  ignoring config entirely.

The collector overwrote the scorer's `relevance` with the config value, but the
scorer's own `relevance` (and any direct caller) silently used `2.5`. This is a
latent correctness bug whenever the configured threshold differs from `2.5`.

Changes:

- Added `config.DEFAULT_SCORE_THRESHOLD = 2.5` as the single source of truth and
  routed `config.score_threshold()` through it.
- Gave `scorer.score_article(..., *, threshold=DEFAULT_SCORE_THRESHOLD)` a
  threshold parameter and removed the hardcoded `2.5`.
- Updated `collector.py` and `reporter.py` to call `config.score_threshold()`
  and pass the resolved threshold into `score_article`, removing the duplicated
  inline `.get("reporting", ...)` reads and the redundant `relevance` re-write.

Result: relevance is now computed once, consistently, against the configured
threshold across collection, scoring, and reporting.

### Deliberately not merged

`reporter._truncate` and `notifier._truncate` look similar but differ
intentionally — the notifier variant collapses whitespace before truncating
(suited to Discord embeds) while the reporter variant preserves Markdown
spacing. They were left separate to avoid changing rendered output.

## Suggested next steps

- Replace stdlib `urllib` HTTP in `utils`/`notifier` with the already-declared
  `requests` dependency for consistent timeouts, retries, and error handling.
- Add `pyproject.toml` with a console-script entry point and pin a formatter
  (`ruff`/`black`) so `ai-researcher` installs cleanly instead of relying on
  `python -m`.
- Parallelize per-source fetching (it is currently sequential) with a bounded
  thread pool, since each `collect` waits on network I/O per source.
- Extend the test suite beyond the feedback path to cover `scorer`, `collector`,
  and `reporter` (the modules touched by this refactor).
