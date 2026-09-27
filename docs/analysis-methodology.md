# Analysis methodology

Reference for every number `analysis/opportunity_scoring.py` produces: what
it answers, how it is computed, and how to read it without fooling yourself.

**This is not an ADR.** `docs/decisions/0005-opportunity-scoring-methodology.md`
records *why* these formulas were chosen and what was rejected; that debate
is not repeated here. This document is the working reference — the thing to
open when a number on the dashboard looks surprising, and the source of
truth for the plain-language wording the Streamlit app shows users.

Every section follows the same five-part template, so a new metric can be
added without redesigning the page:

| Part | Purpose |
| --- | --- |
| **Question** | the plain-language question the metric answers |
| **Formula** | the exact computation, in the same terms as the code |
| **Worked example** | real numbers from the tracked `comexstat.duckdb` |
| **How to read it** | what a high or low value actually tells you |
| **Where it misleads** | the failure mode to watch for |

---

## The running example

One example runs through the whole document, so the numbers can be checked
against each other rather than taken on faith:

> **Livers, frozen (NCM `02062200`) → which countries are the best
> opportunities?**
> Default parameters: `window_years=10`, `min_years_active=4`,
> `product_level="ncm_code"`, `geo_level="country"`.

`rank_markets()` returns 52 countries, and **Singapore ranks #1** with an
`opportunity_score` of `1.482`. Its yearly Brazilian-export history, which
every metric below is derived from:

| Year | FOB (USD) | Tons | USD/ton |
| --- | --- | --- | --- |
| 2017 | — | — | — |
| 2018 | 101 | 0.062 | 1,629 |
| 2019 | 463 | 0.231 | 2,004 |
| 2020 | 649 | 0.211 | 3,076 |
| 2021 | 973 | 0.333 | 2,922 |
| 2022 | 11,202 | 5.192 | 2,158 |
| 2023 | 25,964 | 14.489 | 1,792 |
| 2024 | 71,666 | 51.784 | 1,384 |
| 2025 | 150,694 | 88.183 | 1,709 |
| 2026 | 46,899 | 21.737 | 2,158 |

Note the scale before anything else: 2018's entire year was **62 kg** and
$101. This is a market that grew from a rounding error to 88 tons in seven
years — genuinely fast, and still genuinely small. Holding both of those
facts at once is the entire point of reporting score and confidence
separately.

---

## Step 0 — The grain: what a "group" is

**Question.** What is being scored — a country? a region? a product?

Every metric is computed per **group**, and a group is one value of the
*ranked* axis, with the *fixed* axis held constant:

```
ranked axis = the thing being compared (country | region | trade_bloc, or ncm_code | category)
fixed axis  = the thing held constant  (one NCM code, one category, one country, ..., or "overall")
group       = one distinct value of the ranked axis, filtered to the fixed axis
```

`rank_markets()` fixes a product and ranks geographies; `rank_products()`
fixes a geography and ranks products. They are the same query, pivoted —
so every formula below applies unchanged to both.

**Worked example.** Fixed axis: `ncm_code = '02062200'`. Ranked axis:
`country`. Singapore is one group; its input is the nine rows above.

**Where it misleads.** `share_pct` is the only metric whose meaning
*changes* with the pivot, because its denominator is the fixed axis. See
its own section.

---

## Step 1 — The trailing window

**Question.** Which years count?

**Formula.**

```
max_year = the latest year present in marts.exports
min_year = max_year - window_years + 1
```

**Worked example.** `max_year = 2026`, `window_years = 10` →
**2017–2026**. Brazil's data goes back to 1997, so 20 years of history are
deliberately excluded: the question is what the market looks like *now*,
not in 2003.

**How to read it.** Widening the window buys statistical stability and
loses currency. Ten years is long enough for the trend fit to have
something to work with and short enough that a market's 2005 behaviour
does not outvote its 2024 behaviour.

**Where it misleads.** The window is a hard edge, not a taper — a market
that collapsed in 2016 looks untouched by it. And `max_year` is whatever
is in the database, **including a partial current year** (see [Known
distortions](#known-distortions-in-the-current-data)).

---

## Step 2 — `years_active`, and the >= 4 floor

**Question.** How many years in the window did this group actually buy
anything?

**Formula.** Years are aggregated first, then only positive ones are kept:

```
years_active = count of years in [min_year, max_year] where sum(fob_usd) > 0
```

Groups with `years_active < min_years_active` are **dropped entirely** —
no score, no confidence, not ranked. The floor cannot be set below 3, and
4 is the recommended default.

**Worked example.** Singapore: `years_active = 9` (every year from 2018
on, nothing in 2017). Across all frozen-liver buyers, **61 of 113
countries are dropped** by the floor and 52 survive.

**How to read it.** This is the crudest and most important filter in the
methodology. A market with two years of history is not a trend, it is an
anecdote.

**Where it misleads.** A genuinely new market — first shipment in 2025,
growing fast — is invisible until it has four years of history. That is a
deliberate trade: ADR 0005's amendment documents what happened when the
floor was absent (Jordan appearing at ~143,000%/year growth off two data
points). Such markets have to be found by eyeballing recent raw volume,
not by this ranking.

---

## Step 3 — The log-linear trend fit

**Question.** Is there one steady growth trajectory here, and how steep is
it?

**Formula.** A least-squares regression of **log** FOB on year, over the
active years only, using DuckDB's built-in aggregates:

```sql
regr_slope(ln(fob_usd), year) AS log_growth_rate
regr_r2(ln(fob_usd), year)    AS trend_r2
```

Log space, not raw dollars, because trade growth is multiplicative: a
market going 100 → 200 → 400 is *one* straight line in log space and an
accelerating curve in dollars.

**Worked example.** Singapore, 2018–2026 (n = 9):

```
log_growth_rate = 0.910185
trend_r2        = 0.908226
```

**How to read it.** The slope is growth per year *in log units* — not
directly readable, which is why the next step converts it. `trend_r2` is
how much of the year-to-year variation one straight line explains: a
steady climber approaches 1, a spike-then-collapse or a sawtooth scores
low.

**Where it misleads.** A single regression over the whole window cannot
see a **turning point**. A market that grew hard 2017–2022 and has fallen
since 2023 still reports positive growth with a mediocre fit. Only the
yearly history shows that shape — which is why the app puts the history
chart next to the score.

---

## Step 4 — `annual_growth_pct`

**Question.** How fast is this market growing per year, in plain percent?

**Formula.**

```
annual_growth_pct = exp(log_growth_rate) - 1
```

**Worked example.**

```
exp(0.910185) - 1 = 1.484782  →  +148.5% per year
```

**How to read it.** The compound annual growth implied by the fitted line
— not the change between any two specific years. Singapore's +148%/yr
means the fitted trend roughly 2.5x's every year across the window.

**Where it misleads.** Percentage growth is **scale-blind by design**
(that is what makes products of wildly different size comparable), so it
says nothing about whether the market is worth serving. $101 → $150,694
is +148%/yr; so is $101M → $150M. `volume_confidence` and the absolute
tons/FOB columns exist to supply what this number deliberately omits.

---

## Step 5 — `trend_r2_adj` (trend fit)

**Question.** How much should the trend fit be trusted, given how few
years support it?

**Formula.** Adjusted R-squared, clipped to `[0, 1]`:

```
trend_r2_adj = 1 - (1 - trend_r2) * (years_active - 1) / (years_active - 2)
```

**Worked example.**

```
1 - (1 - 0.908226) * (9 - 1) / (9 - 2)
= 1 - 0.091774 * 8/7
= 0.895115
```

Slightly below the raw 0.908 — a small penalty, because nine points is a
reasonable amount of evidence.

**How to read it.** ~0.9 means "a single steady exponential explains
almost all of this market's behaviour". Below ~0.3, the trend line is
drawn through noise and the growth number should not be relied on.

**Where it misleads.** Raw R-squared is actively dangerous at small `n` —
a line through 2 points fits *perfectly by mathematical necessity*, so raw
R-squared reports maximum confidence exactly where there is least
evidence. That is why the adjustment exists and why `years_active >= 4` is
enforced alongside it. The adjustment is only ever a penalty: **adjusted
R-squared can never exceed raw R-squared** for `years_active > 2`. If it
ever does, the implementation has a bug — this is how the first (wrong)
attempt at this fix was caught.

---

## Step 6 — `coverage_score`

**Question.** How complete is this market's history within the window?

**Formula.**

```
coverage_score = min(years_active / window_years, 1.0)
```

**Worked example.** Singapore: `9 / 10 = 0.9`.

**How to read it.** A penalty for gappy or short history. A market present
in every year of the window scores 1.0; one present in four of ten scores
0.4 — a hard ceiling on its confidence no matter how cleanly those four
years line up.

**Where it misleads.** It cannot distinguish *why* a year is missing:
"this market did not exist yet" and "this market stopped buying for a
year" score identically. Singapore's 0.9 is the former; a 0.9 caused by a
gap in the middle of the window would be more worrying.

---

## Step 7 — `volume_confidence` and `K`

**Question.** Is this market big enough that its numbers are not just
noise?

**Formula.** A Bayesian-shrinkage shape — same family as a weighted rating
adjustment:

```
volume_confidence = total_fob_usd / (total_fob_usd + K)

K = median total_fob_usd across every group in the full
    (fixed_level x ranked_level) grid, over the same window
```

`K` is derived from the data, not hardcoded, so it re-scales automatically
across aggregation levels. It is deliberately computed over the **whole
grid**, not just the groups matching the current filter — so "big" means
big compared to a typical product-country pair, not big compared to the
handful of countries on screen.

**Worked example.** For the `ncm_code x country` grid over 2017–2026,
across 1,253 groups: **`K` = $49,736**. Singapore:

```
308,611 / (308,611 + 49,736) = 0.861
```

**How to read it.** 0.5 means "exactly median-sized". Above ~0.8 means
"comfortably larger than a typical group". It approaches 1 asymptotically
— Egypt, at $84M, scores 0.9994.

**Where it misleads.** This is the most over-readable number in the set.
Singapore's 0.861 sounds like a strong endorsement; the underlying figure
is **$308,611 of total trade spread over nine years**, roughly $34k/year.
It cleared the bar because the bar is the median group ($49,736), and the
median product-country pair in this dataset is tiny. `volume_confidence`
answers *"is this statistically substantial?"*, never *"is this
commercially worthwhile?"* — for the latter, read the tons and FOB columns
directly.

---

## Step 8 — `confidence`

**Question.** Taking everything together, how much evidence is behind this
row?

**Formula.** Geometric mean of the three components:

```
confidence = (coverage_score * trend_r2_adj * volume_confidence) ^ (1/3)
```

**Worked example.**

```
(0.9 * 0.895115 * 0.861207) ^ (1/3) = 0.885
```

**How to read it.** **An index on a 0–1 scale, not a probability.** 0.885
does not mean "88.5% likely to be right"; it means all three evidence legs
are individually strong. Read it as a band rather than a precise value:

| Range | Reading |
| --- | --- |
| 0.70 and above | well-evidenced — history, fit, and size all hold up |
| 0.40 – 0.70 | one leg is weak; check which before acting |
| below 0.40 | thin evidence — treat the score as a hypothesis only |

Always look at which leg is *binding*. A confidence of 0.33 from
`coverage 0.4 x fit 0.17 x volume 0.52` (Guyana, in this ranking) is a
short-history problem, not a size problem, and the remedy — wait for more
years — is different.

**Where it misleads.** The geometric mean is deliberately AND-like: one
near-zero leg drags the whole thing toward zero regardless of the other
two, and that is intended. But the reverse also holds — a high
`confidence` cannot rescue a market that is simply too small to bother
with, because "too small to bother with" is a commercial judgement that
appears nowhere in this formula.

---

## Step 9 — `share_pct`

**Question.** How much of this trade does the group already account for —
i.e. how much headroom is left?

**Formula.** Share of the **fixed** axis, in the most recent year only:

```
share_pct = group's fob_usd in max_year
            / total fob_usd in max_year across the fixed axis
```

Which pivots with the lens:

- product fixed → this country's share of that product's exports
- country fixed → this product's share of that country's purchases

**Worked example.** In 2026, Brazil exported $25,468,727 of frozen livers
in total. Singapore took $46,899:

```
46,899 / 25,468,727 = 0.00184  →  0.18%
```

For contrast, Egypt took $17,259,004 of that same total — **67.8%**.

**How to read it.** Low share is the "opportunity" half of the score: a
market Brazil barely serves has room to grow. High share means the
position is already won, and growth there is defence, not expansion.

**Where it misleads.** Two things.

1. It is **one year**, not the window — deliberately, since headroom is a
   question about *now*, but it makes `share_pct` the noisiest input to
   the score.
2. It is share of **Brazil's** exports, not of the destination's total
   imports. A country buying 100% of its beef from Brazil and one buying
   1% look identical here. ComexStat cannot see other suppliers at all —
   that gap is Phase 4's problem, not a defect in this formula.

---

## Step 10 — `opportunity_score`

**Question.** Where is there both momentum and room to grow?

**Formula.**

```
opportunity_score = annual_growth_pct * (1 - share_pct)
```

**Worked example.**

```
1.484782 * (1 - 0.001841) = 1.482049
```

**How to read it.** The score is not an arbitrary index — it has a
readable meaning: **the growth rate, discounted by the share already
held.** Singapore's `1.482` reads as *"growing about 148%/yr with
essentially all of its headroom still open (99.8%)."* That is why it
barely differs from its raw growth rate: at 0.18% share, the discount is
negligible.

The discount only bites when share is large:

| Country | Growth/yr | Share | Headroom | Score |
| --- | --- | --- | --- | --- |
| Singapore | +148.5% | 0.18% | 99.82% | **1.482** |
| Egypt | +66.7% | 67.77% | 32.23% | **0.215** |

Egypt is growing fast in absolute terms — $35.8M in 2025 — but two-thirds
of the market is already Brazil's, so most of that growth is not
*opportunity*. Negative growth produces a negative score with no
special-casing.

**Where it misleads.** The score inherits everything `annual_growth_pct`
omits — above all, scale. It is a **ranking key, not a magnitude**: "1.482
vs. 0.915" means "ranks higher", not "1.6x better". And because
`(1 - share_pct)` is bounded in `[0, 1]` while growth is unbounded, growth
dominates: for any market under ~5% share, the score is effectively just
its growth rate. Never read it without `confidence` and absolute volume
beside it.

---

## Step 11 — `unit_price_usd_per_ton`

**Question.** How expensive is this product to this market?

**Formula.** Summed, *then* divided — at the query's own grain, over the
same window:

```
unit_price_usd_per_ton = sum(fob_usd) / sum(metric_ton)
```

**Worked example.** Singapore, 2017–2026: $308,611 over 182.222 tons →
**$1,694/ton**.

Averaging the nine yearly ratios in the table instead gives **$2,092/ton**
— a **23.5% overstatement**, because 2018's 62 kg shipment at $1,629/ton
gets the same weight as 2025's 88 tons.

**How to read it.** A descriptive column, not a score input. Useful for
sanity-checking a market (is it paying premium or discount prices?) and
for spotting product-mix differences between destinations.

**Where it misleads.** ComexStat has no per-shipment prices, only monthly
aggregates, so this is a proxy for an average realised price — it hides
mix, incoterms, and product quality within an NCM code. The
sum-then-divide rule is not a preference; **divide-then-average is
arithmetically wrong** for a weighted quantity, and the demonstration
above is why.

---

## Reading a result: the pair that matters

The most useful comparison in this ranking is not #1 vs. #2 — it is #1 vs.
the incumbent:

| | Singapore | Egypt |
| --- | --- | --- |
| Rank by score | **#1** | #31 |
| `opportunity_score` | 1.482 | 0.215 |
| `confidence` | 0.885 | 0.863 |
| `annual_growth_pct` | +148.5% | +66.7% |
| `share_pct` | 0.18% | 67.8% |
| Total FOB in window | $308,611 | $83,991,851 |
| Total tons in window | 182 | 51,503 |

The two rows have **nearly identical confidence** and a **272x difference
in size**. Nothing is broken: the methodology is answering exactly the
question it was asked — *where is there growth with headroom left* — and
Singapore is the honest answer. But "highest-scoring" and "most
commercially significant" are different questions, and this table is what
that difference looks like in practice.

The intended workflow follows from that:

1. **Score** narrows 113 countries to a shortlist.
2. **Confidence** says which shortlist entries are backed by evidence.
3. **Absolute tons and FOB** say which are worth a sales conversation.
4. **The yearly history** shows whether the trend is still intact.

The score is a lead generator for manual research, never the decision.

---

## Known distortions in the current data

Real issues in the output as it stands today, not hypotheticals.

### The current year is partial

`max_year` is whatever the database holds, and the 2026 slice currently
covers **months 1–8 only**. Two consequences:

- The trend fit treats a two-thirds year as a full one, **understating
  growth** for any market that is still growing. Egypt's 2026 ($17.3M)
  against 2025 ($35.8M) reads as a collapse; it is 8 months against 12.
- `share_pct` is computed on that partial year. Being a ratio, this mostly
  cancels out — but not for markets whose buying is seasonal inside the
  missing months.

Options when this gets addressed: exclude the partial year from the fit,
annualise it, or report it separately. Not yet decided.

### Brazil appears as a destination country

`Brazil` shows up as a frozen-liver *buyer* ($2,147 over 4 years, ranked
#7 by score) — re-imports or returned shipments inside ComexStat's export
data. Small in value, but it is not a market, and it should probably be
excluded at the marts layer.

### `K`'s bar is low

At `K = $49,736` for the `ncm_code x country` grid, a market averaging
$34k/year clears `volume_confidence = 0.86`. The formula is behaving as
designed — the median product-country pair really is that small — but the
label "volume confidence" oversells it. Reading it next to absolute tons
is the mitigation; a floor on absolute volume would be a change to the
methodology, not a bug fix.

### Pending unit change

The code currently emits `total_kg`. Tons are the only meaningful unit for
this trade, so this document is written in tons throughout; the column
becomes `total_metric_ton` when that change lands.

---

## Quick reference

| Column | One-line meaning | Scale |
| --- | --- | --- |
| `years_active` | years in the window with any exports | count |
| `annual_growth_pct` | compound yearly growth from the fitted trend | % (unbounded) |
| `trend_r2_adj` | how well one steady trend explains the history | 0–1 |
| `coverage_score` | how complete the history is within the window | 0–1 |
| `volume_confidence` | how large this group is vs. a typical one | 0–1 |
| `confidence` | all three evidence legs combined (geometric mean) | 0–1 index |
| `share_pct` | slice of the fixed axis already held, latest year | % |
| `opportunity_score` | growth, discounted by share already held | ranking key |
| `total_fob_usd` | total export value in the window | USD |
| `total_metric_ton` | total export weight in the window | tons |
| `unit_price_usd_per_ton` | realised average price, sum-then-divide | USD/ton |

---

## Adding a new metric

Keep the five-part template, and specifically:

1. State the **question** in the user's language before the formula.
2. Write the formula in the **same names the code uses**, so the two can
   be diffed by eye.
3. Use the **same running example** (frozen livers → Singapore) unless the
   metric cannot be demonstrated with it — one set of numbers that ties
   together beats eleven unrelated ones.
4. Say plainly whether it feeds the **score**, the **confidence**, or is
   **descriptive only**.
5. Fill in **Where it misleads** honestly. Every metric here has a failure
   mode; a section without one is an unfinished section.

Then add a row to [Quick reference](#quick-reference), and — if the app
surfaces it — the same one-line meaning to the app's metric glossary, so
the dashboard and this document cannot drift apart.
