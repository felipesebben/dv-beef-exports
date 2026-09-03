# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Market-intelligence tool for a Brazilian beef export trading company, built on [ComexStat](https://comexstat.mdic.gov.br) (Brazil's official foreign trade statistics, MDIC). Answers "what is Brazil selling, to where, and which markets look promising" from public trade data — company/importer-level discovery is an explicit non-goal of this data source (see `docs/ROADMAP.md` Phase 4).

Two-person coworking/learning project (see `docs/decisions/0001-initial-technical-foundations.md`), on GitHub Flow, iterating phase by phase per `docs/ROADMAP.md`.

## Commands

```bash
uv sync --all-extras --dev     # install deps (requires uv)
uv run pytest                  # run all tests (coverage report prints automatically, see pyproject.toml)
uv run pytest tests/ingestion/test_duckdb_loader.py::test_load_exports_inserts_rows  # single test
uv run ruff check .             # lint
uv run ruff format .            # format
uv run pre-commit install       # one-time: run ruff on every commit
```

CI (`.github/workflows/ci.yml`) runs `ruff check` + `pytest` on every PR into `main`; both must pass before merge.

## Architecture

`src/dv_beef_exports/` — `ingestion/` (pulling and loading ComexStat data), `analysis/` (Phase 2, not started), `app/` (Streamlit prototype, Phase 3, not started).

**Ingestion pipeline** (`ingestion/`):
- `ncm_codes.py` — the 11 beef NCM codes this pipeline tracks, each tagged with a `category` (`frozen`, `offal`, `salted_dried`, `processed`). Fresh/chilled beef and live cattle/hides are deliberately excluded — different commodities/product lines (see ADR 0002).
- `comexstat_client.fetch_exports()` — calls `POST /general` on `api-comexstat.mdic.gov.br` (no auth). Requests `?language=en` so country/NCM names come back human-readable directly in the response. See `docs/comexstat-api-reference.md` for the confirmed endpoint/param reference (from the actual OpenAPI spec) — covers `/general/dates/updated` (needed for ADR 0003's automation step), the `/tables/*` dimension endpoints, and the fact that `country` filter values are numeric codes. Raises `ComexStatError` (non-retryable: bad request, unexpected shape) or `ComexStatTransientError` (retryable via `tenacity`, 4 attempts, exponential backoff capped at 30s). **Both 403 and 429 are classified as transient** — the API sits behind a WAF that soft-blocks with undocumented, occasional 403s and 429s ("rate limited", found via live use, not something mocked tests caught).
- `duckdb_loader.py` — loads fetched rows into the tracked `data/processed/comexstat.duckdb` file, table `exports`, `PRIMARY KEY (ncm_code, country, year, month)`. `load_exports()` deletes-then-inserts scoped to the requested `ncm_codes`/period before inserting, so re-running the same pull is idempotent. Period scoping compares `year*100+month` as a single int rather than a row-tuple — DuckDB rejects Postgres-style `(year, month) >= (?, ?)` tuple comparison against `BETWEEN`.

**Data layer restructure in progress** (`docs/decisions/0004-medallion-data-layering.md`): the current single-step `exports` table is being split into three schemas inside the same tracked `.duckdb` file — `raw` (API responses close to as-returned, append-only, one row per pull tagged with `fetched_at` and what was requested — never overwritten, so nothing ever needs a re-pull for a logic change), `staging` (typed/deduped/cleaned, joins the NCM reference table, dedup picks the most recent `raw` pull per key), `marts` (business judgment: kg→metric-ton conversion, cross-category weight comparability — categories are kept separate here rather than blended, since e.g. canned/processed beef's net weight isn't directly comparable to a frozen cut's). This restructure is meant to land, along with real dimension tables (NCM hierarchy, country/bloc), *before* the full historical backfill (all 11 NCM codes × 1997–present) — see the ADR for why, and `docs/decisions/0002-comexstat-data-access-strategy.md` for the confirmed API/bulk-data shapes behind the design.

**Data directory conventions** (`.gitignore`):
- `data/processed/comexstat.duckdb` — tracked in git (small, narrow NCM scope; doubles as change history — see ADR 0003). Everything else in `data/processed/` is ignored.
- `data/raw/` — gitignored scratch, regenerable, not the asset.
- `data/samples/` — gitignored reference-only sample data (bulk CSVs, dimension tables) used to design against real shapes; not part of the pipeline itself.

## Conventions

- Branches: `feat/<name>`, `fix/<name>`, `chore/<name>`; PRs squash-merged into `main` (`docs/WORKFLOW.md`).
- Commits: conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`).
- Add an ADR under `docs/decisions/` only for choices that would be confusing to re-derive later (data source, library, storage/schema shape) — not routine implementation details. Update `docs/DEVLOG.md` when a PR lands something worth remembering.
- API tests use `requests_mock`; DuckDB tests use `tmp_path` for an isolated `.duckdb` file per test (see `tests/ingestion/`).
