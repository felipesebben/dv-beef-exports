# 0003 — Storage layer and long-run automation strategy

## Context
Two questions raised beyond the NCM/API scope of ADR 0002: what do we
query/store the pulled data in, and how does this keep running without
someone remembering to press a button every month.

## Decisions

**Storage/query layer: DuckDB.** A single embedded `.duckdb` file holds
both fact data (pulled from ComexStat) and dimension/reference tables
(NCM codes, countries, economic blocs). Reasons:
- Prior experience on the team with DuckDB on data of this shape
- No server to run or pay for — fits the budget constraint that shaped
  this whole project
- SQL window functions (rank, YoY growth, moving averages) map directly
  onto the Phase 2 questions ("which countries are outliers") better than
  hand-rolled pandas
- One portable file the Streamlit app (Phase 3) can query directly, and
  that ships trivially with the repo when deployed — no separate database
  service to stand up
- Reads/writes pandas DataFrames natively when that's more convenient

Given this, unlike raw intermediate pulls, **the DuckDB file itself is
tracked in git** (`data/processed/comexstat.duckdb`) — it's small (narrow
NCM scope, see ADR 0002) and versioning it via git commits doubles as our
change history/backup with no extra infrastructure. `data/raw/` stays
gitignored — raw API responses are regenerable scratch, not the asset.

**Automation: scheduled pull, reconciliation-gated, PR-based initially.**
ComexStat updates monthly, first week of the following month (confirmed
via `/general/dates/updated`). Plan:
1. A scheduled GitHub Actions workflow (weekly — cheap, and doesn't
   require guessing MDIC's exact release day) polls `/general/dates/updated`
   and exits immediately if nothing new has been published.
2. If there's new data, it re-pulls, refreshes the DuckDB file, and
   checks the result against MDIC's own published totals
   (`EXP_TOTAIS_CONFERENCIA.csv`, found during the ComexStat research) as
   an automated data-quality gate — this is what makes it trustworthy
   enough to run unattended, not just scheduled.
3. It opens a PR with the refreshed file (branch protection already
   requires CI to pass before merge). **Start with manual review of these
   PRs** for the first several monthly cycles so we can eyeball the
   numbers; move to `gh pr merge --auto` only once the pipeline has
   proven itself.

This is Phase 1's last piece, built after the ingestion client and DuckDB
loader exist — not before.

## Amendment (2026-10-03): the automation as built
Building the refresh (`ingestion/refresh.py`, `.github/workflows/refresh.yml`)
tested this ADR's assumptions live, and two didn't hold.

**`EXP_TOTAIS_CONFERENCIA.csv` can't be the quality gate.** It holds one
row per *year* with totals for *all* of Brazil's exports - every product
($348B in 2025) - and is a checksum for MDIC's bulk CSV files, not a check
on our 11 beef NCM codes. Its host (`balanca.economia.gov.br`) also serves an
incomplete TLS certificate chain, so a verifying client refuses it. Replaced
by a check against the API itself: for every (NCM code, month) re-pulled,
our per-country rows must add up exactly to ComexStat's own per-code total,
fetched by a second, coarser `/general` query (`details=["ncm"]`). Verified
live: zero mismatches across 2025-2026. A mismatch means a truncated or
partial pull and fails the run (no PR). Two softer signals go into the PR
body instead: a new month far outside the previous 12 months' range
(0.4x-2.5x the median) is flagged, and every revised month is listed.

**`/general/dates/updated` confirmed** (live, 2026-10-03):
`{"data": {"updated": "2026-09-04", "year": "2026", "monthNumber": "08"}}` -
the newest published month and its publication date.

**PRs need a fine-grained token, not `GITHUB_TOKEN`.** Pushes and PRs made
with the built-in token deliberately don't trigger other workflows, so CI
would never run on the refresh PR and branch protection would block it. The
workflow uses a repository secret `REFRESH_PAT`: a fine-grained personal
access token scoped to this repo, with Contents and Pull requests
read/write. Free (as are Actions minutes on a public repo); needs renewing
when it expires.

The rest stands: weekly schedule (Mondays 12:00 UTC) plus manual dispatch
with an optional `force`, exit early when nothing new is published, and
manual review of the refresh PRs before considering auto-merge.

## Status
Accepted, 2026-08-31. Amended 2026-10-03 (gate, token; above).

See `docs/comexstat-api-reference.md` for the confirmed `/general/dates/updated` params (none) and caveats (response body shape isn't documented in the spec — confirm with a live call before wiring the automation step).
