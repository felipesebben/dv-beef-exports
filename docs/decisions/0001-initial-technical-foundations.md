# 0001 — Initial technical foundations

## Context
Building an internal market-intelligence tool for a Brazilian beef export
trading company, on a near-zero budget, as a two-person coworking/learning
project. Need something we can iterate on quickly now and evolve into a
production tool integrated with the company's own platform later.

## Decisions

**Data source: ComexStat.** Brazil's official foreign trade statistics
(MDIC). Public, free, high historical granularity (by NCM product code,
destination country, month, value, weight), has both an API and bulk data
downloads. No cost, no rate-limit budget to manage against a paid vendor.
It gives country/product-level aggregates only — no company-level data —
so client/importer discovery (Phase 4) will need a different source.

**Language/stack: Python + uv.** Best fit for the data-wrangling work
(pandas ecosystem) and for Streamlit, our chosen prototyping UI. `uv` over
`poetry`/plain `pip+venv` for being a single fast tool covering venv,
dependency resolution, and locking — one less thing to context-switch on
while learning.

**Prototype UI: Streamlit.** Fastest path from "analysis function" to
"something a non-technical stakeholder can click through," with no
frontend build step. Explicitly a prototype — Phase 5 replaces it with a
proper app (richer frontend, e.g. D3.js) once the underlying analysis is
validated and worth productionizing.

**Testing: pytest.** Standard choice, no reason to deviate.

**Lint/format: ruff.** Replaces the black+flake8+isort combo with one fast
tool.

**Branching: GitHub Flow**, not GitFlow — a two-person project doesn't
need release branches; short-lived feature branches + PRs into `main` is
enough structure without the ceremony.

## Status
Accepted, 2026-08-31.
