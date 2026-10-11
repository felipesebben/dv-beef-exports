# 0007 — SIGSIF export approvals and exports by state: finding suppliers

## Status
Accepted, 2026-10-11. Widens the 2026-10-03 scope decision ("only
ComexStat") by one Brazilian government source, at Felipe's request.

## Context
The app answers "which markets, which products". The next question a
trading company asks is **"who do we buy it from?"** - which Brazilian
plants can legally ship that product to that market. A colleague asked
whether MAPA's SIGSIF (the federal meat-inspection system) could answer it.

What we found (checked 2026-10-11):

- The legacy public site (`sigsif.agricultura.gov.br/sigsif_cons/...`) is
  off-limits to automation: its `robots.txt` is `Disallow: /` for every
  agent, a WAF drops non-browser clients, and it only looks up one plant at
  a time. The logged-in PGA-SIGSIF platform needs personal credentials.
- MAPA publishes the same data as **open data**: dataset "Plataforma de
  Gestão Agropecuária - PGA - SIGSIF" on `dados.agricultura.gov.br`,
  **CC-BY**, updated **monthly**. Two of its CSVs matter:
  - *Estabelecimentos Nacionais Habilitados* - one row per destination ×
    plant (SIF number) × product approval, with validity, approval and
    suspension dates (37,497 rows; 5,693 bovine);
  - *Estabelecimentos Registrados no SIF* - the plant registry: CNPJ, legal
    and trade name, address, plant class, plus phone/e-mail.
  The portal's `robots.txt` allows the file downloads (with
  `Crawl-Delay: 60`) and disallows `/api/`.
- ComexStat has **no company dimension**, but `/general` accepts
  `details: ["state"]` - the *state of production* ("Product UF") - and
  SIGSIF records each plant's state. That is the only join between the two.
  (A state-level period must stay within one calendar year: a period that
  crosses a year boundary returns nothing.)

## Decision

### Data
- **Download the two open-data CSVs only** - never the legacy site, never
  the portal's `/api/`, never the logged-in platform. One request per file
  per weekly refresh, a project User-Agent.
- **Keep it narrow and public-safe.** Only bovine approvals, and only the
  registry rows of plants holding one. Never store the registry's phone or
  e-mail columns; for a registrant identified by a CPF (a person, not a
  company), store no name or address. The repo is public, and contact
  details are one web search away for anyone who needs them.
- **Snapshot on change.** Each file is current state with no history, so
  the weekly refresh stores a snapshot (`raw.sigsif_snapshots` /
  `raw.sigsif_approvals` / `raw.sigsif_plants`, append-only, VARCHAR) -
  but only when a content hash of the beef rows differs from the last
  one, so the tracked database grows with real changes, not with runs.
  History starts at the first snapshot (2026-10-11); approvals before that
  are not recoverable.
- **Staging** (`sigsif.build_staging`) rebuilds from the latest snapshot:
  - countries mapped to ComexStat codes by accent-insensitive match on
    ComexStat's Portuguese names, plus a reviewed override list; the EU
    list is expanded through ComexStat's own bloc membership and the
    Eurasian Economic Union to its five members; "BRASIL" (the domestic
    list) dropped; an unmatched name stays with a NULL code and is flagged
    in the refresh PR;
  - each product line mapped to our categories (below);
  - a status per approval: *suspended* if a suspension date isn't
    followed by a later approval event, else *expired* if past its
    validity date, else *active*;
  - `staging.sigsif_approval_spans`: first/last snapshot each approval
    was seen in, for "what changed".
- **Exports by state** (`exports_by_state.py`): `raw.exports_by_state` /
  `raw.state_pulls` → `staging` → `marts.exports_by_state`, 2023 onward,
  re-pulled in the same revision window as the main refresh. Kept out of
  `raw.pulls` on purpose: a pull logged there counts as covering its
  months for the per-country table. Gated like the main pull: per state,
  rows must add up exactly to the per-country rows (0 mismatches over
  59,161 rows at backfill).

### Products: approvals are processing types, not tariff codes
SIGSIF approves *kinds* of product, so the mapping is to our categories,
never to NCM codes:

| SIGSIF product line | Our categories |
| --- | --- |
| "em natureza" (raw) | frozen + offal - one approval covers both, and frozen and chilled alike |
| meat "in natura" / frozen, Hilton quota | frozen |
| offal ("miúdos", stomach) | offal |
| "com adição de inibidores", salted | salted_dried |
| cooked, heat-treated, sterilised (canned), meat-based products | processed |
| chilled only, not heat-treated, tallow, gelatine, casings, serum, hydrolysed | none of ours |

So a plant approved for raw beef shows up for frozen livers and for
boneless cuts alike: the list says it *may* ship them, not that it makes
them.

### Linking plants to flows: by state, as a lead
"Mato Grosso ships 15% of Brazil's beef to Mexico, and plant X is in Mato
Grosso" ranks plant X up - it is a lead, not a claim that X ships to
Mexico. ComexStat cannot say which company exported what.

### Markets without a list
SIGSIF lists only the ~55 destinations that approve Brazilian plants one by
one. **A market missing from it is not closed** (Libya, Ghana, Guyana...):
any SIF plant can usually ship against that country's certificate. For
those, the app shows proven exporters - plants on at least 5 of MAPA's
lists for the product - instead of an approved list.

### App
A fourth page, **Find suppliers**: market + product pickers (pre-filled
from the Explain page's "Find suppliers" button), a one-paragraph summary,
the by-state chart, and the plant table (status, export experience, and
links: a web search for the company in its town, and its registered
address on a map). MAPA's attribution is on the page (CC-BY).

## Consequences
- The weekly refresh now always checks SIGSIF, even when ComexStat has no
  new month; a SIGSIF-only change opens its own PR. A SIGSIF failure is
  reported in the PR body and never blocks the trade data.
- The refresh PR branch is now a fixed `data/refresh`, titled by what
  changed ("data: ComexStat through 2026-10 + SIGSIF approvals as of ...").
- The tracked database grows by the state-level rows (~15k a year) and by
  a beef snapshot (~6k approval rows) whenever MAPA's list changes.
- Mapping SIGSIF country names is a maintained list: a new destination
  name shows up as a warning in the refresh PR until it is added.
- Not done (yet): company-level history before 2026-10-11, the plants'
  own export volumes (no public source), and destination-side importer
  data (still ROADMAP Phase 4, backlog).
