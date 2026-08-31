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
