# Roadmap

The goal: give a small beef-export trading company market intelligence they
can't otherwise afford, so they can identify promising buyer countries
instead of relying on visiting trade shows and cold outreach.

ComexStat gives us country/product-level aggregate trade flows. It answers
"what is Brazil selling, and to where" — it does **not** contain company
names. Phases 0–3 build the market-intelligence layer on ComexStat. Phase 4
is a separate problem (finding actual importer companies) that this data
can point us toward but not solve directly.

## Phase 0 — Foundations
- Repo, tooling, CI, docs scaffolding
- Confirm ComexStat's actual API/bulk-data shape and the NCM codes that mean
  "beef" (see `docs/decisions/`)

## Phase 1 — Ingestion
- `POST /general` client against `api-comexstat.mdic.gov.br` (no auth),
  filtered to our beef NCM codes, full history (1997–present)
- Load into a DuckDB file (`data/processed/comexstat.duckdb`), tracked in
  git — see `docs/decisions/0003-storage-and-automation-strategy.md`
- Once the client + DuckDB loader exist: a scheduled, reconciliation-gated
  refresh so this keeps running without manual intervention (same ADR)
- See `docs/decisions/0002-comexstat-data-access-strategy.md` for why
  API-first over the bulk CSV files, and the exact NCM code scope

## Phase 2 — Analysis
- Metrics answering the core questions:
  - Market overview (volume/value trends, YoY growth)
  - Top products × top destinations
  - Outlier detection: countries with strong growth but low absolute share
    (i.e. underserved, not just the obvious big buyers)
  - Country opportunity scoring beyond the obvious markets

## Phase 3 — Prototype
- Streamlit dashboard surfacing the Phase 2 metrics for internal use
  (`app/`: market overview, opportunity ranking, and a plain-language
  "explain the numbers" page)

## Phase 4 — Client discovery (backlog)
- Separate research effort: identifying actual importer companies within
  the countries Phase 2 flags as promising. Needs different data sources
  (customs-transparency countries, trade directories, manual research)
  since ComexStat has no company-level data.
- **Status: researched, then moved to the backlog (2026-10-03).** A
  free-sources-only survey of the ten top-scoring markets is in
  `docs/research/beef-importer-free-data-sources.md`. Short version:
  - Only **Canada** publishes named importers for free (ISED Canadian
    Importers Database, open licence). US, Singapore, Paraguay and Turkey
    are partial; Mexico, Ghana, Guyana and Libya have no free source.
  - **UN Comtrade** (free API) gives each destination's total imports by
    supplier for all ten — Brazil's share vs competitors — with Libya via
    partner-reported (mirror) data.
- Backlog items, roughly in order, when this is picked up:
  1. **Comtrade share layer** (`raw` / `staging` / `marts`, like ComexStat).
     The repo is public and Comtrade's licence is internal-use only, so raw
     rows go in a gitignored DuckDB file and only derived marts (Brazil's
     share, rank) are committed — record that in an ADR first.
  2. **Destination-side cross-check** of ComexStat. Brazil reported
     US$124M of frozen boneless beef to Turkey in 2024; Turkey reports
     US$5.25M from Brazil. ComexStat records the *declared* destination, so
     transit and free-zone flows can inflate a market's opportunity score.
     Until this is checked, treat Turkey's score as suspect.
  3. Importer prototypes where free data is good: **Canada** (bulk
     download), **US** (FSIS data + manual manifest lookups), **Singapore**
     (company-register download + manual qualification), plus free formal
     information requests in **Paraguay**.
  4. A data-handling ADR: company-level records can be committed; named
     people's contact details never enter git (LGPD).
- Mexico is the biggest gap — the fastest-growing market, with no free
  importer names — and the one place a paid data source may be worth
  pricing.

## Phase 5 — Productionize
- Replace the Streamlit prototype with a proper app (API backend + richer
  frontend, e.g. D3.js), integrated into the company's own platform
