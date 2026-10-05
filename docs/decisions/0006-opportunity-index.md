# 0006 — Opportunity index replaces growth × (1 − share)

## Context
ADR 0005 scored opportunities as `annual_growth_pct × (1 − share_pct)`,
with `confidence` reported alongside but never affecting the rank. Used in
the app, that ranking turned out to answer a narrower question than the
business asks. On the default view (all beef → countries, data through Aug
2026):

- **The score was effectively the growth rate.** Share is under 3% for
  every country except China, so `(1 − share)` barely moved anything.
- **Size played no part.** Vanuatu ranked #5 on $14k a year (3 t),
  Bulgaria #9 on $3.5k (0.7 t), Palau #12 on $6.8k. Frozen livers ranked
  Guyana #2 on 26.8 t over the whole 10-year window.
- **Evidence played no part.** Bangladesh ranked #7 with 0% confidence.
- **The biggest markets sank** - the US #39, China #77 - while a saturated
  market like Egypt (61% of Brazil's frozen livers) was never discounted
  for being saturated once its growth was high.

The company is small; it wants leads that are **growing steadily, recently,
in commercially real volumes, with room left and prices that hold up** -
not the fastest-growing rounding errors (see ADR 0005's "consistent,
noticeable evidence" framing, which this sharpens).

## Decision
`opportunity_score` (column name kept) becomes a 0-100 **opportunity
index**:

    opportunity_score = 100 × attractiveness × headroom × materiality × evidence

- **attractiveness** (0-1) - weighted percentile ranks *within the ranked
  set*, so products of very different scale stay comparable (0005's
  fairness principle) and no single extreme value dominates:
  - 30% long-run momentum: `annual_growth_pct` (0005's log-linear trend);
  - 30% recent momentum: 0.6 × `recent_growth_pct` (log-linear growth over
    the last 4 periods) + 0.4 × `recent_consistency` (share of the last 3
    year-on-year changes that were up; a period with no sales is a "down")
    - this is what rewards growth that is recent *and* constant;
  - 20% size: log of value per active year (diminishing returns: bigger
    helps, but China's scale doesn't win by itself);
  - 20% price trend: `price_trend_pct`, log-linear growth of $/t over the
    fitted periods - a market paying more over time is worth more.
  Missing components (fewer than 3 points) sit at the neutral 0.5.
- **headroom** = `1 − share_pct`, as a **multiplier** rather than one more
  weighted term: a market's attractiveness only counts for the part of it
  not already Brazil's. A weighted term let size outvote saturation (Egypt
  stayed #1 for livers in testing).
- **materiality** = `tons_per_year / (tons_per_year + 25)`. 25 t is about
  one 40-ft reefer container of frozen beef: one container a year scores
  0.5, ten about 0.9, a 3-t-a-year market about 0.1. An *absolute* anchor,
  in tons so it means the same across products and price levels - percentile
  size alone can't filter tiny markets, because in a niche product almost
  every market is small.
- **evidence** = `0.5 + 0.5 × confidence`: 0005's confidence now acts on
  the rank, halving it at zero evidence rather than vetoing.

Constants live in `analysis/opportunity_scoring.py` (`INDEX_WEIGHTS`,
`RECENT_PERIODS`, `CONTAINER_TONS`). Components are output columns
(`recent_growth_pct`, `recent_consistency`, `price_trend_pct`,
`tons_per_year`, `materiality`, `attractiveness`) so every factor of a
rank is visible - the Explain page breaks the index into its four factors
and names the weakest.

## Evidence (real data, checked 2026-10-05)
- All beef → countries: Mexico #1, Philippines #2, then Congo, Côte
  d'Ivoire, Turkmenistan… all material and steady. US #39 → #21. China
  #77 → #91 (biggest, but 53% already Brazil's). Vanuatu / Bulgaria /
  Palau → #150-158.
- Frozen livers: Libya, UAE, Saudi Arabia lead; Egypt (61% share) #8;
  Guyana (26.8 t in 10 years) #2 → #13.
- **Robust to the choices made:** top-10 overlap with the chosen design is
  8-10 of 10 under equal weights, size-heavy weights, or a 12.5 t / 50 t
  anchor, and the #1 never changes. The exception is dropping price trend
  (5-6 of 10 change) - kept deliberately, as rising prices are real
  information. The old score shared only 1-4 of these top 10s.

## Alternatives considered
- **Keep growth × (1 − share), add a size filter** - a hard cut-off makes
  a cliff (a market just under it vanishes) and still ignores recency,
  price and evidence.
- **All factors as one weighted sum** - let size outvote saturation
  (Egypt #1 for livers) and evidence never truly bit.
- **Absolute (not percentile) growth inputs** - one +300%/yr outlier
  flattens everyone else; percentiles keep the index scale-free, at the
  cost of being relative to the ranked set (below).

## Consequences
- **The index is relative to what's ranked**: the same market can score
  differently in a different query. It's a ranking key, not a forecast.
- **Four multiplied factors compress the scale** - read the factors, not
  the index's absolute value; the Explain page shows them.
- `recent_consistency` rests on only 3 changes, so it moves in steps of
  1/3. A rising $/t can be a mix shift toward pricier cuts rather than
  price rises.
- Unchanged: Brazil-side data only, so destination-side issues (e.g.
  Turkey's suspect transit flows, docs/ROADMAP.md backlog) still apply.
- The working reference is `docs/analysis-methodology.md`.

## Status
Accepted, 2026-10-05. Supersedes ADR 0005's score formula; 0005's
confidence definition stands and now feeds the index's evidence factor.
