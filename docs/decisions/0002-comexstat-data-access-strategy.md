# 0002 — ComexStat data access strategy

## Context
Needed to confirm ComexStat's actual API/bulk-data shape before designing
the ingestion layer (Phase 1). Researched by fetching the live OpenAPI
spec and running real queries against production, not by trusting
third-party writeups.

## Findings

**API**: `https://api-comexstat.mdic.gov.br`, documented at `/docs`. No
authentication. Main query endpoint is `POST /general` (body: `flow`,
`period.from`/`to`, `filters` e.g. NCM/country, `details` for grouping,
`metrics` for FOB/kg). Reference data (NCM table, country table, etc.) is
under `/tables/*`. No documented numeric rate limit, but it sits behind
Cloudflare/a WAF and returns occasional unexplained `403`s — the client
needs retry-with-backoff, not just a happy-path request.

**Bulk CSVs**: yearly files at `balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/EXP_{YEAR}.csv`,
~100MB/year, semicolon-delimited. Text encoding could not be confirmed
from HTTP headers — detect it at read time rather than assume UTF-8.

**History**: monthly data from 1997 to the present month (published ~first
week of the following month). Pre-1997 uses a discontinued code system
(NBM) — not worth bridging for a current-market-intelligence tool.

**NCM codes for beef**: confirmed against the live `/tables/ncm` endpoint.

## Decisions

**API-first, not bulk CSVs, for Phase 1.** Our product scope is a
narrow slice (~15-20 NCM codes) of Brazil's total trade, not the full
dataset. A `POST /general` query filtered to just our NCM codes across
1997–2026 should return a manageable result directly — no need for the
~100MB/year full-detail bulk files, and no need to solve the CSV encoding
question yet. Fall back to bulk CSVs only if the API can't handle the
full range in a small number of chunked (e.g. per-year) requests.

**Product scope: meat only.** NCM chapters/codes: fresh/chilled (0201.x),
frozen (0202.x — 02023000, boneless frozen, is Brazil's single largest
beef export line by value), edible offal (0206.x), salted/dried/smoked
(02102000), processed/preserved incl. corned beef (16025000). Excludes
live cattle (0102.x) and hides/leather (chapter 41) — different
commodities from a meat-trading business. The concrete code list lives in
`src/dv_beef_exports/ingestion/ncm_codes.py`, not duplicated here.

**Full available history (1997–present), not a shorter window.** Costs
nothing extra given the narrow product scope, and better supports the
trend/outlier questions (e.g. "which countries grew share over 10 years")
that a recent-only window would blunt. The analysis layer can window it
down for specific views.

## Open question — not blocking
No dataset-specific open-data license could be confirmed (only found
gov.br's generic site-wide footer license, not clearly the deliberate
license for this dataset). This data is routinely redistributed
commercially by banks and trade consultancies, so treated as low-risk for
*internal* BI use — but get written confirmation from MDIC/SECEX before
this becomes an external-facing or redistributed product.

## Status
Accepted, 2026-08-31.
