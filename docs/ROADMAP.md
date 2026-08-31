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
- Local caching of raw pulls so we're not re-hitting the source on every run
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

## Phase 4 — Client discovery
- Separate research effort: identifying actual importer companies within
  the countries Phase 2 flags as promising. Needs different data sources
  (customs-transparency countries, trade directories, manual research)
  since ComexStat has no company-level data.

## Phase 5 — Productionize
- Replace the Streamlit prototype with a proper app (API backend + richer
  frontend, e.g. D3.js), integrated into the company's own platform
