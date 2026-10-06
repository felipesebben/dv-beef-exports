"""
Plain-language explanations of one opportunity-scoring result row, for the
"Explain the numbers" page.

The reader is a business user, not an analyst: no R², no log-linear fits.
Every metric is explained three ways - what it means, what it says for the
user's own selection (with the real numbers), and what to do about it -
and the action is chosen by which band the value falls in. The bands mirror
the readings in docs/analysis-methodology.md.

Pure Python, no Streamlit, so every sentence is unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Selection:
    """Who and what is being explained, in words.

    mode: "markets" (a product is fixed, markets are ranked) or "products"
        (a market is fixed, products are ranked).
    fixed_label: the fixed side, e.g. "Livers, frozen", "all beef",
        "Vietnam", "all markets".
    subject_label: the ranked item being explained, e.g. "Singapore".
    ranked_noun: what the ranked items are, singular, e.g. "country".
    rank / n_ranked: the subject's position by opportunity score.
    window_label: e.g. "Sep 2016 - Aug 2026".
    """

    mode: str
    fixed_label: str
    subject_label: str
    ranked_noun: str
    rank: int
    n_ranked: int
    window_years: int
    window_label: str

    @property
    def product(self) -> str:
        return self.fixed_label if self.mode == "markets" else self.subject_label

    @property
    def market(self) -> str:
        return self.subject_label if self.mode == "markets" else self.fixed_label

    @property
    def flow(self) -> str:
        """e.g. "Brazil's exports of Livers, frozen to Singapore"."""
        return f"Brazil's exports of {self.product} to {self.market}"


@dataclass(frozen=True)
class MetricExplanation:
    key: str
    title: str
    value: str
    meaning: str
    for_you: str
    action: str
    caveat: str | None = None


def money(usd: float) -> str:
    """$524, $9,083, $27.3k, $309k, $84.0M, $17.5B."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:.1f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:.1f}M"
    if abs(usd) >= 1e5:
        return f"${usd / 1e3:.0f}k"
    if abs(usd) >= 1e4:
        return f"${usd / 1e3:.1f}k"
    return f"${usd:,.0f}"


def tons(t: float) -> str:
    """4.3 t, 182 t, 27,533 t, 3.15 million t."""
    if t >= 1e6:
        return f"{t / 1e6:.2f} million t"
    return f"{t:,.1f} t" if t < 10 else f"{t:,.0f} t"


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def share_text(s: float) -> str:
    """A share for sentences: "under 0.1%" rather than a misleading "0.0%"."""
    if 0 < s < 0.001:
        return "under 0.1%"
    return pct(s, 1 if s < 0.1 else 0)


def stalled_since(history: pd.DataFrame) -> pd.Timestamp | None:
    """End of the last period with any exports, if that isn't the latest
    period - i.e. nothing was sold in the most recent 12 months."""
    latest = history.iloc[-1]
    if latest["fob_usd"] > 0:
        return None
    sold = history[history["fob_usd"] > 0]
    return sold["period_end"].iloc[-1] if not sold.empty else None


def plural(noun: str) -> str:
    """country -> countries, product category -> product categories."""
    return noun[:-1] + "ies" if noun.endswith("y") else noun + "s"


def _cap(text: str) -> str:
    """Capitalise a sentence-initial label ("frozen livers" -> "Frozen livers")."""
    return text[:1].upper() + text[1:]


def implied_typical_size(total_fob_usd: float, volume_confidence: float) -> float:
    """K, the median group size volume_confidence is measured against,
    recovered from volume_confidence = total / (total + K)."""
    return total_fob_usd * (1 - volume_confidence) / volume_confidence


def _confidence_legs(row: pd.Series) -> dict[str, float]:
    return {
        "history (how many years it bought)": row["coverage_score"],
        "steadiness of the trend": row["trend_r2_adj"],
        "size compared with a typical market": row["volume_confidence"],
    }


def explain_growth(row: pd.Series, sel: Selection, history: pd.DataFrame) -> MetricExplanation:
    g = row["annual_growth_pct"]
    fitted = history[history["in_fit"]]
    first, last = fitted.iloc[0], fitted.iloc[-1]
    span = (
        f"from {money(first['fob_usd'])} in the 12 months to "
        f"{first['period_end']:%b %Y} to {money(last['fob_usd'])} in the 12 months to "
        f"{last['period_end']:%b %Y}"
    )
    if g >= 0.5:
        action = (
            "Fast growth. Worth understanding *why* - a new trade agreement, a "
            "competitor leaving, a new distributor?"
        )
        if first["fob_usd"] < 1e5:
            action += (
                " Growth from a small base can reverse quickly, so check the trend "
                "steadiness below."
            )
    elif g >= 0.1:
        action = (
            "Healthy, steady expansion - the kind of market that rewards a sustained sales effort."
        )
    elif g >= 0:
        action = "Roughly flat. Demand is stable but not expanding - a defend-and-maintain market."
    else:
        action = (
            "Shrinking. Unless you know a reason it will recover, this is not a growth "
            "lead right now - find out whether buyers moved to another supplier."
        )
    stalled = stalled_since(history)
    if stalled is not None:
        action = (
            f"**Nothing was sold in the latest 12 months** - the last sale was in the "
            f"12 months to {stalled:%b %Y}. The growth rate describes a trade that may "
            "already have stopped: find out why before treating it as a lead."
        )
    return MetricExplanation(
        key="annual_growth_pct",
        title="Growth per year",
        value=f"{g * 100:+.0f}%/yr",
        meaning=(
            "How fast this trade has been growing, as an average yearly rate over the "
            "whole window - not just the change between the last two years."
        ),
        for_you=(f"{sel.flow} grew by about **{g * 100:+.0f}% a year** on average, {span}."),
        action=action,
        caveat=(
            "A percentage says nothing about size: going from $500 to $1,000 is +100%, "
            "same as $5M to $10M. Always read it next to the size figures."
        ),
    )


def explain_trend_fit(row: pd.Series, sel: Selection) -> MetricExplanation:
    f = row["trend_r2_adj"]
    if f >= 0.7:
        band = "steady"
        action = (
            "The growth figure is reliable - sales have climbed along a consistent "
            "path, not in one lucky burst."
        )
    elif f >= 0.3:
        band = "bumpy"
        action = (
            "The direction is real but the path is uneven - some years well above or "
            "below the trend. Look at the chart for one-off spikes or a recent drop "
            "before trusting the growth rate."
        )
    else:
        band = "erratic"
        action = (
            "Sales jump around too much for the growth figure to mean much. Treat it as "
            "noise and judge this market on its recent years instead."
        )
    return MetricExplanation(
        key="trend_r2_adj",
        title="Trend steadiness",
        value=f"{f:.2f}",
        meaning=(
            "How closely the year-by-year sales follow one smooth growth line, from 0 "
            "(no pattern at all) to 1 (a perfectly steady path). Technically an "
            "adjusted R², which also discounts markets with only a few years of data."
        ),
        for_you=(
            f"At **{f:.2f}**, the growth of {sel.flow} has been **{band}**. The chart "
            "shows each 12-month period against the trend line the growth rate comes from - "
            "the closer the bars sit to the line, the higher this number."
        ),
        action=action,
    )


def explain_share(row: pd.Series, sel: Selection) -> MetricExplanation:
    s = row["share_pct"]
    if sel.mode == "markets":
        for_you = (
            f"In the latest 12 months, {sel.market} took **{share_text(s)}** of "
            f"Brazil's total exports of {sel.product}."
        )
        if s < 0.05:
            action = (
                "A small slice - almost all of this market's potential is still untapped by Brazil."
            )
        elif s < 0.25:
            action = (
                "A meaningful but not dominant position - room to grow alongside existing trade."
            )
        else:
            action = (
                "Already one of Brazil's biggest outlets for this product. Growth here is "
                "mostly *defending* a position, not opening a new one."
            )
    else:
        for_you = (
            f"In the latest 12 months, {sel.product} made up **{share_text(s)}** of "
            f"everything Brazil sold to {sel.market} (among the tracked beef products)."
        )
        if s < 0.05:
            action = "A minor line for this market - room to grow it if demand is there."
        elif s < 0.25:
            action = "An established line, with room to grow."
        else:
            action = "Already a core product for this market - focus on keeping it."
    return MetricExplanation(
        key="share_pct",
        title="Share (room to grow)",
        value=share_text(s),
        meaning=(
            "How much of the trade is already covered. A low share means more headroom; "
            "a high share means the position is already won."
        ),
        for_you=for_you,
        action=action,
        caveat=(
            "This is a share of *Brazil's* exports only - the data can't see what this "
            "market buys from other countries."
        ),
    )


# an index factor at or above this isn't worth calling out as a weakness
STRONG_FACTOR = 0.85


def index_factors(row: pd.Series) -> dict[str, float]:
    """The four factors the opportunity index multiplies, each 0-1."""
    return {
        "attractiveness": row["attractiveness"],
        "room to grow": 1 - row["share_pct"],
        "commercial size": row["materiality"],
        "evidence": 0.5 + 0.5 * row["confidence"],
    }


def explain_score(row: pd.Series, sel: Selection) -> MetricExplanation:
    score = row["opportunity_score"]
    factors = index_factors(row)
    weakest = min(factors, key=factors.get)
    top = sel.rank <= max(5, round(sel.n_ranked * 0.1))
    held_back = {
        "attractiveness": "its growth, momentum, size or price trend rank low against the rest",
        "room to grow": "Brazil already holds much of this trade",
        "commercial size": "the volumes are small - few containers a year",
        "evidence": "the evidence behind the trend is thin",
    }[weakest]
    # naming a "weakest" factor only helps when it's actually weak
    if factors[weakest] >= STRONG_FACTOR:
        limit = "every factor is strong - nothing holds it back much"
    else:
        limit = f"what holds it back most is {weakest} - {held_back}"
    if top:
        action = f"Ranked **#{sel.rank} of {sel.n_ranked}** - a shortlist candidate: {limit}."
    else:
        action = f"Ranked #{sel.rank} of {sel.n_ranked}: {limit}."
    breakdown = ", ".join(f"{name} **{value:.2f}**" for name, value in factors.items())
    return MetricExplanation(
        key="opportunity_score",
        title="Opportunity index",
        value=f"{score:.0f} / 100",
        meaning=(
            "One 0-100 number to rank by. It multiplies four factors, each from 0 to 1: "
            "**attractiveness** (long-run growth, recent momentum, size and price trend, "
            "each ranked against the rest), **room to grow** (the part not already "
            "Brazil's), **commercial size** (tons a year against one 25-ton container) and "
            "**evidence** (how much to trust the trend). Because they multiply, one weak "
            "factor pulls the whole index down."
        ),
        for_you=f"{_cap(sel.subject_label)} scores **{score:.0f}**: {breakdown}.",
        action=action,
        caveat=(
            'A ranking key, not a forecast: 60 vs 30 means "ranks higher", not "twice as '
            "good\". It's also relative to what's ranked, so the same market can score "
            "differently in another query."
        ),
    )


def explain_recent(row: pd.Series, sel: Selection) -> MetricExplanation:
    g, c = row["recent_growth_pct"], row["recent_consistency"]
    if pd.isna(g):
        for_you = (
            f"{sel.flow} didn't sell in enough of the last 4 years to measure recent momentum."
        )
        action = "Recent history is too patchy to read - lean on the long-run figures."
        value = "n/a"
    else:
        ups = "" if pd.isna(c) else f", and {pct(c)} of the last year-on-year changes were up"
        for_you = f"Over the last 4 years, {sel.flow} grew about **{g * 100:+.0f}% a year**{ups}."
        long_run = row["annual_growth_pct"]
        if g > long_run and (pd.isna(c) or c >= 0.67):
            action = (
                "Accelerating, and consistently - the momentum is recent and real. The "
                "strongest kind of signal for a lead."
            )
        elif g > 0 and (pd.isna(c) or c >= 0.67):
            action = "Still growing steadily in recent years - the trend is holding."
        elif g > 0:
            action = "Growing recently, but unevenly - check the yearly bars before relying on it."
        else:
            action = (
                "Recent years are flat or falling, whatever the long-run rate says - find out "
                "what changed."
            )
        value = f"{g * 100:+.0f}%/yr"
    return MetricExplanation(
        key="recent_growth_pct",
        title="Recent momentum",
        value=value,
        meaning=(
            "Growth over just the last 4 years, and how consistently it rose. It rewards "
            "growth that is recent and steady over growth that happened years ago."
        ),
        for_you=for_you,
        action=action,
    )


def explain_coverage(row: pd.Series, sel: Selection) -> MetricExplanation:
    n, c = int(row["years_active"]), row["coverage_score"]
    if c >= 0.8:
        action = "A consistent buyer - the history is long enough to trust."
    elif c >= 0.5:
        action = (
            "Bought in some years and not others, or started partway through. Check the "
            "chart: a recent start is a newer market; gaps mean it buys on and off."
        )
    else:
        action = (
            "Only a few years of history. Whatever the growth figure says, it rests on "
            "little evidence - give it time before investing heavily."
        )
    return MetricExplanation(
        key="coverage_score",
        title="History",
        value=f"{n} of {sel.window_years} years",
        meaning=f"In how many of the last {sel.window_years} years this trade actually happened.",
        for_you=(
            f"{sel.flow} happened in **{n} of the last {sel.window_years} years** "
            f"({sel.window_label})."
        ),
        action=action,
        caveat=(
            "Very small first years can be left out of the count, as noise - they "
            'show as light cells marked "Too small to count" on the chart.'
        ),
    )


def explain_volume_confidence(row: pd.Series, sel: Selection) -> MetricExplanation:
    vc, total = row["volume_confidence"], row["total_fob_usd"]
    typical = implied_typical_size(total, vc)
    ratio = total / typical
    if vc >= 0.8:
        size = f"about {ratio:,.0f}x a typical one" if ratio >= 2 else "well above typical"
        action = "Big enough that its numbers aren't just noise."
    elif vc >= 0.5:
        size = "around a typical one"
        action = "Average-sized - the numbers are usable, but small swings can move them."
    else:
        size = "smaller than a typical one"
        action = (
            "Small enough that one or two shipments can swing the growth figure - "
            "treat the trend with caution."
        )
    return MetricExplanation(
        key="volume_confidence",
        title=f"Size vs. a typical {sel.ranked_noun}",
        value=f"{vc:.2f}",
        meaning=(
            "Whether this trade is big enough for its trend to be meaningful, compared "
            f"with a typical {sel.ranked_noun} in this selection (the middle one of all "
            f"{plural(sel.ranked_noun)} with any sales). 0.5 = exactly typical."
        ),
        for_you=(
            f"{sel.flow} totalled **{money(total)}** over the window. A typical "
            f"{sel.ranked_noun} here totals about {money(typical)}, so this one is "
            f"**{size}**."
        ),
        action=action,
        caveat=(
            "This says whether the numbers are *statistically* solid, not whether the "
            "market is *commercially* worth it - most trades are tiny, so "
            "the bar is low. For that, look at the size figures."
        ),
    )


def explain_confidence(row: pd.Series, sel: Selection) -> MetricExplanation:
    c = row["confidence"]
    legs = _confidence_legs(row)
    weakest = min(legs, key=legs.get)
    strongest = max(legs, key=legs.get)
    if c >= 0.7:
        band = "well-evidenced"
        action = "History, steadiness and size all hold up - the score can be trusted as a lead."
    elif c >= 0.4:
        band = "partly evidenced"
        action = (
            f"One piece of evidence is weak: **{weakest}**. Check that before acting - "
            "it may be fine (e.g. a genuinely new market) or a warning sign."
        )
    else:
        band = "thinly evidenced"
        action = (
            "Treat the score as a hypothesis, not a finding - put it on a watch list and "
            "revisit as more data comes in."
        )
    return MetricExplanation(
        key="confidence",
        title="Confidence",
        value=pct(c),
        meaning=(
            "How much evidence stands behind the score, combining three things: how many "
            "years the market bought, how steady the trend is, and how big the trade is. "
            "One weak piece pulls the whole thing down. It's an index, not a probability."
        ),
        for_you=(
            f"At **{pct(c)}**, the score for {sel.subject_label} is **{band}**. "
            f"Strongest evidence: {strongest}"
            + (f"; weakest: {weakest}." if legs[weakest] < 0.7 else "; nothing is weak.")
        ),
        action=action,
    )


def explain_size(row: pd.Series, sel: Selection) -> MetricExplanation:
    total, t, n = row["total_fob_usd"], row["total_metric_ton"], int(row["years_active"])
    per_year, tons_year = total / n, row["tons_per_year"]
    containers = tons_year / 25
    if containers >= 10:
        action = "A commercially significant flow - large enough to justify dedicated effort."
    elif containers >= 1:
        action = "A real, regular flow - worth pursuing if it fits your existing routes and buyers."
    else:
        action = (
            "Less than one container a year. Even fast, steady growth here may not justify a "
            "sales effort on its own - the index discounts it heavily."
        )
    container_text = (
        "under one 25-ton container a year"
        if containers < 1
        else f"about **{containers:,.0f} container{'s' if round(containers) != 1 else ''} a year**"
    )
    return MetricExplanation(
        key="total_fob_usd",
        title="Size of the trade",
        value=money(total),
        meaning=(
            "How much was actually sold. The index measures size in tons a year against one "
            "25-ton reefer container: one container a year counts half, ten nearly in full."
        ),
        for_you=(
            f"Over the window, {sel.flow} added up to **{money(total)}** and "
            f"**{tons(t)}** - about {money(per_year)} and {tons(tons_year)} a year, "
            f"{container_text}."
        ),
        action=action,
    )


def explain_unit_price(row: pd.Series, sel: Selection, peers: pd.DataFrame) -> MetricExplanation:
    price, trend = row["unit_price_usd_per_ton"], row["price_trend_pct"]
    peer_median = peers["unit_price_usd_per_ton"].median()
    diff = price / peer_median - 1
    if diff > 0.1:
        position = f"**{pct(diff)} above** the median"
    elif diff < -0.1:
        position = f"**{pct(-diff)} below** the median"
    else:
        position = "**in line with** the median"
    if pd.isna(trend):
        trend_text = "Not enough priced years to measure a trend."
        action = "No price trend to read yet."
    else:
        trend_text = f"The price has moved about **{trend * 100:+.0f}% a year**."
        if trend > 0.02:
            action = (
                "Rising prices - the market is paying more over time, which makes growth "
                "there more valuable."
            )
        elif trend < -0.02:
            action = (
                "Falling prices - growth there may come at thinner margins; check whether "
                "the mix shifted toward cheaper cuts."
            )
        else:
            action = "Prices broadly stable."
    return MetricExplanation(
        key="unit_price_usd_per_ton",
        title="Price",
        value=f"${price:,.0f}/t",
        meaning=(
            "The average price per ton (total value divided by total weight), and how it "
            "has moved year by year. A rising price counts toward the index."
        ),
        for_you=(
            f"{sel.flow} averaged **${price:,.0f} per ton**, {position} of "
            f"${peer_median:,.0f}/t across the {sel.n_ranked} {plural(sel.ranked_noun)} ranked "
            f"in this query. {trend_text}"
        ),
        action=action,
        caveat=(
            "An average across all cuts and qualities within the product code - a change "
            "can be a shift in mix, not new prices."
        ),
    )


def explain_all(
    row: pd.Series, sel: Selection, history: pd.DataFrame, peers: pd.DataFrame
) -> list[MetricExplanation]:
    """Every metric, in the order a reader should take them: the index's
    ingredients (growth, momentum, steadiness, room, size, price), the index
    that combines them, then the evidence behind it."""
    return [
        explain_growth(row, sel, history),
        explain_recent(row, sel),
        explain_trend_fit(row, sel),
        explain_share(row, sel),
        explain_size(row, sel),
        explain_unit_price(row, sel, peers),
        explain_score(row, sel),
        explain_coverage(row, sel),
        explain_volume_confidence(row, sel),
        explain_confidence(row, sel),
    ]


def bottom_line(row: pd.Series, sel: Selection, history: pd.DataFrame) -> str:
    """Two or three sentences a business user can act on."""
    g, c, s = row["annual_growth_pct"], row["confidence"], row["share_pct"]
    per_year = row["total_fob_usd"] / row["years_active"]
    legs = _confidence_legs(row)
    weakest = min(legs, key=legs.get)

    if sel.mode == "markets":
        held = f"{sel.market} takes {share_text(s)} of Brazil's exports of {sel.product}"
    else:
        held = f"it is {share_text(s)} of what Brazil sells to {sel.market}"
    summary = (
        f"**{_cap(sel.subject_label)}** ranks **#{sel.rank} of {sel.n_ranked}**: "
        f"{sel.flow} {'grew' if g >= 0 else 'shrank'} about {abs(g) * 100:.0f}% a year, "
        f"{held}, and confidence is {pct(c)}."
    )
    stalled = stalled_since(history)
    if stalled is not None:
        step = (
            f"**Check before acting** - nothing was sold in the latest 12 months (last sale "
            f"in the 12 months to {stalled:%b %Y}), so the trend may already have stopped."
        )
    elif g <= 0:
        step = "Not a growth lead right now - the trade is shrinking."
    elif s >= 0.25:
        step = (
            "**Established position** - Brazil already holds a large share, so this is "
            "about defending and deepening an existing flow, not opening a new market."
        )
    elif c >= 0.7:
        step = (
            "**Strong lead** - the evidence holds up. Next step: research who the buyers "
            "are and how the price compares with your offer."
        )
    elif c >= 0.4:
        step = (
            f"**Promising but unproven** - the weakest evidence is {weakest}. Validate "
            "that before committing effort."
        )
    else:
        step = "**Hypothesis only** - too little evidence yet. Keep it on a watch list."
    if per_year < 1e5:
        step += f" Mind the scale: about {money(per_year)} a year, small in absolute terms."
    return f"{summary}\n\n{step}"


# --- Market overview page ---------------------------------------------------


def growth_drivers(value_yoy: float, volume_yoy: float, price_yoy: float) -> str:
    """Why value moved, in words: more beef shipped, higher prices, or both.

    Value = volume x price, so on a log scale the two shares add up to the
    whole change: price_share = ln(1 + price) / ln(1 + value).
    """
    v, q, p = f"{value_yoy * 100:+.0f}%", f"{volume_yoy * 100:+.0f}%", f"{price_yoy * 100:+.0f}%"
    both = f"volume {q} and the average price {p}"
    if value_yoy > 0 and volume_yoy > 0 and price_yoy > 0:
        price_share = math.log1p(price_yoy) / math.log1p(value_yoy)
        if price_share > 0.65:
            why = "mostly **higher prices**, not more beef shipped"
        elif price_share < 0.35:
            why = "mostly **more beef shipped**, not higher prices"
        else:
            why = "roughly **half more beef shipped, half higher prices**"
        return f"Value {v}: {both} - so the growth came {why}."
    if value_yoy > 0 and volume_yoy <= 0:
        return f"Value {v} even though volume fell ({q}) - the growth is **all price** ({p})."
    if value_yoy > 0:
        return f"Value {v} even though prices fell ({p}) - the growth is **all volume** ({q})."
    if volume_yoy < 0 and price_yoy < 0:
        return f"Value {v}: {both} - **both** less beef and lower prices."
    if volume_yoy < 0:
        return f"Value {v}: mainly **less beef shipped** ({q}), with the average price {p}."
    return f"Value {v}: mainly **lower prices** ({p}), with volume {q}."


def overview_summary(trend: pd.DataFrame, scope: str) -> str:
    """The overview page's headline: the latest 12 months vs the 12 before,
    with what drove the change. scope reads mid-sentence, e.g.
    "Brazil's exports of frozen livers to all markets"."""
    latest = trend.iloc[-1]
    period = f"the 12 months to {latest['period_end']:%b %Y}"
    if latest["fob_usd"] <= 0:
        return f"Nothing in {period}: {scope} recorded no sales."
    headline = (
        f"In {period}, {scope} came to **{money(latest['fob_usd'])}** "
        f"({tons(latest['metric_ton'])}) across **{int(latest['destinations'])}** "
        f"destination {'country' if latest['destinations'] == 1 else 'countries'}."
    )
    if pd.isna(latest["fob_yoy_pct"]):
        return f"{headline} There were no sales in the 12 months before, so no comparison."
    drivers = growth_drivers(latest["fob_yoy_pct"], latest["ton_yoy_pct"], latest["price_yoy_pct"])
    return f"{headline}\n\n{drivers}"


def concentration_note(
    matrix: pd.DataFrame,
    destination_noun: str,
    share_column: str = "share_of_total",
    basis: str = "value",
) -> str | None:
    """A warning when one destination dominates - a dependency risk worth
    knowing before planning around the totals. None when nothing dominates.
    share_column / basis: which share to judge by ("value" or "volume")."""
    shares = (
        matrix.groupby("destination", sort=False)[share_column].sum().sort_values(ascending=False)
    )
    if shares.empty:
        return None
    top, share = shares.index[0], shares.iloc[0]
    if share < 0.3:
        return None
    return (
        f"**{top} alone took {pct(share)}** of everything in scope by {basis}. Totals and "
        "trends "
        f"here largely reflect that one {destination_noun} - a change in its demand moves "
        "the whole picture."
    )
