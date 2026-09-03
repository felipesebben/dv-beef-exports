# ComexStat API reference

Confirmed endpoints, params, and gotchas from the actual OpenAPI spec —
`https://api-comexstat.mdic.gov.br/docs/doc.yaml`, linked from the
interactive docs at `/docs` (a Stoplight Elements page: the spec URL is
in `<elements-api apiDescriptionUrl="...">` in that page's HTML). Not
duplicated in `docs/decisions/0002-comexstat-data-access-strategy.md` —
that ADR records the *decision* (API-first); this doc records the
endpoint *facts* behind it, confirmed 2026-09-03.

**Fetching the spec/docs yourself**: a plain `curl` (or any fetch without
browser-like headers) to `/docs` gets hard-blocked by Cloudflare — not
the soft 403/429 `comexstat_client.py` already retries around, a real
block page. Needs a real `User-Agent` (and ideally `Accept`/
`Accept-Language`) to get through. `/docs/doc.yaml` itself fetches fine
once you're past that.

**Spec caveat**: every endpoint below documents its request params, but
none document a response body schema or example (all just say "200
OK"). Field names in actual responses still need confirming against a
live call — this doc removes guessing on *inputs*, not response shape.

## `/general/*` — the endpoint we use

- **`POST /general`** — what `comexstat_client.fetch_exports()` already
  calls. Full param set per the spec (only `flow`/`period`/`filters`/
  `details`/`metrics` are used today):
  - `flow`: `"export"` or `"import"`
  - `monthDetail`: bool
  - `period.from`/`period.to`: `"YYYY-MM"`
  - `filters`: `[{filter, values}]` — **`country` values are numeric
    codes** (e.g. `104`, `107`), not names. This confirms the API can
    filter/group countries by code — resolves the open question from
    the medallion-layering design discussion about whether a country
    code is available at all (it is, as a filter input at least; whether
    a `/general` response with `details: ["country"]` also *returns* a
    code alongside the name is still unconfirmed — no response example
    in the spec).
  - `details`: e.g. `["country", "state", "ncm"]` — `"state"` here is
    the Brazilian exporting state (UF), not a foreign country subdivision.
  - `metrics`: `metricFOB`, `metricKG`, `metricStatistic`,
    `metricFreight`, `metricInsurance`, `metricCIF` — the last three are
    **import-only** (a documented 400 error names this explicitly:
    `"metricFreight, metricInsurance e metricCIF só são permitidos para
    fluxo (flow) de importação"`).
  - Documented 400s: invalid filter, invalid detail item, invalid
    metric, invalid metric for flow, invalid period, invalid flow.
- **`GET /general/dates/updated`** — no params. "Obtain the date of the
  last update available for query... monthly export/import data going
  back to 1997." This is the endpoint `docs/decisions/0003-storage-and-
  automation-strategy.md` names for gating the scheduled refresh (poll
  this, exit if nothing new). Response shape unconfirmed (see caveat
  above) — worth one live call when actually building that automation
  step, not worth guessing at now.
- **`GET /general/dates/years`** — no params. Earliest/latest year
  available for query.

## `/tables/*` — dimension data, live from the API

Confirms `docs/decisions/0004-medallion-data-layering.md`'s deferred
dimension tables can be sourced live from the API itself, not only from
the manually-uploaded snapshot in `data/samples/dimensions/` (bulk
Excel/CSV exports of the same underlying data):

- **`GET /tables/countries`** — country code + description table.
  `search` param (substring match, e.g. `"br"`).
- **`GET /tables/countries/{id}`** — single country by code.
- **`GET /tables/economic-blocks`** — bloc code + description.
  `add=country` returns member countries inline; `language`; `search`.
- **`GET /tables/ncm`** — NCM code + description. Paginated
  (`page`/`perPage`); `search`; `add=sh|cuci|cgce` joins in the
  Harmonized System / CUCI / CGCE classification for each NCM.
- **`GET /tables/ncm/{coNcm}`** — single NCM by code.
- **`GET /tables/hs`** — Harmonized System (SH) table — the chapter/
  heading/subheading hierarchy. Paginated; `language`; `add=ncm` joins
  NCM codes under each SH entry.

Not currently used, listed for completeness: `/cities/*` and
`/historical-data/*` mirror `/general/*`'s shape (dates/updated,
dates/years, filters, details, metrics, main query) at city-level and
for a longer historical series respectively — neither is part of this
project's scope. `/tables/uf`, `/tables/cities`, `/tables/ways`
(transport route), `/tables/urf` (customs office), `/tables/nbm`
(pre-1997 code system) also exist, matching the Brazil-side dimensions
noted from the sample workbook — not needed unless `details` ever
widens beyond `["country", "ncm"]`.
