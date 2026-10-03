# Analysis methodology

Reference for every number `analysis/opportunity_scoring.py` produces: what
it answers, how it is computed, and how to read it without fooling yourself.

**This is not an ADR.** `docs/decisions/0005-opportunity-scoring-methodology.md`
records *why* these formulas were chosen and what was rejected; that debate
is not repeated here. This document is the working reference — the thing to
open when a number on the dashboard looks surprising, and the source of
truth for the plain-language wording the Streamlit app shows users. The
app's **Explain the numbers** page is the business-reader version of this
document: the same metrics, applied to whichever result the user picks,
with an action for each (`app/explanations.py`).

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
> Data through **August 2026**.

`rank_markets()` returns 52 countries, and **Singapore ranks #1** with an
`opportunity_score` of `1.513`.
Every metric below is derived from Singapore's Brazilian-export history,
grouped into trailing 12-month periods ending at the latest month in the
data (see [Step 1](#step-1--the-trailing-window)):

| Period | `periods_ago` | FOB (USD) | Tons | USD/ton |
| --- | --- | --- | --- | --- |
| Sep 2016 – Aug 2018 | 9, 8 | — | — | — |
| Sep 2018 – Aug 2019 | 7 | 524 | 0.273 | 1,919 |
| Sep 2019 – Aug 2020 | 6 | 565 | 0.195 | 2,897 |
| Sep 2020 – Aug 2021 | 5 | 752 | 0.237 | 3,173 |
| Sep 2021 – Aug 2022 | 4 | 9,083 | 4.258 | 2,133 |
| Sep 2022 – Aug 2023 | 3 | 19,599 | 10.191 | 1,923 |
| Sep 2023 – Aug 2024 | 2 | 62,338 | 44.210 | 1,410 |
| Sep 2024 – Aug 2025 | 1 | 110,513 | 67.569 | 1,636 |
| Sep 2025 – Aug 2026 | 0 | 105,237 | 55.289 | 1,903 |

Note the scale before anything else: the first active period was **273 kg**
and $524. This is a market that grew from a rounding error to ~68 tons a
year in six years — genuinely fast, and still genuinely small. Holding both
of those facts at once is the entire point of reporting score and
confidence separately.

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

Everything reads from `marts.exports`, which excludes Brazil itself as a
destination — ComexStat lists it for a few hundred rows (~$204k in total,
re-imports or returned goods), and it is not an export market.
`staging.exports` keeps those rows, faithful to the source.

**Worked example.** Fixed axis: `ncm_code = '02062200'`. Ranked axis:
`country`. Singapore is one group; its input is the eight active periods
above.

**Where it misleads.** `share_pct` is the only metric whose meaning
*changes* with the pivot, because its denominator is the fixed axis. See
its own section.

---

## Step 1 — The trailing window

**Question.** Which stretch of time counts, and how is it cut into years?

**Formula.** Time is cut into **trailing 12-month periods** ending at the
latest month in the data, not calendar years:

```
month_index = year * 12 + month - 1
latest_idx  = max(month_index) across marts.exports
periods_ago = (latest_idx - month_index) // 12     -- 0 = the latest 12 months

in window  <=>  periods_ago < window_years
```

Every "year" in the methodology — `years_active`, the growth rate, the
share year — is one of these periods.

**Worked example.** Latest month = **Aug 2026**, `window_years = 10` →
ten periods, **Sep 2016 – Aug 2026**. Brazil's data goes back to 1997, so
about 20 years of history are deliberately excluded: the question is what
the market looks like *now*, not in 2003.

**How to read it.** Widening the window buys statistical stability and
loses currency. Ten years is long enough for the trend fit to have
something to work with and short enough that a market's 2005 behaviour
does not outvote its 2024 behaviour.

Periods rather than calendar years because the latest calendar year is
almost always partial: ComexStat publishes monthly, so in September the
current year holds eight months. Scored as a full year, it reads as a
one-third collapse for every market — before this change, that dragged
Guyana's trend fit down to 0.17 (now 0.69) and Hong Kong's to 0.25 (now
0.69). Rolling periods keep the newest data, keep every period a full 12
months, and invent nothing (unlike annualising, which would guess at the
missing months and get seasonal buyers wrong).

**Where it misleads.** The window is a hard edge, not a taper — a market
that collapsed just before it looks untouched by it. Period boundaries
also move every month as new data lands, so a ranking pulled in September
and one pulled in October are cut differently; small markets with one or
two shipments a year can shift between periods, and a shipment landing on
one side of a boundary or the other can change a group's first period —
which matters for the [leading trim](#step-2--years_active-the-leading-trim-and-the--4-floor).

---

## Step 2 — `years_active`, the leading trim, and the >= 4 floor

**Question.** In how many of the window's periods did this group actually
buy anything — in amounts that are more than noise?

**Formula.** Periods are aggregated first, and only positive ones are
kept. Then **leading tiny periods are trimmed**: walking from the oldest
period forward, every period before the first one worth at least 1% of
the group's median active period is dropped.

```
active periods = periods with periods_ago < window_years and sum(fob_usd) > 0
trimmed        = leading active periods with fob_usd < 0.01 * median(active fob_usd)
years_active   = count of active periods, minus trimmed ones
```

Trimmed periods are treated as noise, not activity: they are excluded from
`years_active`, the trend fit, and the window totals. The trim is
**leading-only** — a tiny period in the middle or at the end of the series
is kept, so a market that collapses to almost nothing still reads as
shrinking.

Why it exists: in log space a near-zero first value has enormous leverage
on the slope — going from $42 to $13,080 is a bigger log step than going
from $1M to $100M. Bahrain's frozen-liver history starts with a $42 period
(two shipments of $22 and $20), then runs $13,080 → $33,241 → $13,960 →
$102,339. Untrimmed, that $42 made Bahrain the #1 opportunity at
+379%/yr; trimmed (0.3% of its $13,960 median), it is +70%/yr and #7.
Across the whole `ncm_code x country` grid the trim touches 16 groups.

Groups with `years_active < min_years_active` are **dropped entirely** —
no score, no confidence, not ranked. The floor cannot be set below 3, and
4 is the recommended default.

**Worked example.** Singapore: `years_active = 8` (every period from
Sep 2018 – Aug 2019 on; nothing in the two before). Its first period,
$524, is 3.7% of its $14,341 median — small but real, so nothing is
trimmed. Across all frozen-liver
buyers in the window, **61 of 113 countries are dropped** by the floor and
52 survive.

**How to read it.** This is the crudest and most important filter in the
methodology. A market with two years of history is not a trend, it is an
anecdote.

**Where it misleads.** A genuinely new market — first shipment last year,
growing fast — is invisible until it has four periods of history. That is a
deliberate trade: ADR 0005's amendment documents what happened when the
floor was absent (Jordan appearing at ~143,000%/year growth off two data
points). Such markets have to be found by eyeballing recent raw volume,
not by this ranking.

---

## Step 3 — The log-linear trend fit

**Question.** Is there one steady growth trajectory here, and how steep is
it?

**Formula.** A least-squares regression of **log** FOB on time, over the
active periods only, using DuckDB's built-in aggregates. Time is
`-periods_ago`, so it runs forward (oldest to newest) and one unit is one
year:

```sql
regr_slope(ln(fob_usd), -periods_ago) AS log_growth_rate
regr_r2(ln(fob_usd), -periods_ago)    AS trend_r2
```

Log space, not raw dollars, because trade growth is multiplicative: a
market going 100 → 200 → 400 is *one* straight line in log space and an
accelerating curve in dollars.

**Worked example.** Singapore, eight active periods (n = 8):

```
log_growth_rate = 0.922851
trend_r2        = 0.927228
```

**How to read it.** The slope is growth per year *in log units* — not
directly readable, which is why the next step converts it. `trend_r2` is
how much of the period-to-period variation one straight line explains: a
steady climber approaches 1, a spike-then-collapse or a sawtooth scores
low.

**Where it misleads.** Two ways.

1. A single regression over the whole window cannot see a **turning
   point**. A market that grew hard for six years and has fallen for the
   last two still reports positive growth with a mediocre fit. Only the
   period-by-period history shows that shape — the app does not chart it
   yet, so check `marts.exports` directly before acting on a lead.
2. In log space, a **single extreme point** has outsized leverage on the
   slope. The worst case — a near-zero *first* period — is removed by the
   [leading trim](#step-2--years_active-the-leading-trim-and-the--4-floor);
   extreme points elsewhere in the series are not, and show up as a lower
   `trend_r2` instead.

---

## Step 4 — `annual_growth_pct`

**Question.** How fast is this market growing per year, in plain percent?

**Formula.**

```
annual_growth_pct = exp(log_growth_rate) - 1
```

**Worked example.**

```
exp(0.922851) - 1 = 1.516455  →  +151.6% per year
```

**How to read it.** The compound annual growth implied by the fitted line
— not the change between any two specific periods. Singapore's +152%/yr
means the fitted trend roughly 2.5x's every year across the window.

**Where it misleads.** Percentage growth is **scale-blind by design**
(that is what makes products of wildly different size comparable), so it
says nothing about whether the market is worth serving. $524 → $105,237
is +152%/yr; so is $524M → $105B. `volume_confidence` and the absolute
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
1 - (1 - 0.927228) * (8 - 1) / (8 - 2)
= 1 - 0.072772 * 7/6
= 0.915100
```

Slightly below the raw 0.927 — a small penalty, because eight points is a
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

**Worked example.** Singapore: `8 / 10 = 0.8`.

**How to read it.** A penalty for gappy or short history. A market present
in every period of the window scores 1.0; one present in four of ten scores
0.4 — a hard ceiling on its confidence no matter how cleanly those four
periods line up.

**Where it misleads.** It cannot distinguish *why* a period is missing:
"this market did not exist yet" and "this market stopped buying for a
year" score identically. Singapore's 0.8 is the former; a 0.8 caused by
gaps in the middle of the window would be more worrying.

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

**Worked example.** For the `ncm_code x country` grid over Sep 2016 –
Aug 2026, across 1,247 groups: **`K` = $52,119**. Singapore:

```
308,611 / (308,611 + 52,119) = 0.856
```

**How to read it.** 0.5 means "exactly median-sized". Above ~0.8 means
"comfortably larger than a typical group". It approaches 1 asymptotically
— Egypt, at $84M, scores 0.9994.

**Where it misleads.** This is the most over-readable number in the set.
Singapore's 0.856 sounds like a strong endorsement; the underlying figure
is **$308,611 of total trade spread over eight years**, roughly $39k/year.
It cleared the bar because the bar is the median group ($52,119), and the
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
(0.8 * 0.915100 * 0.855518) ^ (1/3) = 0.856
```

**How to read it.** **An index on a 0–1 scale, not a probability.** 0.856
does not mean "85.6% likely to be right"; it means all three evidence legs
are individually strong. Read it as a band rather than a precise value:

| Range | Reading |
| --- | --- |
| 0.70 and above | well-evidenced — history, fit, and size all hold up |
| 0.40 – 0.70 | one leg is weak; check which before acting |
| below 0.40 | thin evidence — treat the score as a hypothesis only |

Always look at which leg is *binding*. A confidence of 0.52 from
`coverage 0.4 x fit 0.69 x volume 0.50` (Guyana, #2 in this ranking) is a
short-history problem first — four active periods of ten — and the
remedy, waiting for more years, is different from the remedy for a weak
fit or a tiny market.

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

**Formula.** Share of the **fixed** axis, in the latest 12-month period
only (`periods_ago = 0`):

```
share_pct = group's fob_usd in the latest period
            / total fob_usd in the latest period across the fixed axis
```

Which pivots with the lens:

- product fixed → this country's share of that product's exports
- country fixed → this product's share of that country's purchases

**Worked example.** From Sep 2025 to Aug 2026, Brazil exported $49,039,992
of frozen livers in total. Singapore took $105,237:

```
105,237 / 49,039,992 = 0.00215  →  0.21%
```

For contrast, Egypt took $30,054,566 of that same total — **61.3%**.

**How to read it.** Low share is the "opportunity" half of the score: a
market Brazil barely serves has room to grow. High share means the
position is already won, and growth there is defence, not expansion.

**Where it misleads.** Two things.

1. It is **one period**, not the window — deliberately, since headroom is
   a question about *now*, but it makes `share_pct` the noisiest input to
   the score. Being a full 12 months, it does at least count seasonal
   buyers fairly, whichever months they buy in.
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
1.516455 * (1 - 0.002146) = 1.513201
```

**How to read it.** The score is not an arbitrary index — it has a
readable meaning: **the growth rate, discounted by the share already
held.** Singapore's `1.513` reads as *"growing about 152%/yr with
essentially all of its headroom still open (99.8%)."* That is why it
barely differs from its raw growth rate: at 0.21% share, the discount is
negligible.

The discount only bites when share is large:

| Country | Growth/yr | Share | Headroom | Score |
| --- | --- | --- | --- | --- |
| Singapore | +151.6% | 0.21% | 99.79% | **1.513** |
| Egypt | +81.9% | 61.29% | 38.71% | **0.317** |

Egypt is growing fast in absolute terms — $30.1M in the latest 12 months,
up from $5.1M two periods earlier — but most of the market is already
Brazil's, so most of that growth is not *opportunity*. Negative growth
produces a negative score with no special-casing.

**Where it misleads.** The score inherits everything `annual_growth_pct`
omits — above all, scale. It is a **ranking key, not a magnitude**: "1.513
vs. 0.893" means "ranks higher", not "1.7x better". And because
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

**Worked example.** Singapore, Sep 2016 – Aug 2026: $308,611 over 182.222
tons → **$1,694/ton**.

Averaging the eight per-period ratios in the table instead gives
**$2,124/ton** — a **25.4% overstatement**, because the Sep 2019 – Aug 2020
period's 195 kg at $2,897/ton gets the same weight as Sep 2024 – Aug 2025's
68 tons at $1,636/ton.

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

The most useful comparison in this ranking is not #1 vs. #2 — it is the
top-ranked newcomer vs. the incumbent:

| | Singapore | Egypt |
| --- | --- | --- |
| Rank by score | **#1** | #22 |
| `opportunity_score` | 1.513 | 0.317 |
| `confidence` | 0.856 | 0.876 |
| `annual_growth_pct` | +151.6% | +81.9% |
| `share_pct` | 0.21% | 61.3% |
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
4. **The period-by-period history** shows whether the trend is still
   intact.

The score is a lead generator for manual research, never the decision.

---

## Known distortions in the current data

Real issues in the output as it stands today, not hypotheticals.

### Trimming can reveal a market reopening (Mexico)

The [leading trim](#step-2--years_active-the-leading-trim-and-the--4-floor)
compares each period against the group's *median*, so when a market's
recent volume dwarfs its old volume, old-but-real periods can fall under
1% and be trimmed. Boneless beef (`02023000`) to Mexico: two periods of
~$40–50k early in the window, a four-year gap, then a steep ramp. Those
early periods are under 1% of today's median, so they are trimmed and the
fit sees only the recent ramp — +513%/yr instead of +205%/yr.

That is arguably the right reading (a gap followed by a ramp is a market
opening, and the old trade was a different regime), but it is a judgement
the formula makes silently. Any group with a multi-year gap inside the
window deserves a look at its raw history before its growth is trusted.

### `K`'s bar is low

At `K = $52,119` for the `ncm_code x country` grid, a market averaging
$39k/year clears `volume_confidence = 0.86`. The formula is behaving as
designed — the median product-country pair really is that small — but the
label "volume confidence" oversells it. Reading it next to absolute tons
is the mitigation; a floor on absolute volume would be a change to the
methodology, not a bug fix.

---

## Quick reference

| Column | One-line meaning | Scale |
| --- | --- | --- |
| `years_active` | 12-month periods in the window with exports, after the leading trim | count |
| `annual_growth_pct` | compound yearly growth from the fitted trend | % (unbounded) |
| `trend_r2_adj` | how well one steady trend explains the history | 0–1 |
| `coverage_score` | how complete the history is within the window | 0–1 |
| `volume_confidence` | how large this group is vs. a typical one | 0–1 |
| `confidence` | all three evidence legs combined (geometric mean) | 0–1 index |
| `share_pct` | slice of the fixed axis already held, latest 12 months | % |
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

Then add a row to [Quick reference](#quick-reference), and the same
one-line meaning to `METRIC_GLOSSARY` in `analysis/opportunity_scoring.py`
— the app shows it as that column's header tooltip. A test compares the
two word for word, so the dashboard and this document cannot drift apart.
