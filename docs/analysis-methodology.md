# Analysis methodology

Reference for every number `analysis/opportunity_scoring.py` produces: what
it answers, how it is computed, and how to read it without fooling yourself.

**This is not an ADR.** `docs/decisions/0005-opportunity-scoring-methodology.md`
records *why* the confidence formulas were chosen, and
`docs/decisions/0006-opportunity-index.md` records why the 0–100
opportunity index replaced 0005's `growth x (1 - share)` score; that debate
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

`rank_markets()` returns 52 countries. **Libya ranks #1** with an
`opportunity_score` of `60.66`; **Singapore ranks #7** with `31.33`.

The running example stays **Singapore**, not Libya: its eight-period
history exercises every step (trend fit, recent momentum, price trend), and
it is the clearest case of what the index changed. It is the fastest
grower in the set, held back because it buys less than one container a
year. Libya has only four active periods, too few to show most steps well.

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
year in six years — genuinely fast, and still genuinely small. The index
holds both facts at once: growth lifts its `attractiveness`, size caps its
`materiality`.

---

## Step 0 — The grain: what a "group" is

**Question.** What is being scored — a country? a region? a product?

Every metric is computed per **group**, and a group is one value of the
*ranked* axis, with the *fixed* axis held constant:

```
ranked axis = the thing being compared (country | region | trade_bloc, or ncm_code | category)
fixed axis  = the thing held constant  (one NCM code, one category, one country, ..., or "overall")
group       = one distinct value of the ranked axis, filtered to the fixed axis
ranked set  = all the groups one query returns (the 52 countries above)
```

`rank_markets()` fixes a product and ranks geographies; `rank_products()`
fixes a geography and ranks products. They are the same query, pivoted —
so every formula below applies unchanged to both.

Everything reads from `marts.exports`, which excludes Brazil itself as a
destination — ComexStat lists it for a few hundred rows (230 rows, ~$204k
in total, re-imports or returned goods), and it is not an export market.
`staging.exports` keeps those rows, faithful to the source.

**Worked example.** Fixed axis: `ncm_code = '02062200'`. Ranked axis:
`country`. Singapore is one group; its input is the eight active periods
above. The ranked set is the 52 countries that clear the floor.

**Where it misleads.** Two metrics *change meaning* with the query.
`share_pct` changes with the pivot, because its denominator is the fixed
axis (see its own section). `attractiveness`, and so `opportunity_score`,
change with the ranked set, because they are built from percentile ranks
**within the ranked set** (see [Step 15](#step-15--attractiveness)).

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

Every "year" in the methodology — `years_active`, the growth rates, the
share year, `tons_per_year` — is one of these periods.

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
`years_active`, the trend fit, the price trend, and the window totals. The
trim is **leading-only** — a tiny period in the middle or at the end of the series
is kept, so a market that collapses to almost nothing still reads as
shrinking.

Why it exists: in log space a near-zero first value has enormous leverage
on the slope — going from $42 to $13,080 is a bigger log step than going
from $1M to $100M. Bahrain's frozen-liver history (spelled "Bahrein" in
the data) starts with a $42 period (two shipments of $22 and $20), then
runs $13,080 → $33,241 → $13,960 → $102,339. Untrimmed, that $42 gave it
+379%/yr, which made it the #1 opportunity under the old growth-based
score. Trimmed (0.3% of its $13,960 median), it is +70%/yr. Across the
whole `ncm_code x country` grid the trim touches 27 groups, 16 of which
still clear the floor and get scored.

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
   last two still reports positive growth with a mediocre fit. The
   [recent-momentum](#step-10--recent_growth_pct) columns partly catch
   this. The period-by-period history chart on the app's Explain page
   (`period_history()`) shows it directly, so check it before acting on a
   lead.
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

That is the highest growth rate of the 52 countries in the ranked set.

**How to read it.** The compound annual growth implied by the fitted line
— not the change between any two specific periods. Singapore's +152%/yr
means the fitted trend roughly 2.5x's every year across the window. It is
the "long-run momentum" input to [`attractiveness`](#step-15--attractiveness)
(30% of it).

**Where it misleads.** Percentage growth is **scale-blind by design**
(that is what makes products of wildly different size comparable), so it
says nothing about whether the market is worth serving. $524 → $105,237
is +152%/yr; so is $524M → $105B. Under the old score this omission was
the whole problem, because the score *was* the growth rate. The index
supplies scale separately, through the size part of `attractiveness` and
through [`materiality`](#step-14--materiality).

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
commercially worthwhile?"* — that is [`materiality`](#step-14--materiality)'s
job.

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
| below 0.40 | thin evidence — treat the index as a hypothesis only |

Always look at which leg is *binding*. A confidence of 0.52 from
`coverage 0.4 x fit 0.69 x volume 0.50` (Guyana, #13 in this ranking) is a
short-history problem first — four active periods of ten — and the
remedy, waiting for more years, is different from the remedy for a weak
fit or a tiny market.

Confidence now feeds the index, through the
[`evidence` factor](#step-16--opportunity_score-the-opportunity-index):
`0.5 + 0.5 x confidence`.

**Where it misleads.** The geometric mean is deliberately AND-like: one
near-zero leg drags the whole thing toward zero regardless of the other
two, and that is intended. But the reverse also holds — a high
`confidence` cannot rescue a market that is simply too small to bother
with, because "too small to bother with" is a commercial judgement this
formula does not make. `materiality` makes it.

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

**How to read it.** Low share means room to grow. `1 - share_pct` is the
index's [`headroom`](#step-16--opportunity_score-the-opportunity-index)
factor. A market Brazil barely serves keeps almost all of its index. One
where the position is already won keeps only the part not yet Brazil's,
because growth there is defence, not expansion.

**Where it misleads.** Two things.

1. It is **one period**, not the window — deliberately, since headroom is
   a question about *now*, but it makes `share_pct` the noisiest input to
   the index. Being a full 12 months, it does at least count seasonal
   buyers fairly, whichever months they buy in.
2. It is share of **Brazil's** exports, not of the destination's total
   imports. A country buying 100% of its beef from Brazil and one buying
   1% look identical here. ComexStat cannot see other suppliers at all —
   that gap is Phase 4's problem, not a defect in this formula.

---

## Step 10 — `recent_growth_pct`

**Question.** How fast has this market grown *lately*, not across the
whole window?

**Formula.** The same log-linear growth as `annual_growth_pct`, fitted only
to the last `RECENT_PERIODS = 4` periods (`periods_ago < 4`), over the
periods in them with positive FOB:

```
recent_growth_pct = exp(slope of ln(fob_usd) on -periods_ago, last 4 periods) - 1
                    (NaN if fewer than 3 of the 4 periods had exports)
```

**Worked example.** Singapore's last four periods: $19,599 → $62,338 →
$110,513 → $105,237.

```
slope = 0.561477  →  exp(0.561477) - 1 = 0.753260  →  +75.3% per year
```

That is half its window-long +152%/yr: the steep early climb from a few
hundred dollars is outside the last four periods, and the latest period
dipped.

**How to read it.** Compared with `annual_growth_pct`, it shows whether
momentum is holding up. Recent growth well below long-run growth means the
market is maturing or turning; above it, accelerating. It is one half of
the "recent momentum" part of `attractiveness` (0.6 of that part).

**Where it misleads.** Four points is a short line: one big shipment
landing in one period rather than the next moves it a lot. It is missing
for markets that bought in fewer than three of the last four periods. In
this ranking that is 4 of 52 (Qatar, Iran, Belize, Isle of Man). A missing
value sits at the neutral middle of the percentile ranking (0.5) rather
than being punished.

---

## Step 11 — `recent_consistency`

**Question.** Of the last few year-on-year changes, how many were up?

**Formula.** Over the last 4 periods, with periods that had no sales filled
in as $0:

```
changes            = each of the last 3 period-on-period changes whose starting period was > 0
recent_consistency = share of those changes that were up (later period > earlier period)
                     (NaN if there is no such change)
```

A period with no sales counts as a **down**: it is a real "bought
nothing", not missing data. A change *starting* from a zero period cannot
be compared and is skipped.

**Worked example.** Singapore: $19,599 → $62,338 (up) → $110,513 (up) →
$105,237 (down), so **2 of 3 = 0.667**.

Qatar shows the zero-period rules. Its last four periods are $0 → $137,257
→ $0 → $41,044. Only one change starts from a positive period ($137,257 →
$0, a down), so `recent_consistency = 0.0`, resting on a single change.

**How to read it.** 1.0 is three rises in a row; 0.0 is no rise at all. It
is the other half of the "recent momentum" part of `attractiveness` (0.4
of that part), there to reward steady climbers over one lucky period.

**Where it misleads.** It rests on **at most 3 changes**, so it can only
take a handful of values (0, ⅓, ½, ⅔, 1) and ties are everywhere: 6 of
the 52 countries here sit at 1.0. It also ignores size, so a rise of $1
and a rise of $1M count the same.

---

## Step 12 — `price_trend_pct`

**Question.** Is this market paying more per ton over time?

**Formula.** Log-linear growth of the average price per ton, over the
fitted (`in_fit`, i.e. not leading-trimmed) periods that have tons > 0:

```
price per period = fob_usd / metric_ton
price_trend_pct  = exp(slope of ln(price) on -periods_ago) - 1
                   (NaN if fewer than 3 such periods)
```

**Worked example.** Singapore's eight priced periods: $1,919 → $2,897 →
$3,173 → $2,133 → $1,923 → $1,410 → $1,636 → $1,903 per ton.

```
slope = -0.064936  →  exp(-0.064936) - 1 = -0.062873  →  -6.3% per year
```

**How to read it.** A market with rising $/t pays more for the same
product over time. That can signal demand outrunning supply, or buyers
moving up to better product. It is the "rising price" part of
`attractiveness` (20%).

**Where it misleads.** Two ways.

1. **A rising $/t can be a mix shift, not a price rise.** Within one NCM
   code, a buyer moving toward pricier cuts, better grades, or a
   different incoterm raises $/t with no change in what any single
   product costs. At `category` or `overall` level the mix effect is
   larger still.
2. Every period counts equally in the fit, whatever its tonnage.
   Singapore's falling trend is driven by its first three periods, of
   roughly 200 kg each at $1,919–$3,173/t, weighing as much as the
   44–68 t periods at $1,410–$1,903/t. Small early lots at high per-ton
   prices make a market look like its price is falling.

---

## Step 13 — `tons_per_year`

**Question.** How much does this market actually take, in a typical year
it buys?

**Formula.**

```
tons_per_year = total_metric_ton / years_active
```

This is per **active** year, not per window year. A market that bought in
4 of 10 periods is measured on the 4.

**Worked example.** Singapore: `182.222 / 8 = 22.778` tons a year. Libya,
#1: `942.863 / 4 = 235.716`. Egypt: 5,722.5.

**How to read it.** The plain commercial size of the market, in the unit a
trading desk thinks in. It is the input to `materiality`.

**Where it misleads.** Dividing by active years flatters intermittent
buyers: a market that bought 100 t across 4 active periods of 10 reads as
25 t/yr, not 10. It is also an average over the window, so a market that
grew from 0.3 t to 68 t (Singapore) reads as 23 t, below its latest
period's 55 t. Read it next to the latest period's tons in the history.

---

## Step 14 — `materiality`

**Question.** Is this market big enough to be worth a sales effort?

**Formula.** A saturating curve anchored on one container:

```
materiality = tons_per_year / (tons_per_year + CONTAINER_TONS)
CONTAINER_TONS = 25    -- ~ one 40-ft reefer container of frozen beef
```

One container a year scores 0.5; ten containers (250 t) score 0.91; a
tenth of a container scores 0.09.

**Worked example.** Singapore:

```
22.778 / (22.778 + 25) = 0.476744
```

Singapore buys under one container a year, so it keeps under half of its
index. Libya (235.7 t/yr) scores 0.904. Guyana (26.8 t over the whole
window, 6.7 t/yr) scores 0.211.

**How to read it.** This is the commercial floor the old score lacked.
Above ~0.8 (100 t/yr, four containers) the market is big enough that size
stops mattering and the other factors decide. Below ~0.2 the index is
mostly being suppressed by size, whatever the market's growth.

**Where it misleads.** The container anchor is a trading-desk convention,
not a law: a buyer of 25 t/yr of a high-value cut and one of 25 t/yr of
low-value offal score the same. It also treats all categories' tons as
alike, so at `category`/`overall` level a ton of canned beef and a ton of
frozen cuts count equally. The ranking is robust to the exact anchor (see
[Step 16](#step-16--opportunity_score-the-opportunity-index)), but the
index *value* is not.

---

## Step 15 — `attractiveness`

**Question.** Setting tonnage and share aside, how good does this market
look compared with the others in this ranking?

**Formula.** A weighted sum of **percentile ranks within the ranked set**.
`pct` is the rank, from 0 to 1, among the groups this query returns; a
missing value gets 0.5, the neutral middle.

```
attractiveness = 0.30 x pct(annual_growth_pct)                                         -- long-run momentum
               + 0.30 x (0.6 x pct(recent_growth_pct) + 0.4 x pct(recent_consistency)) -- recent momentum
               + 0.20 x pct(log10(total_fob_usd / years_active))                       -- size, diminishing returns
               + 0.20 x pct(price_trend_pct)                                           -- rising price
```

The weights are `INDEX_WEIGHTS`, `RECENT_GROWTH_WEIGHT` and
`RECENT_CONSISTENCY_WEIGHT` in the code.

**Worked example.** Singapore, among the 52 frozen-liver countries:

| Part | Input | Percentile |
| --- | --- | --- |
| long-run momentum | +151.6%/yr | 1.000 (the highest) |
| recent growth | +75.3%/yr | 0.667 |
| recent consistency | 0.667 | 0.716 |
| size | log10(308,611 / 8) = 4.586 | 0.827 |
| price trend | -6.3%/yr | 0.192 |

```
  0.30 x 1.000                         = 0.300
+ 0.30 x (0.6 x 0.667 + 0.4 x 0.716)   = 0.30 x 0.686 = 0.206
+ 0.20 x 0.827                         = 0.165
+ 0.20 x 0.192                         = 0.038
                                       = 0.710
```

For comparison, Libya scores 0.799 and Egypt 0.825, the highest in the
set.

**How to read it.** It runs from 0 to 1, where 1 would be the best in the
set on every part. Percentiles make it **scale-free**: growth in percent,
dollars, and $/t trends can be combined without any one extreme value (a
+500%/yr outlier, an $84M giant) swamping the rest. Size enters on a log
scale, so a market ten times bigger gains a fixed step, not ten times the
credit.

**Where it misleads.** It is **relative to the ranked set**, so the same
market gets a different value in a different query. Singapore is 0.710
among frozen-liver buyers, 0.730 among `category = offal` buyers (#2
there, index 67.62), and 0.509 among buyers of all beef (#47, index
46.78). Compare `attractiveness` and `opportunity_score` within one result
only, never across two. Percentiles also discard distance: the top grower
gets 1.0 whether it beats the next one by 1 point or by 100.

---

## Step 16 — `opportunity_score`: the opportunity index

**Question.** Where is there momentum, room to grow, a market worth
serving, and evidence to back it, all at once?

**Formula.** Four factors, multiplied, on a 0–100 scale:

```
opportunity_score = 100 x attractiveness x headroom x materiality x evidence

attractiveness = Step 15
headroom       = 1 - share_pct               -- only the part not already Brazil's counts
materiality    = Step 14
evidence       = 0.5 + 0.5 x confidence      -- thin evidence halves the index
```

Multiplying, not adding, makes each factor a gate: a market has to be
good on all four to score high. Headroom is a multiplier, not a
percentile, so a saturated market's size counts only for the part not
already Brazil's. Evidence runs from 0.5 to 1, so zero confidence halves
the index rather than zeroing it: weak evidence is a reason for caution,
not proof there is nothing there.

**Worked example.** Singapore:

```
100 x 0.709729 x (1 - 0.002146) x 0.476744 x (0.5 + 0.5 x 0.855584)
= 100 x 0.709729 x 0.997854 x 0.476744 x 0.927792
= 31.33      →  #7 of 52
```

| Country | Attractiveness | Headroom | Materiality | Evidence | Index | Rank (old score) |
| --- | --- | --- | --- | --- | --- | --- |
| Libya | 0.799 | 0.987 | 0.904 | 0.851 | **60.66** | #1 (#8) |
| Singapore | 0.710 | 0.998 | 0.477 | 0.928 | **31.33** | #7 (#1) |
| Egypt | 0.825 | 0.387 | 0.996 | 0.938 | **29.84** | #8 (#22) |
| Guyana | 0.707 | 0.999 | 0.211 | 0.760 | **11.34** | #13 (#2) |

Singapore has the set's top growth but loses half its index to size.
Egypt has the best attractiveness in the set and is the biggest buyer
($30.1M in the latest 12 months, up from $5.1M two periods earlier), but
61% of it is already Brazil's, so headroom holds it at #8. Guyana's frozen
livers, 26.8 t across the whole 10-year window, fall from #2 to #13.

**How to read it.** It is a **ranking key**: higher means a better lead
within this result. Then read the factors. They say *why* a market ranks
where it does, and which one to look at next. A low materiality says "too
small today", a low headroom says "already ours", and a low evidence says
"wait for more data".

What the change fixed, on the default all-beef → countries view (161
countries): the old `growth x (1 - share)` score was effectively just the
growth rate, since every market but China had under 12% share. Size
played no part: Vanuatu ranked #5 on $84,557 over six years, Bulgaria #9
on $14,144 over four, and Palau #12. Confidence never moved the rank
either (Bangladesh was #7 at 0.0 confidence). The biggest markets sank,
with the US at #39 and China at #77. Under the index, Mexico is #1 and the
Philippines #2, and the US is #21. China is #91: the biggest market, but
53.1% already Brazil's. Vanuatu, Palau and Bulgaria are #150, #155 and
#158; Bangladesh is #85.

Robustness, on that same view: the top 10 keeps 9 of its 10 countries
under equal weights (0.25 each), 8 of 10 with the size weight doubled to
0.40, 9 of 10 with a 12.5 t container anchor and 10 of 10 with a 50 t
anchor. Dropping the price trend changes 4 of the 10. It is kept
deliberately (see `docs/decisions/0006-opportunity-index.md`).

**Where it misleads.** Multiplying four factors **compresses the scale**.
The frozen-liver indexes run from 0.005 to 60.66 with a median of 1.98,
while the all-beef view's run from 0.09 to 85.90 with a median of 33.17.
"31 vs. 60" means "ranks lower", not "half as good", and a value means
nothing outside its own query, because attractiveness is percentile-based
(see Step 15). The index also inherits every input's failure mode: above
all, a short recent window, a three-change consistency score and
mix-driven price trends. It is a lead generator, not a forecast.

---

## Step 17 — `unit_price_usd_per_ton`

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

**How to read it.** A descriptive column, not an index input (its *trend*
is an input: see [`price_trend_pct`](#step-12--price_trend_pct)). Useful
for sanity-checking a market (is it paying premium or discount prices?)
and for spotting product-mix differences between destinations.

**Where it misleads.** ComexStat has no per-shipment prices, only monthly
aggregates, so this is a proxy for an average realised price — it hides
mix, incoterms, and product quality within an NCM code. The
sum-then-divide rule is not a preference; **divide-then-average is
arithmetically wrong** for a weighted quantity, and the demonstration
above is why.

---

## Reading a result: read the factors, not just the rank

The index puts the four questions a trader asks into one number, so the
useful move is to take it apart again. Three frozen-liver rows show the
typical shapes:

| | Libya | Singapore | Egypt |
| --- | --- | --- | --- |
| Rank | **#1** | #7 | #8 |
| `opportunity_score` | 60.66 | 31.33 | 29.84 |
| `attractiveness` | 0.799 | 0.710 | 0.825 |
| `share_pct` | 1.3% | 0.21% | 61.3% |
| `tons_per_year` | 235.7 | 22.8 | 5,722.5 |
| `materiality` | 0.904 | 0.477 | 0.996 |
| `confidence` | 0.702 | 0.856 | 0.876 |
| `years_active` | 4 | 8 | 9 |

- **Libya** is the all-round lead. It is growing (+63.6%/yr long-run,
  +120.7%/yr recently, three rises in a row), buys about nine containers a
  year, and is barely served. Its weak spot is history: four active
  periods, so `coverage_score` is 0.4.
- **Singapore** is a fast grower too small to matter yet. The index is
  saying "watch it", not "chase it".
- **Egypt** is attractive and huge but already mostly Brazil's. The index
  ranks defence below expansion, which is the intended reading.

The intended workflow:

1. **The index** narrows 113 frozen-liver buyers to a shortlist.
2. **Its factors** say why each entry is there, and what would change it.
3. **Confidence** and its legs say how much evidence backs each one.
4. **The period-by-period history** (Explain page) shows whether the trend
   is still intact.

The index is a lead generator for manual research, never the decision.

---

## Known distortions in the current data

Real issues in the output as it stands today, not hypotheticals.

### Percentiles are relative to the ranked set

The same market gets a different `attractiveness`, and so a different
index, in every query. Singapore is 31.33 (#7) for frozen livers, 67.62
(#2) for offal, and 46.78 (#47) for all beef. Adding or removing one
market from a ranked set, for example one crossing the 4-period floor
when a new month shifts the periods, moves every other market's
percentiles too. Compare index values within one result only.

### Recent consistency rests on three changes, or fewer

`recent_consistency` can only take the values 0, ⅓, ½, ⅔ and 1, and a
market with gaps in its last four periods may have it decided by a single
change: Qatar's 0.0 for frozen livers is one drop. It carries 12% of
`attractiveness` (0.30 x 0.4), enough to move a close call.

### A rising $/t can be a mix shift

`price_trend_pct` cannot tell a market paying more for the same product
from one buying more of the expensive product. Below NCM level ComexStat
has no product detail, and at `category`/`overall` level the mix effect
grows. Unweighted periods also let small early lots dominate the slope
(Singapore's -6.3%/yr). Check the history before reading a price trend as
pricing power.

### The index is compressed; read its factors

Multiplying four 0–1 factors pushes many values toward the bottom of the
scale: the frozen-liver median index is 1.98 out of 100. The index is a
ranking key. How far apart two markets really are, and why, is in their
factors.

### Turkey's transit flows are unaffected

Brazil reports $124,038,703 of frozen boneless beef (`02023000`) to Turkey
in calendar 2024. Turkey's own statistics report about $5.25M from Brazil
(UN Comtrade, outside this database; see `docs/ROADMAP.md`). ComexStat
records the *declared* destination, so transit and free-zone flows count
as Turkish demand. The index does nothing about this: Turkey ranks #16 of
130 for boneless beef with an index of 57.51, on 13,026 t/yr (materiality
0.998). Treat its score as suspect until the destination-side check is
done.

### Trimming can reveal a market reopening (Mexico)

The [leading trim](#step-2--years_active-the-leading-trim-and-the--4-floor)
compares each period against the group's *median*, so when a market's
recent volume dwarfs its old volume, old-but-real periods can fall under
1% and be trimmed. Boneless beef (`02023000`) to Mexico: two periods of
~$40–50k early in the window, a four-year gap, then a steep ramp. Those
early periods are under 1% of today's median, so they are trimmed and the
fit sees only the recent ramp — +513%/yr instead of +205%/yr. Mexico is
#1 for boneless beef (index 75.57).

That is arguably the right reading (a gap followed by a ramp is a market
opening, and the old trade was a different regime), but it is a judgement
the formula makes silently. Any group with a multi-year gap inside the
window deserves a look at its raw history before its growth is trusted.

### `K`'s bar is low

At `K = $52,119` for the `ncm_code x country` grid, a market averaging
$39k/year clears `volume_confidence = 0.86`. The formula is behaving as
designed — the median product-country pair really is that small — but the
label "volume confidence" oversells it. Under the old score this let tiny
markets top the ranking. The index's `materiality` factor now supplies the
commercial floor, so the low bar only nudges `confidence`, and so
`evidence`, up for small markets.

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
| `recent_growth_pct` | yearly growth over the last 4 periods | % (unbounded) |
| `recent_consistency` | share of the last 3 year-on-year changes that were up | 0–1 |
| `price_trend_pct` | yearly change in the average price per ton | % (unbounded) |
| `tons_per_year` | average tons a year, in years with sales | tons/year |
| `materiality` | commercial size: tons a year vs. one 25-ton container | 0–1 |
| `attractiveness` | growth, recent momentum, size and price trend, ranked | 0–1, relative to the ranked set |
| `opportunity_score` | 0-100 index: attractiveness x headroom x materiality x evidence | 0–100 ranking key |
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
   together beats seventeen unrelated ones.
4. Say plainly whether it feeds the **index** (and which factor), the
   **confidence**, or is **descriptive only**. If it feeds
   `attractiveness`, note that a new part reweights every existing one.
5. Fill in **Where it misleads** honestly. Every metric here has a failure
   mode; a section without one is an unfinished section.

Then add a row to [Quick reference](#quick-reference), in the same position
as the column in `_OUTPUT_COLUMNS`, and the same
one-line meaning to `METRIC_GLOSSARY` in `analysis/opportunity_scoring.py`
— the app shows it as that column's header tooltip. A test compares the
two word for word, so the dashboard and this document cannot drift apart.
