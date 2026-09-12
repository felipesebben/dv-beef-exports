# 0005 — Opportunity scoring and confidence methodology

## Context
Phase 2 analysis needs to answer two symmetric questions, both parametrized
over `product_level` (`ncm_code | category | overall`) and `geo_level`
(`country | region | trade_bloc | overall`):

1. **Product → markets**: for this product, which markets are most
   promising, and how confident can we be about this?
2. **Country → products**: for this country, which products are most
   promising, and how confident can we be about this?

These are the same underlying scored/ranked data, just pivoted on which
axis is fixed vs. ranked. Both need an opportunity score *and* a confidence
signal reported alongside it — not fused into one number — because the
target user (a small trading company) wants actionable leads for manual
follow-up research, not a black-box ranking. Per Felipe's framing: "I am in
a small company, so large volumes is not what I look for. I look for
consistent, noticeable evidence that can support my research later on." —
i.e. favor stable multi-year growth over one-off spikes or sheer absolute
size.

Constraints already settled that this ADR builds on:
- DuckDB SQL (window/aggregate functions), not pandas — reaffirms `0003`.
- Score inputs must be percentage-based (share %, growth %), not raw
  dollar/volume magnitude, so products/markets of very different scale are
  comparable without a separate normalization step.
- Unit-price proxy (`fob_usd / metric_ton`) must always be computed
  sum-then-divide at the target aggregation level, never as an average of
  row-level or sub-group ratios.
- Scope is Brazil's own export data only — a country with zero Brazil
  purchases is invisible to this analysis, by design (Phase 4 is the
  separate "different data sources" problem).

## Decisions

### A single log-linear trend fit is the shared building block
For any (product_level value, geo_level value) group, take the yearly
`fob_usd` time series over a trailing window (**10 years**, or all
available years if fewer), restricted to years with `fob_usd > 0`. Fit a
regression of `ln(fob_usd)` on `year` using DuckDB's built-in aggregates —
no numpy/pandas needed:

```sql
SELECT
    regr_slope(ln(fob_usd), year) AS log_growth_rate,
    regr_r2(ln(fob_usd), year)    AS trend_r2,
    count(*)                      AS years_active
FROM yearly_agg
WHERE year >= current_year - 10
GROUP BY <product_level_col>, <geo_level_col>
```

This one fit produces both a growth number and its consistency signal:
- `annual_growth_pct = exp(log_growth_rate) - 1` — the growth input to the
  score.
- `trend_r2` — how well a single steady exponential trend explains the
  actual yearly values (0–1). A steady climber scores near 1; a spike or
  sawtooth scores low. This is "consistent, noticeable evidence" made
  numeric, and feeds confidence, not the score. **Superseded by the
  2026-09-12 amendment below — use adjusted R², not raw R², and require
  `years_active >= 4`.**

Fitting in log-space (rather than year-over-year % deltas) is deliberate:
trade volume growth is naturally multiplicative, and a single regression
smooths across all years in the window at once instead of chaining
noisy single-year deltas.

### Confidence: three components combined as a geometric mean
1. **Coverage** — `years_active / window_size`, capped at 1. Penalizes
   sparse/gappy history.
2. **Trend consistency** — `trend_r2` directly, from the fit above.
   **Superseded — see the 2026-09-12 amendment: use adjusted R², not raw
   R², and require `years_active >= 4`.**
3. **Volume confidence** — `total_fob_usd / (total_fob_usd + K)`, a
   Bayesian-shrinkage shape (same family as a Wilson/IMDB weighted-rating
   adjustment): approaches 1 as volume grows, sits at 0.5 when volume
   equals `K`. Needed because e.g. $8k → $24k is a "200% growth" headline
   that's really just noise. `K` is derived from the data itself (the
   median total `fob_usd` across all groups at the given aggregation
   level), so it adapts per product/geo level rather than being a
   hardcoded dollar figure.

```
confidence = (coverage_score × trend_r2 × volume_confidence) ^ (1/3)
```

Geometric, not arithmetic, mean — deliberately AND-like. One weak leg (say,
only 2 years of data) drags the whole score down rather than letting a
great trend fit compensate for having almost no history.

### Score: growth × headroom, symmetric across both lenses
Define `share_pct` as this group's slice of the *fixed* axis, in the most
recent year:
- **Product fixed → rank countries**: this country's `fob_usd` ÷ total
  `fob_usd` across all countries, for this product.
- **Country fixed → rank products**: this product's `fob_usd` ÷ total
  `fob_usd` across all products, for this country.

Same shape either way — one query, pivoted.

```
opportunity_score = annual_growth_pct × (1 - share_pct)
```

High growth + low existing share = underserved market with momentum —
matches the ROADMAP's own definition of outlier detection directly.
Negative growth pulls the score negative/low with no special-casing
needed.

### Score and confidence are reported separately, never fused
Output shape is `(rank candidate, opportunity_score, confidence)`. A human
decides how to weigh a high-score/low-confidence lead against a
lower-score/high-confidence one — matches the "actionable leads for manual
follow-up," not an auto-decided ranking.

## Open parameters (defaults chosen, not hard-locked)
- **Trailing window size**: 10 years. Long enough to smooth noise, short
  enough to reflect the current market rather than 1997-era patterns.
- **`K`** (volume-confidence shrinkage constant): median total `fob_usd`
  across groups at the given aggregation level, recomputed per level
  rather than fixed.
- **Equal weighting** in the confidence geometric mean. Fine as a first
  version; revisit if one component turns out to dominate in practice.

These are implementation defaults, not re-litigated here — worth
revisiting once real output is eyeballed against known markets, but not a
blocker to building the first version.

## Consequences
- New `analysis/` module implements this against `marts.exports`,
  parametrized by `product_level`/`geo_level`, exposing both lenses off
  the same underlying query.
- Percentile-rank or log-scale normalization of absolute magnitude
  (considered and set aside when the scoring philosophy was first
  discussed) stays unnecessary as long as score inputs remain
  percentage-based; revisit only if raw magnitude ever gets blended into
  the score directly.
- If ComexStat's per-shipment-price gap or any other unit-price nuance
  changes, the sum-then-divide rule stays independent of this ADR and
  doesn't need revisiting here.

## Amendment (2026-09-12): raw R² is unreliable at low sample sizes

Found while working the methodology against real data in
`notebooks/opportunity_scoring_eda.ipynb`, one product (frozen tongues,
NCM `02062100`) ranked across every importing country. The top of the
ranked-by-`opportunity_score` table was dominated by countries showing
implausible growth rates — Jordan at 1,432 (i.e. ~143,000%/year), Chile at
117, Sierra Leone at 82 — burying genuinely promising, better-supported
candidates (Cambodia, the Philippines) further down.

Root cause: every one of those inflated rows had `years_active = 2`, and
every one showed `trend_r2 = 1.000000` exactly. This isn't a data
coincidence — **a straight line through exactly 2 points fits perfectly by
mathematical necessity**, so `regr_r2` is guaranteed to equal 1.0 at `n=2`
regardless of what those two points are. Raw R² only starts carrying real
information once there are more data points than the model has parameters
to fit (a line has 2: slope + intercept), so it was giving its strongest
"trust me" signal exactly where there was the least reason to.

**Fix:**
1. Require **`years_active >= 4`** before a (product_level, geo_level)
   group gets a score or confidence at all — below that, adjusted R² is
   either undefined (`n=2`) or too unstable to trust (`n=3`).
2. Replace `trend_r2` with **adjusted R²** in the confidence formula,
   everywhere the original Decisions section above said `trend_r2`:

   ```
   trend_r2_adj = 1 - (1 - trend_r2) * (years_active - 1) / (years_active - 2)
   ```

   clipped to `[0, 1]` (a genuinely poor fit can push the raw computation
   negative — treated as zero confidence, not a negative one). Adjusted R²
   is the standard statistical tool for exactly this problem: it explicitly
   penalizes R² for how few data points support it, converging toward raw
   R² as `years_active` grows. Sanity check: adjusted R² must never exceed
   raw R² for `years_active > 2` — if it ever does in an implementation,
   that implementation has a bug (this is how the fix's first, buggy
   notebook attempt was itself caught — a dropped `(n - 1)` factor produced
   adjusted values *higher* than raw R², which is mathematically
   impossible).

Confirmed against real output after the fix: Cambodia (`years_active=4`,
`trend_r2=0.957`) → `trend_r2_adj=0.936`, confidence 0.70; the Philippines
(`years_active=7`, `trend_r2=0.670`) → `trend_r2_adj=0.604`, confidence
0.74 — both now correctly surface near the top of the ranking, ahead of
the `years_active=2` artifacts, which are excluded entirely.

## Status
Accepted, 2026-09-12. Amended 2026-09-12 (adjusted R² + `years_active >= 4`
floor, above).
