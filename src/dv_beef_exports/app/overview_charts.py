"""
Charts for the Market overview page's analysis sections - pure functions
returning Altair charts (schema-checked in tests; style_chart() is applied by
the page). Colour jobs follow the dataviz skill: one series = one hue and no
legend box; growth vs decline = blue vs orange (colour-blind safe, never
green/red), always with a sign in the label too; context in grey.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from dv_beef_exports.app.common import (
    BLUE,
    BLUE_DARK,
    CHART_INK,
    CHART_MUTED,
    DOWN,
    GREY,
    INK,
    MONEY_AXIS_LABELS,
    ORANGE,
    TONS_AXIS_LABELS,
    UP,
    period_label,
)
from dv_beef_exports.app.explanations import money

_THIS_YEAR, _YEAR_BEFORE = "Latest 12 months", "Same month, a year earlier"


def _title(text: str, subtitle: str | list[str] = "") -> alt.TitleParams:
    return alt.TitleParams(text=text, subtitle=subtitle, anchor="start")


def signed_money(usd: float) -> str:
    """+$525M / -$5.7M - the sign carries the direction, not just the colour."""
    return ("+" if usd >= 0 else "-") + money(abs(usd))


def monthly_chart(monthly: pd.DataFrame) -> alt.LayerChart:
    """The latest 12 months against the same months a year earlier: two
    lines on one value axis, the latest in blue, the year before in grey,
    both named at their last point."""
    rows = []
    for _, r in monthly.iterrows():
        label = f"{r['month']:%b '%y}"
        rows.append({"month": label, "series": _THIS_YEAR, "fob_usd": r["fob_usd"]})
        rows.append({"month": label, "series": _YEAR_BEFORE, "fob_usd": r["prev_fob_usd"]})
    data = pd.DataFrame(rows)
    order = list(dict.fromkeys(data["month"]))
    yoy = monthly.assign(month=[f"{m:%b '%y}" for m in monthly["month"]])
    colors = alt.Scale(domain=[_THIS_YEAR, _YEAR_BEFORE], range=[BLUE, GREY])
    x = alt.X("month:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True))
    lines = (
        alt.Chart(data)
        .mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(size=36, filled=True))
        .encode(
            x=x,
            y=alt.Y(
                "fob_usd:Q",
                title="Value shipped in the month (USD)",
                axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS),
            ),
            color=alt.Color(
                "series:N",
                scale=colors,
                legend=alt.Legend(orient="top", title=None, symbolType="stroke"),
            ),
            # the year before sits behind
            order=alt.Order("series:N", sort="descending"),
        )
    )
    # one tooltip per month with both values and the change, on a wide hit area
    hover = (
        alt.Chart(yoy)
        .mark_bar(opacity=0)
        .encode(
            x=x,
            y=alt.Y("fob_usd:Q"),
            tooltip=[
                alt.Tooltip("month:N", title="Month"),
                alt.Tooltip("fob_usd:Q", title=_THIS_YEAR, format="$,.0f"),
                alt.Tooltip("prev_fob_usd:Q", title="A year earlier", format="$,.0f"),
                alt.Tooltip("fob_yoy_pct:Q", title="Change", format="+.0%"),
            ],
        )
    )
    return (lines + hover).properties(
        title=_title(
            "Month by month, against a year earlier",
            "Seasonality and the latest momentum that 12-month totals smooth away",
        ),
        height=300,
    )


def bridge_chart(bridge: dict) -> alt.LayerChart:
    """A waterfall from the previous 12 months' value to the latest: the
    change split into its volume effect and its price effect."""
    previous = period_label(bridge["previous_start"], bridge["previous_end"])
    latest = period_label(bridge["latest_start"], bridge["latest_end"])
    vol, price = bridge["volume_effect"], bridge["price_effect"]
    start = bridge["previous"]
    steps = [
        (previous, 0.0, start, "total", money(start)),
        ("Volume effect", start, start + vol, "up" if vol >= 0 else "down", signed_money(vol)),
        (
            "Price effect",
            start + vol,
            start + vol + price,
            "up" if price >= 0 else "down",
            signed_money(price),
        ),
        (latest, 0.0, bridge["latest"], "latest", money(bridge["latest"])),
    ]
    data = pd.DataFrame(steps, columns=["step", "low", "high", "kind", "label"])
    data["top"] = data[["low", "high"]].max(axis=1)
    order = list(data["step"])
    x = alt.X("step:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelLimit=160))
    bars = (
        alt.Chart(data)
        .mark_bar(cornerRadius=4, size=56)
        .encode(
            x=x,
            y=alt.Y(
                "low:Q",
                title="Value in the 12-month period (USD)",
                axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS),
            ),
            y2="high:Q",
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(
                    domain=["total", "up", "down", "latest"], range=[GREY, UP, DOWN, BLUE_DARK]
                ),
                legend=None,  # every bar is named on the axis and labelled
            ),
            tooltip=[
                alt.Tooltip("step:N", title=" "),
                alt.Tooltip("label:N", title="Value"),
            ],
        )
    )
    labels = (
        alt.Chart(data)
        .mark_text(dy=-8, fontSize=12, fontWeight=500, color=INK)
        .encode(x=x, y="top:Q", text="label:N")
    )
    return (bars + labels).properties(
        title=_title(
            "What moved the value",
            [
                f"Volume effect: the change in tons, at the old ${bridge['old_price']:,.0f}/t",
                f"Price effect: the change to ${bridge['new_price']:,.0f}/t, on the new tons",
            ],
        ),
        height=300,
    )


def movers_chart(movers: pd.DataFrame, destination_noun: str) -> alt.LayerChart:
    """Biggest gains (blue, right) and losses (orange, left) in value,
    largest at the top, each labelled with its signed change."""
    data = movers.assign(
        direction=["Gain" if c > 0 else "Loss" for c in movers["change"]],
        label=[
            f"{signed_money(c)}" + ("" if pd.isna(p) else f" ({p:+.0%})")
            for c, p in zip(movers["change"], movers["change_pct"], strict=True)
        ],
        # losses are often slivers next to the gains: label them just right
        # of zero (their row has no bar there) rather than over the names
        label_at=movers["change"].clip(lower=0),
    ).sort_values("change", ascending=False)
    order = list(data["destination"])
    base = alt.Chart(data).encode(
        y=alt.Y(
            "destination:N",
            sort=order,
            title=None,
            axis=alt.Axis(domain=False, labelLimit=170, labelColor=INK),
        )
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.66}).encode(
        x=alt.X(
            "change:Q",
            title="Change in value vs the previous 12 months (USD)",
            axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS),
        ),
        color=alt.Color(
            "direction:N",
            scale=alt.Scale(domain=["Gain", "Loss"], range=[UP, DOWN]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=[
            alt.Tooltip("destination:N", title=destination_noun.capitalize()),
            alt.Tooltip("prev_fob_usd:Q", title="Previous 12 months", format="$,.0f"),
            alt.Tooltip("fob_usd:Q", title="Latest 12 months", format="$,.0f"),
            alt.Tooltip("label:N", title="Change"),
        ],
    )
    labels = base.mark_text(align="left", dx=5, fontSize=11, color=CHART_INK).encode(
        x="label_at:Q", text="label:N"
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=CHART_MUTED).encode(x="x:Q")
    return (bars + zero + labels).properties(
        title=_title(
            f"Which {destination_noun}s drove the change",
            "Biggest gains and losses in value, latest 12 months vs the 12 before",
        ),
        height=alt.Step(26),
    )


def price_volume_chart(pv: pd.DataFrame, average_price: float, labelled: int = 5) -> alt.LayerChart:
    """Premium vs bulk buyers: tons a year (log scale) against average $/t,
    one dot per destination, with the scope's average price as a reference
    line. The biggest buyers are named."""
    data = pv.assign(
        name=[d if i < labelled else "" for i, d in enumerate(pv["destination"])],
        position=[
            "Pays above average" if p >= 1 else "Pays below average" for p in pv["price_vs_average"]
        ],
    )
    x = alt.X(
        "metric_ton:Q",
        title="Tons in the latest 12 months (log scale)",
        scale=alt.Scale(type="log", nice=False, padding=16),
        axis=alt.Axis(labelExpr=TONS_AXIS_LABELS, tickCount=5, grid=False),
    )
    y = alt.Y(
        "unit_price_usd_per_ton:Q",
        title="Average price (USD per ton)",
        scale=alt.Scale(zero=False, padding=14),
        axis=alt.Axis(format="$,.0f"),
    )
    dots = (
        alt.Chart(data)
        .mark_circle(opacity=0.9, stroke="white", strokeWidth=1.5)
        .encode(
            x=x,
            y=y,
            size=alt.Size(
                "fob_usd:Q",
                scale=alt.Scale(range=[60, 900]),
                legend=None,  # size is secondary: the tooltip carries the value
            ),
            color=alt.Color(
                "position:N",
                scale=alt.Scale(
                    domain=["Pays above average", "Pays below average"], range=[BLUE, ORANGE]
                ),
                legend=alt.Legend(orient="top", title=None),
            ),
            tooltip=[
                alt.Tooltip("destination:N", title="Destination"),
                alt.Tooltip("fob_usd:Q", title="Value", format="$,.0f"),
                alt.Tooltip("metric_ton:Q", title="Tons", format=",.0f"),
                alt.Tooltip("unit_price_usd_per_ton:Q", title="Average price", format="$,.0f"),
                alt.Tooltip("price_vs_average:Q", title="vs average", format=".2f"),
                alt.Tooltip("price_yoy_pct:Q", title="Price vs a year earlier", format="+.0%"),
            ],
        )
    )
    names = (
        alt.Chart(data)
        .mark_text(align="left", dx=9, dy=-7, fontSize=11, color=CHART_INK)
        .encode(x=x, y=y, text="name:N")
    )
    reference = alt.Chart(pd.DataFrame({"y": [average_price]}))
    rule = reference.mark_rule(color=CHART_MUTED, strokeDash=[4, 4]).encode(y="y:Q")
    return (rule + dots + names).properties(
        title=_title(
            "Premium vs bulk buyers",
            [
                f"Dashed line = the average price, ${average_price:,.0f}/t",
                "Bigger dot = more value; the biggest buyers are named",
            ],
        ),
        height=380,
    )


def concentration_chart(concentration: pd.DataFrame) -> alt.LayerChart:
    """The top buyer's and the top 5 buyers' share of value per 12-month
    period - two lines on one 0-100% axis, named at their last point."""
    labels = [
        period_label(s, e)
        for s, e in zip(concentration["period_start"], concentration["period_end"], strict=True)
    ]
    rows = []
    for label, (_, r) in zip(labels, concentration.iterrows(), strict=True):
        rows.append({"period": label, "series": "Top 5 buyers", "share": r["top5_share"]})
        rows.append(
            {
                "period": label,
                "series": "Top buyer",
                "share": r["top1_share"],
                "who": r["top_destination"],
            }
        )
    data = pd.DataFrame(rows)
    last = data[data["period"] == labels[-1]].assign(
        text=lambda d: [f"{s} {v:.0%}" for s, v in zip(d["series"], d["share"], strict=True)]
    )
    x = alt.X("period:N", sort=labels, title=None, axis=alt.Axis(labelAngle=-40))
    y = alt.Y(
        "share:Q",
        title="Share of the value",
        scale=alt.Scale(domain=[0, 1]),
        axis=alt.Axis(format="%"),
    )
    colors = alt.Scale(domain=["Top 5 buyers", "Top buyer"], range=[BLUE, ORANGE])
    lines = (
        alt.Chart(data)
        .mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(size=36, filled=True))
        .encode(
            x=x,
            y=y,
            color=alt.Color("series:N", scale=colors, legend=alt.Legend(orient="top", title=None)),
            tooltip=[
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("series:N", title=" "),
                alt.Tooltip("share:Q", title="Share", format=".0%"),
                alt.Tooltip("who:N", title="Top buyer"),
            ],
        )
    )
    end_labels = (
        alt.Chart(last)
        .mark_text(align="left", dx=10, fontSize=11, fontWeight=500, color=CHART_INK)
        .encode(x=x, y=y, text="text:N")
    )
    return (lines + end_labels).properties(
        title=_title(
            "How dependent on the biggest buyers",
            "Share of the value taken by the top buyer and the top 5, each 12-month period",
        ),
        height=300,
        # room right of the plot for the end labels
        padding={"left": 5, "top": 5, "bottom": 5, "right": 110},
    )
