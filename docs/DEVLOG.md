# Devlog

Running log of what landed and why. One entry per merged PR that adds
something worth remembering (see `docs/WORKFLOW.md`).

## 2026-08-31 — Project scaffolding
- Initialized as a `uv`-managed Python 3.12 package (`src/` layout:
  `ingestion/`, `analysis/`, `app/`)
- Runtime deps: `pandas`, `requests`, `streamlit`. Dev deps: `pytest`,
  `pytest-cov`, `ruff`, `pre-commit`
- CI (GitHub Actions): ruff + pytest on every PR to `main`
- Branch strategy: GitHub Flow — see `docs/WORKFLOW.md`
- See `docs/decisions/0001-initial-technical-foundations.md` for the
  reasoning behind these choices

## 2026-09-05 — Full historical backfill (Phase 1 complete)
- `backfill.py`: pulls all 11 tracked NCM codes' full history
  (1997-present) into `raw.exports`, chunked one API call per year. A
  year that fails outright gets a second try after a longer pause
  before being reported as skipped, rather than crashing the whole
  run — `ingest_raw()` only appends, so re-running is always safe.
- `comexstat_client.py`: retry policy tuned to ComexStat's own stated
  429 cooldown (min 10s wait, up to 6 attempts, shared by both
  `_post_general` and `_get_tables`) — the original 2/4/8s backoff was
  shorter than the API's own advertised retry window and got
  exhausted under sustained load during the actual backfill run.
- `data/processed/comexstat.duckdb`: real backfilled data now — 72,724
  rows across `staging.exports`/`marts.exports`, full 1997–2026
  history × all 11 NCM codes.
- Closes out Phase 1 (ingestion) per `docs/ROADMAP.md`; Phase 2
  (analysis) is next.
