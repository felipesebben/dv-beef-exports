# 0004 — Medallion data layering (raw / staging / marts)

## Context
The current shape (`duckdb_loader.py`) goes straight from the API response
to one finished `exports` fact table in a single step — casting, dedup, and
any future business logic (unit conversion, cross-category comparability)
would all have to live in that same step. That's fine for the one slice
pulled so far, but two things ahead make it costly:

- The full backfill (all 11 NCM codes × 1997–present) is a real amount of
  API traffic against an endpoint we've already confirmed rate-limits
  (429) and soft-403s. Re-running it every time a transformation rule
  changes is expensive and slow.
- Phase 2 analysis needs unit conversions (kg → metric ton) and has to
  handle categories that aren't directly comparable by weight — e.g.
  canned/processed beef (NCM 16025000) likely reports net weight
  including non-meat mass (brine, sauce), unlike a frozen boneless cut.
  That kind of judgment call will get revised as real analysis surfaces
  problems with it, and shouldn't require a fresh API pull each time.

## Decisions

**Medallion layering (raw / staging / marts), as schemas inside the one
tracked `data/processed/comexstat.duckdb` file** — not separate files or
environments. This keeps `0003`'s "one portable, git-tracked file"
reasoning intact while separating what used to be one conflated step:

- **`raw`** — API responses captured close to as-returned (minimal to no
  casting), one row per pull, tagged with what was requested (`ncm_codes`,
  `period_from`/`period_to`, `details`) and `fetched_at`. This is the
  layer every downstream layer can be rebuilt from without going back to
  the API.
- **`staging`** — typed and cleaned, derived from `raw`: casting,
  deduplication, joining the NCM reference table. Roughly what today's
  `exports` table already is. Answers "is this correct data," not "what
  does it mean."
- **`marts`** — where business judgment lives: unit conversion (kg → metric
  ton), and anything addressing cross-category comparability. Rebuilt from
  `staging` whenever a rule changes — never needs a new API call.

**Raw pulls become durable and git-tracked**, via the `raw` schema inside
the already-tracked file. This reverses part of `0003`, which called
`data/raw/` disposable scratch backed by nothing durable. `data/raw/` can
stay around for genuinely throwaway debugging dumps, but the `raw` schema
— not that directory — is now the backstop that makes "never re-pull for a
logic change" actually true.

**`raw` is append-only, and refreshes pull incrementally, not from
scratch.** A pull never overwrites or deletes existing `raw` rows, even
for a period already stored — every pull adds new rows tagged with their
own `fetched_at`, so `raw` is a growing log of every API response ever
received, not a snapshot of current truth. `staging` earns that job by
picking the most recent pull per (`ncm_code`, `country`, `year`, `month`)
when it dedupes.

Since ComexStat data updates on a monthly cadence, routine refreshes
don't re-pull the full 1997-present history. Instead: find the latest
(`year`, `month`) already present in `raw` for the requested
`ncm_codes`, and pull forward from there. To guard against ComexStat
revising a month's figures after first publishing them — not yet
confirmed either way — the pull re-includes that latest stored month
rather than starting strictly after it, at the cost of one extra month
of API traffic per refresh. If a revision did happen, the re-pull
appends a newer `raw` row for that month and `staging`'s dedup picks it
up automatically; if it didn't, the extra row is harmless. Whether
ComexStat actually revises published months, and how far back, is worth
checking once real data is flowing — this overlap is a cheap default,
not a verified answer.

**Weight normalization across categories is an open question, not an
invented answer.** Rather than guess a conversion factor to make canned
and frozen product weights blend cleanly, `marts` keeps weight broken out
by category so Phase 2 analysis can decide whether/how to combine them.

## Consequence for existing code
`duckdb_loader.py`'s `load_exports()` currently collapses raw → staging in
one step. It needs to split into a raw-ingest function and a separate
staging-transform function, plus a new marts-build step. This is real
rework, worth doing as its own PR *before* the full historical backfill —
so the backfill lands directly in the new shape once, not once now and
again after a later refactor.

## Dimension tables — noted here, design deferred
`?language=en` on `/general` gives human-readable country/NCM strings
embedded directly in fact rows, which `0002` treated as enough ("no
separate lookup table needed") — true for display, not true for stable
dimensional modeling. No stable keys to join on, no country
region/economic-bloc grouping, no NCM hierarchy (chapter → heading →
subheading, not just the leaf description). ComexStat exposes proper
dimension data for this under `/tables/*` (per `0002`'s research) and as
bulk downloads. These belong in `staging`/`marts` as real dimension
tables — sourced and refreshed independently of the fact pulls, since
dimensions change far less often than trade data does. Schema/table design
for this is deferred to when `duckdb_loader.py` actually gets restructured
per this ADR, not decided yet.

## Status
Accepted, 2026-09-01.
