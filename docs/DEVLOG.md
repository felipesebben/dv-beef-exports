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
- `ncm_codes.py` (#3): the 11 tracked beef NCM codes, tagged by category
  (`frozen`, `offal`, `salted_dried`, `processed`). Fresh/chilled beef is
  deliberately out of scope (ADR 0002).

## 2026-09-01 — ComexStat client and first loader (#4, #5, #6)
- `comexstat_client.py`: `fetch_exports()` against `POST /general`, with
  `tenacity` retrying only transient failures.
- First live run found what mocked tests couldn't: the API also returns
  undocumented **429 rate limits** ("try again in 10 seconds"), which fell
  into the non-retryable branch. Both 403 and 429 are now transient (#5).
- `duckdb_loader.py`: first version, a single `exports` table in the
  tracked `comexstat.duckdb` — superseded by the medallion layout below.

## 2026-09-03 — Medallion layering and dimension tables (#7, #8, #9, #10)
- ADR 0004: `raw` / `staging` / `marts` as three schemas in the one
  tracked DuckDB file. `raw` is append-only — every pull is kept, and
  `staging` dedups to the latest pull per row.
- `docs/comexstat-api-reference.md`, from the API's real OpenAPI spec
  (the `/docs` page blocks plain fetches; the spec lives at
  `/docs/doc.yaml`).
- `duckdb_loader.py` split into `ingest_raw()` / `build_staging()` /
  `build_marts()`; `marts` adds `metric_ton`.
- Dimension tables from the live `/tables/*` endpoints:
  `dim_ncm_hierarchy`, `dim_country`, `dim_economic_bloc`, and
  `bridge_country_bloc` (a bridge, because a country can be in a region
  *and* a trade bloc). The live responses turned out thinner than the
  sample workbook — no ISO codes, no "Section" level. Refreshed
  explicitly, not on every connection, since they hit the network.

## 2026-09-05 — Full historical backfill (Phase 1 complete) (#11)
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

## 2026-09-06 — Dimensions wired into the fact tables (#12)
- `staging.exports` / `marts.exports` now carry country code, NCM
  chapter/heading/subheading, and separate `region` / `trade_bloc`
  columns (ComexStat's bloc list doesn't say which is which, so the four
  trade blocs are named in `TRADE_BLOC_NAMES`).
- `/general` never returns a country code, so the `dim_country` join is
  by name — verified zero mismatches across all 230 real countries.
- Caught a fan-out bug: joining the bloc bridge twice, without
  aggregating first, doubled or quadrupled rows for countries in both a
  region and a trade bloc (e.g. Argentina). Fixed with a per-country CTE
  and a regression test.

## 2026-09-12 — Opportunity scoring methodology (#13, #14, #15, #16, #17)
- ADR 0005: score = `annual_growth_pct × (1 − share_pct)`, reported
  *separately* from a `confidence` index (geometric mean of history
  coverage, trend fit and volume). Percentages keep products of very
  different size comparable. The design favours steady multi-year growth
  over one-off spikes — leads for manual research, not a ranking by size.
- Phase 2 went straight to opportunity scoring; the simpler
  market-overview metrics in `docs/ROADMAP.md` are skipped for now.
- EDA notebook (`notebooks/opportunity_scoring_eda.ipynb`, frozen tongues
  × all countries) validated the design on real data, and caught that raw
  R² is meaningless at 2 data points (a line through 2 points always fits
  perfectly): Jordan showed ~143,000%/yr growth. ADR amended (#15) to use
  **adjusted R²** and require **`years_active >= 4`**.

## 2026-09-13 — Opportunity scoring module (#18)
- `analysis/opportunity_scoring.py`: `rank_markets()` (fix a product,
  rank geographies) and `rank_products()` (fix a geography, rank
  products) — both thin wrappers over one shared query, parametrized by
  `product_level` / `geo_level`, so the two lenses can't drift apart.
- Unit price is always sum-then-divide (`sum(fob) / sum(tons)`), never an
  average of ratios — averaging over-weights tiny shipments.
- The volume-confidence constant `K` is the median group size over the
  *whole* product × geography grid, not just the filtered rows.

## 2026-09-26 — Streamlit prototype (Phase 3 started) (#19)
- `app/main.py`: sidebar picks the lens, the fixed value, the ranked
  axis and the window; results as a top-15 bar chart plus the full
  ranked table.

## 2026-09-27 — Analysis methodology reference (#20, #21)
- `docs/analysis-methodology.md`: every metric with its question,
  formula, a worked example against the real DB (frozen livers →
  Singapore), how to read it, and where it misleads — plus a "known
  distortions" list that drove the next three PRs.
- `total_kg` → `total_metric_ton`, summed from `marts`' own column so the
  unit conversion stays in the marts layer (#21).

## 2026-10-03 — Scoring fixes from the methodology review (#22, #23, #24)
- **Rolling 12-month periods** (#22): the latest calendar year is almost
  always partial (2026 = Jan–Aug), and scoring it as a full year read as
  a one-third collapse for every market. "Year" now means a trailing
  12-month block ending at the latest month in the data. Chosen over
  dropping the partial year, which lost the newest markets, and over
  annualising it, which invents data. Guyana's trend fit went 0.17 → 0.69.
- **Leading-tiny-period trim** (#23): Bahrain ranked #1 at +379%/yr off a
  $42 first period. Leading periods under 1% of the group's median are now
  dropped before the fit — leading-only, so a recent collapse is never
  hidden. Four options were tested against the full grid first; a floor
  on every period was rejected because it hid collapses (Thailand went
  −26% → +20%). Recorded in ADR 0005.
- **Brazil excluded as a destination** (#24): ComexStat lists Brazil
  itself for ~$204k of re-imports. Dropped in `marts` (code `'105'`),
  kept in `staging`; `IS DISTINCT FROM` so rows with no country code
  aren't lost too. Raised `K` from $49,736 to $52,119.

## 2026-10-03 — "Explain the numbers" page (#25)
- The app is now two pages sharing one sidebar query: **Opportunities**
  (the ranking, with column tooltips from `METRIC_GLOSSARY`) and
  **Explain the numbers**.
- Explain page: for any result, a bottom-line verdict plus every metric
  explained for a business reader — what it means, what it says for this
  selection with real numbers, and what to do with it — and a chart of
  each 12-month block against the fitted trend.
- Wording lives in `app/explanations.py` as tested pure functions.
  Reading the output for real cases caught a contradictory verdict for
  high-share markets (Egypt) and a missing flag for trades with no sales
  in the latest 12 months (Vietnam's top product).
- A test keeps `METRIC_GLOSSARY` word-for-word in sync with the
  methodology doc's quick-reference table.
- Fixed the results table's "Total tons" column, broken since #21 by a
  stale `total_kg` label key.

## 2026-10-03 — Market overview (Phase 2 complete)
- `analysis/market_overview.py`: the descriptive metrics skipped earlier —
  `market_trend()` (value, tons, $/t, destinations and YoY per 12-month
  period) and `top_destinations_matrix()` (top destinations × products,
  latest 12 months). Same rolling periods as the scores, so figures agree
  across pages.
- New landing page, **Market overview**: a plain-language headline that
  splits value growth into volume vs. price (the last 12 months: value
  +31% = volume +14% and price +15%; processed beef's +2% was *all*
  price, with volume down 9%), four KPIs, a trend chart, and a
  who-buys-what heatmap that warns when one destination dominates
  (China: 53% of everything). The heatmap follows the trend chart's
  value/volume/price picker, and each product column is coloured on its
  own scale, so the darkest cell is that product's top buyer — a single
  scale across products of very different size only showed that frozen
  cuts are big.
- One shared chart style (`common.style_chart()`) across every page:
  hairline grid on the value axis only, no ticks, muted axis text, axis
  titles at the tip of the axis — so the ink goes to the data. The trend
  chart highlights and labels only its highest and lowest periods.
- The overview has its own pickers, so the ranking sidebar is hidden there;
  `persist_state="session"` keeps the sidebar query intact across the
  visit (Streamlit otherwise resets widgets that aren't rendered).


## 2026-10-03 — Phase 4 researched, moved to backlog
- A free-sources-only survey of where importing companies can be named,
  for the ten top-scoring markets:
  `docs/research/beef-importer-free-data-sources.md` (cited, each claim
  marked verified / snippet / inferred).
- Finding: only Canada names importers for free; market share is fully
  automatable via UN Comtrade. Phase 4 is parked on the backlog with an
  ordered task list in `docs/ROADMAP.md`.
- Two findings outlive Phase 4 and are recorded there: the repo is public,
  so Comtrade's internal-use licence keeps raw rows out of git; and
  ComexStat records declared destinations, which makes Turkey's
  opportunity score suspect (Brazil reports 24x more frozen boneless beef
  to Turkey than Turkey reports receiving).

## 2026-10-03 — Automated monthly refresh (Phase 1's last piece)
- `ingestion/refresh.py` + `.github/workflows/refresh.yml`: weekly check
  for a newly published month; re-pulls the previous year through the
  newest month, gates the result, and opens a PR with the refreshed
  DuckDB file (needs the `REFRESH_PAT` secret - see ADR 0003).
- Testing the plan live overturned three assumptions:
  - **ComexStat revises published months and can delete rows** (June
    2026 lost a row three months on). Staging's "most recent pull per
    key" kept the deleted row's stale copy forever - the tracked DB
    carried one phantom $985 row. Staging now treats the newest pull
    *covering* a month as authoritative, via a new `raw.pulls` log.
  - **Pulls are ordered by `pull_seq`, not `fetched_at`** - two pulls in
    the same clock tick share a timestamp (caught by a test that only
    failed under PowerShell).
  - **MDIC's totals file can't gate our scope** (all-product yearly
    totals, broken TLS chain). The gate instead checks our per-country
    rows against ComexStat's own per-code totals - zero mismatches live.
- ADRs 0003 and 0004 amended accordingly.

## 2026-10-03 — Deployable to Streamlit Community Cloud
- `docs/DEPLOY.md`: free deploy from `main`, restricted to invited viewers.
- `DB_PATH` is now anchored to the code's location instead of the working
  directory (a relative path would silently create an empty database
  wherever the app happened to start), and `main.py` puts `src/` on the
  import path since Community Cloud isn't documented to install the project.
  Verified in a fresh environment without the package, from another folder.

## 2026-10-05 — Frontend UX review
- Reviewed all three pages at phone and desktop width (headless Chrome
  screenshots), for a business reader on a phone.
- **Who-buys-what is now an HTML table** (`st.html`): it scrolls sideways
  on a phone with the destination column pinned - the Altair heatmap
  squeezed 11 columns into ~390px. Per-column shading kept; details on
  hover / long-press.
- **Sidebar query modernised**: one searchable picker per side (the same
  options as the overview) instead of a level dropdown then a value
  dropdown; plain labels ("Best markets / Best products", "Compare"); the
  history window and minimum years moved under "Advanced". Each ranking
  page shows the current query in one line, since the sidebar hides behind
  the menu on phones.
- **Products shown by name everywhere** - ranking products used to show
  bare NCM codes in the Opportunities chart and table.
- Plain-language table columns matching the Explain page ("Trend
  steadiness", not "Trend fit (adj. R²)"); confidence as a bar; key
  columns first, name pinned. Formula and file paths removed from the UI.
- Charts: compact money axes ("$20B"), short period labels ("Sep '25–Aug
  '26"). Explain cards lead with the selection and the action; the generic
  definition sits behind "What this measures".
- Light theme forced (`.streamlit/config.toml`) - the styling assumes it.

