# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Market-intelligence tool for a Brazilian beef export trading company, built on [ComexStat](https://comexstat.mdic.gov.br) (Brazil's official foreign trade statistics, MDIC). Answers "what is Brazil selling, to where, and which markets look promising" from public trade data — company/importer-level discovery is an explicit non-goal of this data source (see `docs/ROADMAP.md` Phase 4).

Two-person coworking/learning project (see `docs/decisions/0001-initial-technical-foundations.md`), on GitHub Flow, iterating phase by phase per `docs/ROADMAP.md`.

## Commands

```bash
uv sync --all-extras --dev     # install deps (requires uv)
uv run pytest                  # run all tests (coverage report prints automatically, see pyproject.toml)
uv run pytest tests/ingestion/test_duckdb_loader.py::test_ingest_raw_appends_rows  # single test
uv run ruff check .             # lint
uv run ruff format .            # format
uv run pre-commit install       # one-time: run ruff on every commit
```

CI (`.github/workflows/ci.yml`) runs `ruff check` + `pytest` on every PR into `main`; both must pass before merge.

## Architecture

`src/dv_beef_exports/` — `ingestion/` (pulling and loading ComexStat data), `analysis/` (Phase 2, not started), `app/` (Streamlit prototype, Phase 3, not started).

**Ingestion pipeline** (`ingestion/`):
- `ncm_codes.py` — the 11 beef NCM codes this pipeline tracks, each tagged with a `category` (`frozen`, `offal`, `salted_dried`, `processed`). Fresh/chilled beef and live cattle/hides are deliberately excluded — different commodities/product lines (see ADR 0002).
- `comexstat_client.py` — `fetch_exports()` calls `POST /general` on `api-comexstat.mdic.gov.br` (no auth), requesting `?language=en` so country/NCM names come back human-readable directly. `fetch_countries()`, `fetch_economic_blocks()`, `fetch_country_blocs()`, `fetch_ncm_hierarchy()` call the `GET /tables/*` dimension endpoints — same host, same `Referer` header, no special `User-Agent` needed (that's only required for the interactive `/docs` HTML page itself). See `docs/comexstat-api-reference.md` for the confirmed endpoint/param reference (from the actual OpenAPI spec), including that the live `/tables/*` responses are thinner than the sample dimensional workbook (no ISO codes, no "Section" level). Raises `ComexStatError` (non-retryable: bad request, unexpected shape) or `ComexStatTransientError` (retryable via `tenacity`, 4 attempts, exponential backoff capped at 30s, shared by both the `POST /general` and `GET /tables/*` code paths via `_handle_response()`). **Both 403 and 429 are classified as transient** — the API sits behind a WAF that soft-blocks with undocumented, occasional 403s and 429s ("rate limited", found via live use, not something mocked tests caught).
- `duckdb_loader.py` — three schemas inside the tracked `data/processed/comexstat.duckdb` file, per `docs/decisions/0004-medallion-data-layering.md`:
  - `raw.exports` — append-only, minimal casting (every column but the provenance fields is `VARCHAR`, even ones that look numeric — the live response's actual field types are still unconfirmed). `ingest_raw()` never deletes or overwrites; every call stamps its rows with a fresh `pull_id`/`fetched_at`, so re-pulling the same NCM codes/period is expected to add duplicate rows, not replace anything.
  - `staging.dim_ncm` — upserted (not replaced) from `ncm_codes.py` on every `get_connection()` call, so its code/description/category columns can't drift from the source. Its `sh6_code`/`unit` columns are populated separately by `refresh_ncm_hierarchy()` (one `/tables/ncm?add=sh` call per tracked NCM code) — upsert rather than replace is what keeps a later `get_connection()` from wiping that enrichment back to null.
  - `staging.dim_ncm_hierarchy` — one row per distinct SH6 group among the tracked NCM codes (several share one, e.g. the bone-in cuts), built by `refresh_ncm_hierarchy()`. Chapter/heading/subheading only — no "Section" level (the API doesn't expose one).
  - `staging.dim_country` / `staging.dim_economic_bloc` / `staging.bridge_country_bloc` — built by `refresh_dim_country()`. Bloc membership is a many-to-many bridge table, not a column on `dim_country` — a country can belong to more than one bloc (e.g. a region and a trade bloc at once).
  - `refresh_ncm_hierarchy()`/`refresh_dim_country()` are explicit/occasional, deliberately **not** run on every `get_connection()` — they make live API calls, and dimension data changes far less often than trade data, so opening a connection shouldn't be coupled to network availability.
  - `staging.exports` — rebuilt from scratch by `build_staging()` (`CREATE OR REPLACE TABLE ... AS SELECT`, not incremental — data volume is small enough that a full rebuild is simpler than upsert logic). Dedups to the most recent `raw` pull per `(ncm_code, country, year, month)` via `QUALIFY ROW_NUMBER() OVER (... ORDER BY fetched_at DESC) = 1`, and joins `dim_ncm` for name/category. Does **not** currently join `dim_ncm_hierarchy` or the country/bloc tables — that's left for Phase 2 analysis to wire in when actually needed.
  - `marts.exports` — rebuilt from `staging` by `build_marts()`, adds `kg / 1000.0 AS metric_ton`. `category` stays a plain column, not rolled up across categories — cross-category weight comparability (e.g. canned/processed beef's net weight isn't directly comparable to a frozen cut's) stays an open, query-time question rather than baked into the schema.
  - `staging.exports`/`marts.exports` don't exist until their build function has run at least once — querying them before that fails loudly rather than silently returning zero rows.

The full historical backfill (all 11 NCM codes × 1997–present) is next, now that both the loader restructure and the dimension tables are in place.

**Data directory conventions** (`.gitignore`):
- `data/processed/comexstat.duckdb` — tracked in git (small, narrow NCM scope; doubles as change history — see ADR 0003). Everything else in `data/processed/` is ignored.
- `data/raw/` — gitignored scratch, regenerable, not the asset.
- `data/samples/` — gitignored reference-only sample data (bulk CSVs, dimension tables) used to design against real shapes; not part of the pipeline itself.

## Conventions

- Branches: `feat/<name>`, `fix/<name>`, `chore/<name>`; PRs squash-merged into `main` (`docs/WORKFLOW.md`).
- Commits: conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`).
- Add an ADR under `docs/decisions/` only for choices that would be confusing to re-derive later (data source, library, storage/schema shape) — not routine implementation details. Update `docs/DEVLOG.md` when a PR lands something worth remembering.
- API tests use `requests_mock`; DuckDB tests use `tmp_path` for an isolated `.duckdb` file per test (see `tests/ingestion/`).
