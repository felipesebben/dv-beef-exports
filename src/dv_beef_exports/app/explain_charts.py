"""
Charts for the "Explain the numbers" page: for every metric, one CONTEXT
chart (the metric over time, or what it's made of, for the selected result)
and one COMPARISON chart (the metric across the ranked set, the selected
result highlighted).

Dataviz rules applied throughout (see the dataviz skill):
- every chart has a title naming what it shows, and a subtitle saying how
  to read it - single-series charts need no legend;
- comparisons use emphasis: the selected result in blue, every other in
  grey - never a rainbow;
- no dual axes: two measures on different scales are two charts, stacked;
- direct labels are selective (the selected bar, the last point), the
  tooltip carries the rest.

Pure functions returning Altair charts, so each one can be schema-checked
in tests; style_chart() is applied by the page.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import altair as alt
import pandas as pd

from dv_beef_exports.app.common import (
    CHART_INK,
    CHART_MUTED,
    MONEY_AXIS_LABELS,
    TONS_AXIS_LABELS,
    period_label,
)
from dv_beef_exports.app.explanations import money, tons

HIGHLIGHT = "#2a78d6"  # the selected result
PEER = "#cfcdc6"  # everything else
UP, DOWN = "#2a78d6", "#e34948"  # diverging pair: growth vs decline
EMPHASIS = "#eb6834"  # the one bar the story is about (weakest evidence)
HEIGHT = 220
TOP_N = 10

_COUNTED, _TOO_SMALL, _NONE = "Counted", "Too small to count", "No sales"
_ACTUAL, _NOT_COUNTED, _LINE = "12-month total", "Not counted", "Trend line"


def _title(text: str, subtitle: str | list[str] = "") -> alt.TitleParams:
    return alt.TitleParams(text=text, subtitle=subtitle, anchor="start")


def _with_periods(history: pd.DataFrame) -> pd.DataFrame:
    return history.assign(
        period=[
            period_label(start, end)
            for start, end in zip(history["period_start"], history["period_end"], strict=True)
        ]
    )


def _period_x(title: str | None = None) -> alt.X:
    return alt.X(
        "period:N",
        sort=None,  # chronological (data) order
        title=title,
        axis=alt.Axis(labelAngle=-40, labelOverlap=True),
    )


# --- comparison: one chart shape for every metric ----------------------------


def comparison_chart(
    peers: pd.DataFrame,
    subject: str,
    column: str,
    metric: str,
    fmt: Callable[[float], str],
    noun_plural: str,
    reference: tuple[str, float] | None = None,
    top_n: int = TOP_N,
) -> alt.LayerChart:
    """`metric` for the top_n of `peers` (columns: name + `column`), the
    subject highlighted - and appended with its rank if it isn't among them.
    `reference` adds a labelled vertical rule (e.g. the median)."""
    ranked = peers[["name", column]].dropna().sort_values(column, ascending=False, kind="stable")
    ranked = ranked.assign(rank=range(1, len(ranked) + 1))
    shown = ranked.head(top_n)
    subject_rows = ranked[ranked["name"] == subject]
    outside = not subject_rows.empty and subject not in set(shown["name"])
    if outside:
        shown = pd.concat([shown, subject_rows])
    data = shown.assign(
        is_subject=shown["name"] == subject,
        label=[fmt(v) for v in shown[column]],
        # labels sit just right of the bar end, or of zero for negative bars
        label_at=shown[column].clip(lower=0),
        axis_name=[
            f"#{r}  {n}" if (outside and n == subject) else n
            for r, n in zip(shown["rank"], shown["name"], strict=True)
        ],
    )
    order = list(data["axis_name"])
    subject_axis_name = data.loc[data["is_subject"], "axis_name"]
    is_subject_label = (
        f"datum.value === {json.dumps(subject_axis_name.iloc[0])}"
        if not subject_axis_name.empty
        else "false"
    )
    subject_rank = subject_rows["rank"].iloc[0] if not subject_rows.empty else None
    rank_note = (
        f"{subject}: #{subject_rank} (shown last)"
        if outside
        else f"{subject}: #{subject_rank}"
        if subject_rank
        else ""
    )
    base = alt.Chart(data).encode(
        y=alt.Y(
            "axis_name:N",
            sort=order,
            title=None,
            axis=alt.Axis(
                domain=False,
                labelLimit=170,
                labelColor={
                    "condition": {"test": is_subject_label, "value": CHART_INK},
                    "value": CHART_MUTED,
                },
                labelFontWeight={
                    "condition": {"test": is_subject_label, "value": 700},
                    "value": 400,
                },
            ),
        ),
        tooltip=[
            alt.Tooltip(
                "name:N",
                title=noun_plural[:-1].capitalize() if noun_plural.endswith("s") else "Name",
            ),
            alt.Tooltip("label:N", title=metric),
            alt.Tooltip("rank:Q", title=f"Rank of {len(ranked)}"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=3, height={"band": 0.7}).encode(
        x=alt.X(f"{column}:Q", title=metric, axis=alt.Axis(labels=False, grid=False)),
        color=alt.condition(alt.datum.is_subject, alt.value(HIGHLIGHT), alt.value(PEER)),
    )
    labels = base.mark_text(align="left", dx=4, fontSize=11).encode(
        x=alt.X("label_at:Q"),
        text="label:N",
        color=alt.condition(alt.datum.is_subject, alt.value(CHART_INK), alt.value(CHART_MUTED)),
    )
    layers = [bars, labels]
    if reference is not None:
        ref_label, ref_value = reference
        ref = pd.DataFrame({"x": [ref_value], "text": [ref_label]})
        layers.append(alt.Chart(ref).mark_rule(color=CHART_INK, strokeWidth=1).encode(x="x:Q"))
        layers.append(
            alt.Chart(ref)
            .mark_text(align="left", dx=3, baseline="top", fontSize=10, color=CHART_INK)
            .encode(x="x:Q", y=alt.value(0), text="text:N")
        )
    return alt.layer(*layers).properties(
        title=_title(
            f"Compared with other {noun_plural}",
            [f"{metric} · top {min(top_n, len(ranked))} of {len(ranked)}", rank_note],
        ),
        height=alt.Step(20),
    )


# --- context charts, one per metric -----------------------------------------


def growth_context(history: pd.DataFrame) -> alt.Chart | None:
    """Each 12-month period's change vs the one before - the path the
    average growth rate summarises."""
    data = _with_periods(history)
    previous = data["fob_usd"].shift(1)
    data = data.assign(change=data["fob_usd"] / previous.where(previous > 0) - 1)
    data = data.dropna(subset=["change"])
    if data.empty:
        return None
    data = data.assign(direction=["Up" if c >= 0 else "Down" for c in data["change"]])
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=_period_x(),
            y=alt.Y("change:Q", title="Change", axis=alt.Axis(format="+.0%")),
            color=alt.Color(
                "direction:N",
                scale=alt.Scale(domain=["Up", "Down"], range=[UP, DOWN]),
                legend=None,  # the axis sign already says it
            ),
            tooltip=[
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("change:Q", title="vs previous 12 months", format="+.1%"),
                alt.Tooltip("fob_usd:Q", title="12-month total", format="$,.0f"),
            ],
        )
        .properties(
            title=_title(
                "Change vs. the previous 12 months",
                "Each bar: one 12-month period against the one before (blue up, red down)",
            ),
            height=HEIGHT,
        )
    )


def trend_context(history: pd.DataFrame, log_scale: bool) -> alt.LayerChart:
    """Actual 12-month totals against the fitted steady-growth line."""
    data = _with_periods(history).assign(
        status=history["in_fit"].map({True: _ACTUAL, False: _NOT_COUNTED}),
        trend=history["trend_fob_usd"].where(history["in_fit"]),
    )
    if log_scale:
        data = data[data["fob_usd"] > 0]
    y_scale = alt.Scale(type="log") if log_scale else alt.Scale()
    colors = alt.Scale(domain=[_ACTUAL, _NOT_COUNTED, _LINE], range=[HIGHLIGHT, PEER, EMPHASIS])
    legend = alt.Legend(title=None, orient="top", direction="horizontal", labelLimit=0)
    tooltip = [
        alt.Tooltip("period:N", title="Period"),
        alt.Tooltip("fob_usd:Q", title="12-month total", format="$,.0f"),
        alt.Tooltip("trend:Q", title="Steady-growth line", format="$,.0f"),
    ]
    base = alt.Chart(data).encode(x=_period_x())
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        y=alt.Y(
            "fob_usd:Q",
            title="Sales",
            scale=y_scale,
            axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS),
        ),
        color=alt.Color("status:N", scale=colors, legend=legend),
        tooltip=tooltip,
    )
    line = (
        base.mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(filled=True, size=45))
        .encode(
            y=alt.Y("trend:Q", scale=y_scale),
            color=alt.Color("series:N", scale=colors, legend=legend),
            tooltip=tooltip,
        )
        .transform_calculate(series=f"'{_LINE}'")
    )
    return (bars + line).properties(
        title=_title(
            "Actual sales vs. the steady-growth line",
            [
                "The closer the bars sit to the line, the steadier the growth",
                "Grey = no sales, or too small to count",
            ],
        ),
        height=HEIGHT + 40,
    )


def _share_axis_format(max_share: float) -> str:
    if max_share < 0.01:
        return ".2%"
    return ".1%" if max_share < 0.1 else ".0%"


def share_context(
    history: pd.DataFrame, scope_totals: pd.DataFrame, title: tuple[str, str]
) -> alt.Chart | None:
    """The selected result's share of its scope, per 12-month period.
    `title` is (title, subtitle) - names go in the subtitle, which fits a
    phone better than a long title."""
    data = _with_periods(history).merge(
        scope_totals[["periods_ago", "fob_usd"]].rename(columns={"fob_usd": "scope_fob"}),
        on="periods_ago",
    )
    data = data[data["scope_fob"] > 0].assign(share=lambda d: d["fob_usd"] / d["scope_fob"])
    if data.empty:
        return None
    last = data.tail(1).assign(label=lambda d: [fmt_share(v) for v in d["share"]])
    base = alt.Chart(data).encode(
        x=_period_x(),
        y=alt.Y(
            "share:Q",
            title="Share",
            # enough decimals that tiny shares don't print as 0%, 0%, 0%
            axis=alt.Axis(format=_share_axis_format(data["share"].max())),
        ),
    )
    line = base.mark_line(color=HIGHLIGHT, strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
        tooltip=[
            alt.Tooltip("period:N", title="Period"),
            alt.Tooltip("share:Q", title="Share", format=".2%"),
            alt.Tooltip("fob_usd:Q", title="Sales", format="$,.0f"),
        ]
    )
    end_label = (
        alt.Chart(last)
        .mark_text(align="left", dx=6, fontWeight="bold", color=CHART_INK)
        .encode(x=_period_x(), y="share:Q", text="label:N")
    )
    return (line + end_label).properties(title=_title(*title), height=HEIGHT)


def coverage_context(history: pd.DataFrame) -> alt.Chart:
    """Which 12-month periods in the window had sales: one cell per period."""
    data = _with_periods(history).assign(
        row="Sales",
        state=[
            _COUNTED if fit else (_TOO_SMALL if fob > 0 else _NONE)
            for fit, fob in zip(history["in_fit"], history["fob_usd"], strict=True)
        ],
    )
    return (
        alt.Chart(data)
        .mark_rect(cornerRadius=3, stroke="white", strokeWidth=3)
        .encode(
            x=_period_x(),
            y=alt.Y("row:N", title=None, axis=None),
            color=alt.Color(
                "state:N",
                scale=alt.Scale(
                    domain=[_COUNTED, _TOO_SMALL, _NONE], range=[HIGHLIGHT, "#9ec5f4", PEER]
                ),
                legend=alt.Legend(title=None, orient="top", direction="horizontal", labelLimit=0),
            ),
            tooltip=[
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("state:N", title="Sales"),
                alt.Tooltip("fob_usd:Q", title="12-month total", format="$,.0f"),
            ],
        )
        .properties(
            title=_title("Which years had sales", "One cell per 12-month period in the window"),
            height=alt.Step(44),
        )
    )


def volume_context(subject: str, total: float, typical: float) -> alt.LayerChart:
    """The selected result's total next to a typical product-market pair."""
    data = pd.DataFrame(
        {
            "who": [subject, "Typical pair"],
            "total": [total, typical],
            "is_subject": [True, False],
            "label": [money(total), money(typical)],
        }
    )
    base = alt.Chart(data).encode(
        y=alt.Y("who:N", sort=None, title=None, axis=alt.Axis(domain=False, labelLimit=190)),
    )
    bars = base.mark_bar(cornerRadiusEnd=3, height={"band": 0.6}).encode(
        x=alt.X(
            "total:Q", title="Total sales in the window", axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS)
        ),
        color=alt.condition(alt.datum.is_subject, alt.value(HIGHLIGHT), alt.value(PEER)),
        tooltip=[
            alt.Tooltip("who:N", title=" "),
            alt.Tooltip("total:Q", title="Total", format="$,.0f"),
        ],
    )
    labels = base.mark_text(align="left", dx=4, color=CHART_INK).encode(x="total:Q", text="label:N")
    return (bars + labels).properties(
        title=_title(
            "Total sales vs. a typical pair",
            "Typical = the median product-market pair over the same years",
        ),
        height=alt.Step(30),
    )


def confidence_context(row: pd.Series) -> alt.LayerChart:
    """The three pieces of evidence confidence combines, weakest emphasised."""
    legs = {
        "History": row["coverage_score"],
        "Trend steadiness": row["trend_r2_adj"],
        "Size vs. typical": row["volume_confidence"],
    }
    weakest = min(legs, key=legs.get)
    data = pd.DataFrame(
        {
            "leg": list(legs),
            "value": list(legs.values()),
            "is_weakest": [leg == weakest for leg in legs],
            "label": [f"{v:.0%}" for v in legs.values()],
        }
    )
    base = alt.Chart(data).encode(
        y=alt.Y("leg:N", sort=None, title=None, axis=alt.Axis(domain=False)),
    )
    bars = base.mark_bar(cornerRadiusEnd=3, height={"band": 0.6}).encode(
        x=alt.X(
            "value:Q",
            title="Score (100% = strongest)",
            scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format=".0%"),
        ),
        color=alt.condition(alt.datum.is_weakest, alt.value(EMPHASIS), alt.value(HIGHLIGHT)),
        tooltip=[
            alt.Tooltip("leg:N", title="Evidence"),
            alt.Tooltip("value:Q", title="Score", format=".0%"),
        ],
    )
    labels = base.mark_text(align="left", dx=4, color=CHART_INK).encode(x="value:Q", text="label:N")
    return (bars + labels).properties(
        title=_title(
            "The three pieces of evidence",
            f"Confidence combines them; the weakest ({weakest}, orange) pulls it down most",
        ),
        height=alt.Step(30),
    )


def size_context(history: pd.DataFrame) -> alt.VConcatChart:
    """Value and tons per 12-month period - two charts, one below the other,
    never a dual axis."""
    data = _with_periods(history)
    base = alt.Chart(data).encode(x=_period_x())
    value = (
        base.mark_bar(color=HIGHLIGHT, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            y=alt.Y("fob_usd:Q", title="Value", axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS)),
            tooltip=[
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("fob_usd:Q", title="Value", format="$,.0f"),
            ],
        )
        .properties(title=_title("Value each 12-month period"), height=130)
    )
    weight = base.mark_bar(color=HIGHLIGHT, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        y=alt.Y("metric_ton:Q", title="Tons", axis=alt.Axis(labelExpr=TONS_AXIS_LABELS)),
        tooltip=[
            alt.Tooltip("period:N", title="Period"),
            alt.Tooltip("metric_ton:Q", title="Tons", format=",.1f"),
        ],
    )
    # one 40-ft reefer container a year - the index's materiality anchor
    container = pd.DataFrame({"y": [25.0], "text": ["one container a year (25 t)"]})
    container_rule = alt.Chart(container).mark_rule(color=CHART_INK, strokeWidth=1).encode(y="y:Q")
    container_label = (
        alt.Chart(container)
        .mark_text(align="left", baseline="bottom", dx=2, dy=-2, fontSize=10, color=CHART_INK)
        .encode(x=alt.value(0), y="y:Q", text="text:N")
    )
    weight = (weight + container_rule + container_label).properties(
        title=_title("Tons each 12-month period", "The line marks one 25-ton container a year"),
        height=130,
    )
    return alt.vconcat(value, weight, spacing=18)


def price_context(
    history: pd.DataFrame, peer_median: float, noun_plural: str
) -> alt.LayerChart | None:
    """$/t per 12-month period, against the median of the ranked set."""
    data = _with_periods(history)
    data = data[data["metric_ton"] > 0].assign(price=lambda d: d["fob_usd"] / d["metric_ton"])
    if data.empty:
        return None
    line = (
        alt.Chart(data)
        .mark_line(color=HIGHLIGHT, strokeWidth=2, point=alt.OverlayMarkDef(size=40))
        .encode(
            x=_period_x(),
            y=alt.Y("price:Q", title="Average price ($/t)", axis=alt.Axis(format="$,.0f")),
            tooltip=[
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("price:Q", title="$/t", format="$,.0f"),
            ],
        )
    )
    ref = pd.DataFrame(
        {"y": [peer_median], "text": [f"Median of ranked {noun_plural}: ${peer_median:,.0f}/t"]}
    )
    rule = alt.Chart(ref).mark_rule(color=CHART_INK, strokeWidth=1).encode(y="y:Q")
    rule_label = (
        alt.Chart(ref)
        .mark_text(align="left", baseline="bottom", dx=2, dy=-3, fontSize=10, color=CHART_INK)
        .encode(x=alt.value(0), y="y:Q", text="text:N")
    )
    return (line + rule + rule_label).properties(
        title=_title(
            "Average price each 12-month period", "Total value ÷ total weight, per period"
        ),
        height=HEIGHT,
    )


# formatters for comparison-chart labels
def fmt_growth(v: float) -> str:
    return f"{v * 100:+.0f}%"


def fmt_share(v: float) -> str:
    if 0 < v < 0.001:
        return "<0.1%"
    return f"{v:.1%}" if v < 0.1 else f"{v:.0%}"


def fmt_pct(v: float) -> str:
    return f"{v:.0%}"


def fmt_two(v: float) -> str:
    return f"{v:.2f}"


def fmt_years(v: float) -> str:
    return f"{v:.0f} yrs"


def fmt_price(v: float) -> str:
    return f"${v:,.0f}/t"


def fmt_tons(v: float) -> str:
    return tons(v)


def recent_context(history: pd.DataFrame, recent_periods: int = 4) -> alt.Chart | None:
    """The last few 12-month totals - what recent momentum is read from."""
    data = _with_periods(history).tail(recent_periods)
    if data["fob_usd"].le(0).all():
        return None
    previous = data["fob_usd"].shift(1)
    data = data.assign(
        direction=[
            "Up" if p > 0 and v > p else ("Down" if p > 0 else "No earlier year to compare")
            for v, p in zip(data["fob_usd"], previous.fillna(0), strict=True)
        ],
        label=[money(v) if v > 0 else "none" for v in data["fob_usd"]],
    )
    base = alt.Chart(data).encode(x=_period_x())
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        y=alt.Y("fob_usd:Q", title="Sales", axis=alt.Axis(labelExpr=MONEY_AXIS_LABELS)),
        color=alt.Color(
            "direction:N",
            scale=alt.Scale(
                domain=["Up", "Down", "No earlier year to compare"], range=[UP, DOWN, PEER]
            ),
            legend=alt.Legend(title=None, orient="top", direction="horizontal", labelLimit=0),
        ),
        tooltip=[
            alt.Tooltip("period:N", title="Period"),
            alt.Tooltip("fob_usd:Q", title="12-month total", format="$,.0f"),
        ],
    )
    labels = base.mark_text(dy=-6, fontSize=11, color=CHART_INK).encode(
        y="fob_usd:Q", text="label:N"
    )
    return (bars + labels).properties(
        title=_title(
            f"The last {len(data)} years",
            "Each bar: one 12-month total, coloured by whether it rose on the year before",
        ),
        height=HEIGHT,
    )


def index_breakdown(factors: dict[str, float]) -> alt.LayerChart:
    """The four factors the index multiplies, the weakest emphasised."""
    weakest = min(factors, key=factors.get)
    data = pd.DataFrame(
        {
            "factor": [name.capitalize() for name in factors],
            "value": list(factors.values()),
            "is_weakest": [name == weakest for name in factors],
            "label": [f"{v:.2f}" for v in factors.values()],
        }
    )
    base = alt.Chart(data).encode(
        y=alt.Y("factor:N", sort=None, title=None, axis=alt.Axis(domain=False)),
    )
    bars = base.mark_bar(cornerRadiusEnd=3, height={"band": 0.6}).encode(
        x=alt.X("value:Q", title="Factor (1 = best)", scale=alt.Scale(domain=[0, 1])),
        color=alt.condition(alt.datum.is_weakest, alt.value(EMPHASIS), alt.value(HIGHLIGHT)),
        tooltip=[alt.Tooltip("factor:N", title="Factor"), alt.Tooltip("value:Q", format=".2f")],
    )
    labels = base.mark_text(align="left", dx=4, color=CHART_INK).encode(x="value:Q", text="label:N")
    return (bars + labels).properties(
        title=_title(
            "What the index is made of",
            f"Index = 100 x all four multiplied; the weakest ({weakest}, orange) costs most",
        ),
        height=alt.Step(30),
    )


def fmt_index(v: float) -> str:
    return f"{v:.0f}"
