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
OK"). Response shapes marked "confirmed" below came from actual live
calls (2026-09-03), not the spec — everything else is still unconfirmed
guessing on shape, even where the params are solid.

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

Sourced live from the API for `staging.dim_ncm_hierarchy`,
`staging.dim_country`, `staging.dim_economic_bloc`, and
`staging.bridge_country_bloc` (`duckdb_loader.refresh_ncm_hierarchy()` /
`refresh_dim_country()`) — **but the live shapes turned out thinner than
the manually-uploaded snapshot in `data/samples/dimensions/`** (no ISO
alpha/numeric codes anywhere, no "Section" level above chapter). That
workbook looks like a separate, richer downloadable product; these
endpoints read more like lookup tables for the ComexStat query UI's
filter dropdowns.

- **`GET /tables/countries`** — `search` param (substring match, e.g.
  `"br"`). **Confirmed response**: `{"data": {"list": [{"id": "160",
  "text": "China"}], "count": 1}}` — just a code + display name, no ISO
  codes. Some entries have leading whitespace in `text` (needs
  stripping). 281 rows total, returned in one call (no pagination
  params on this endpoint).
- **`GET /tables/countries/{id}`** — single country by code, same thin
  shape.
- **`GET /tables/economic-blocks`** — `language`, `search`.
  **Confirmed** (no `add`): `{"id": "111", "text": "Southern Common
  Market (MERCOSUL)"}` per row, 12 blocs total, one call.
  **Confirmed** (`add=country`): flattened membership rows —
  `{"economicBlock": ..., "country": ..., "coBlock": ..., "coCountry":
  ...}`, one row per (bloc, member country) pair, 322 rows total in one
  call. **A country can belong to more than one bloc** (e.g. Argentina
  appears under both "South America" and "Southern Common Market
  (MERCOSUL)") — this is a many-to-many bridge, not a lookup.
- **`GET /tables/ncm`** — paginated (`page`/`perPage`), `search`,
  `add=sh|cuci|cgce`. **Confirmed** (`add=sh`, `search=<code>`):
  `{"coNcm": ..., "noNCM": ..., "unit": "KILOGRAM", "subHeadingCode":
  ..., "subHeading": ..., "headingCode": ..., "heading": ...,
  "chapterCode": ..., "chapter": ...}` — the NCM's full SH6/SH4/chapter
  hierarchy plus its statistical unit, in one call per code. `search`
  matched cleanly on an exact code in testing, but is documented as a
  substring match — `duckdb_loader.refresh_ncm_hierarchy()` filters
  results to an exact `coNcm` match rather than trusting the first hit.
- **`GET /tables/ncm/{coNcm}`** — single NCM by code. **Confirmed**:
  thin — just `{"id": ..., "text": ...}`, no hierarchy at all. Use
  `/tables/ncm?add=sh&search=<code>` instead (above) if the hierarchy is
  needed, which is why this project does.
- **`GET /tables/hs`** — Harmonized System (SH) table, paginated
  (6620 rows total), `add=ncm`. **Not used** — redundant for our scope,
  since `/tables/ncm?add=sh` already gives the hierarchy per NCM code we
  actually track, one call each, no pagination needed.

Not currently used, listed for completeness: `/cities/*` and
`/historical-data/*` mirror `/general/*`'s shape (dates/updated,
dates/years, filters, details, metrics, main query) at city-level and
for a longer historical series respectively — neither is part of this
project's scope. `/tables/uf`, `/tables/cities`, `/tables/ways`
(transport route), `/tables/urf` (customs office), `/tables/nbm`
(pre-1997 code system) also exist, matching the Brazil-side dimensions
noted from the sample workbook — not needed unless `details` ever
widens beyond `["country", "ncm"]`.
