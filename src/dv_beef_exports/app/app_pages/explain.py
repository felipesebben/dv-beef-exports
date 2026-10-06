"""
Explain-the-numbers page: every metric for one result of the sidebar's
query, in plain business language, using that result's real numbers - see
app/explanations.py for the wording and app/explain_charts.py for the two
charts under each metric (its context, and how it compares).
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.market_overview import market_trend
from dv_beef_exports.analysis.opportunity_scoring import period_history
from dv_beef_exports.app import explain_charts as charts
from dv_beef_exports.app.common import (
    RANKED_NOUNS,
    connection,
    display_name,
    geo_label,
    product_phrase,
    query_summary,
    style_chart,
)
from dv_beef_exports.app.explanations import (
    Selection,
    bottom_line,
    explain_all,
    implied_typical_size,
    index_factors,
    plural,
)


def _md(text: str) -> str:
    """Escape $ for st.markdown, which renders text between two $ signs as a
    LaTeX formula - "$309k and 182 t - about $38.6k" would come out garbled."""
    return text.replace("$", "\\$")


def _selection_for(row: pd.Series, rank: int, n_ranked: int, query: dict, window: str) -> Selection:
    subject = row.iloc[0]
    if query["mode"] == "markets":
        fixed = product_phrase(query["product_level"], query["product_value"])
        subject_label = geo_label(query["geo_level"], subject)
        noun = RANKED_NOUNS[query["geo_level"]]
    else:
        fixed = geo_label(query["geo_level"], query["geo_value"])
        subject_label = product_phrase(query["product_level"], subject)
        noun = RANKED_NOUNS[query["product_level"]]
    return Selection(
        mode=query["mode"],
        fixed_label=fixed,
        subject_label=subject_label,
        ranked_noun=noun,
        rank=rank,
        n_ranked=n_ranked,
        window_years=query["window_years"],
        window_label=window,
    )


def _history_for(subject: str, query: dict) -> pd.DataFrame:
    con = connection()
    if query["mode"] == "markets":
        return period_history(
            con,
            product_level=query["product_level"],
            product_value=query["product_value"],
            geo_level=query["geo_level"],
            geo_value=subject,
            window_years=query["window_years"],
        )
    return period_history(
        con,
        product_level=query["product_level"],
        product_value=subject,
        geo_level=query["geo_level"],
        geo_value=query["geo_value"],
        window_years=query["window_years"],
    )


def _scope_totals(query: dict) -> pd.DataFrame:
    """The fixed side's total per period - what the share is a share OF."""
    if query["mode"] == "markets":
        scope = {"product_level": query["product_level"], "product_value": query["product_value"]}
    else:
        scope = {"geo_level": query["geo_level"], "geo_value": query["geo_value"]}
    return market_trend(connection(), window_years=query["window_years"], **scope)


def _share_title(sel: Selection) -> tuple[str, str]:
    """(title, subtitle) - the name goes in the subtitle, which can wrap less
    badly than a title on a phone."""
    if sel.mode == "markets":
        return f"Share of Brazil's {sel.product} exports", f"Going to {sel.market}, per period"
    return f"Share of all Brazil sells to {sel.market}", f"Taken by {sel.product}, per period"


def _charts_for(key: str, ctx: dict) -> tuple[alt.TopLevelMixin | None, alt.TopLevelMixin]:
    """(context chart, comparison chart) for one metric card."""
    peers, subject, nouns = ctx["peers"], ctx["subject"], ctx["nouns"]

    def compare(column: str, metric: str, fmt, reference=None):
        return charts.comparison_chart(peers, subject, column, metric, fmt, nouns, reference)

    history, row = ctx["history"], ctx["row"]
    if key == "annual_growth_pct":
        return charts.growth_context(history), compare(
            "annual_growth_pct", "Growth per year", charts.fmt_growth
        )
    if key == "trend_r2_adj":
        return charts.trend_context(history, ctx["log_scale"]), compare(
            "trend_r2_adj", "Trend steadiness", charts.fmt_two
        )
    if key == "share_pct":
        return charts.share_context(history, ctx["scope_totals"], ctx["share_title"]), compare(
            "share_pct", "Share", charts.fmt_share
        )
    if key == "recent_growth_pct":
        return charts.recent_context(history), compare(
            "recent_growth_pct", "Recent growth per year", charts.fmt_growth
        )
    if key == "opportunity_score":
        return charts.index_breakdown(index_factors(row)), compare(
            "opportunity_score", "Opportunity index", charts.fmt_index
        )
    if key == "coverage_score":
        return charts.coverage_context(history), compare(
            "years_active", "Years with sales", charts.fmt_years
        )
    if key == "volume_confidence":
        typical = implied_typical_size(row["total_fob_usd"], row["volume_confidence"])
        return charts.volume_context(subject, row["total_fob_usd"], typical, nouns), compare(
            "volume_confidence", "Size vs. typical", charts.fmt_two, reference=("typical", 0.5)
        )
    if key == "confidence":
        return charts.confidence_context(row), compare("confidence", "Confidence", charts.fmt_pct)
    if key == "total_fob_usd":
        return charts.size_context(history), compare(
            "tons_per_year",
            "Tons a year",
            charts.fmt_tons,
            reference=("one container", 25.0),
        )
    if key == "unit_price_usd_per_ton":
        median = peers["unit_price_usd_per_ton"].median()
        return charts.price_context(history, median, nouns), compare(
            "price_trend_pct", "Price trend per year", charts.fmt_growth
        )
    raise ValueError(f"no charts for {key!r}")


st.title("Explain the numbers")
st.caption(
    "Every number behind one result, in plain language: what it says for this market, what "
    "to do about it, and how it compares with the rest."
)
query = st.session_state.query
st.caption(query_summary(query))
result = st.session_state.result
explainable = result[result.iloc[:, 0].notna()]

if explainable.empty:
    st.info(
        "Nothing to explain for this selection - nothing sold in enough years. Try a broader "
        "product or market, or more years of history under **Advanced** in the sidebar.",
        icon=":material/search_off:",
    )
    st.stop()

ranked_col = result.columns[0]
ranks = {value: i + 1 for i, value in enumerate(result.iloc[:, 0])}
subject_value = st.selectbox(
    "Explain the numbers for",
    list(explainable.iloc[:, 0]),
    format_func=lambda v: f"#{ranks[v]}  {display_name(ranked_col, v)}",
)
if len(explainable) < len(result):
    st.caption(
        "Countries outside the four tracked trade blocs are grouped as '(no bloc)' and "
        "can't be explained as one market."
    )

row = result[result.iloc[:, 0] == subject_value].iloc[0]
history = _history_for(subject_value, query)
window = f"{history['period_start'].iloc[0]:%b %Y} - {history['period_end'].iloc[-1]:%b %Y}"
sel = _selection_for(row, ranks[subject_value], len(result), query, window)
subject_name = display_name(ranked_col, subject_value)
context = {
    "row": row,
    "history": history,
    "subject": subject_name,
    "peers": result.assign(name=[display_name(ranked_col, v) for v in result[ranked_col]]),
    "nouns": plural(sel.ranked_noun),
    "scope_totals": _scope_totals(query),
    "share_title": _share_title(sel),
    "log_scale": False,
}

with st.container(border=True):
    st.subheader(":material/flag: Bottom line")
    st.markdown(_md(bottom_line(row, sel, history)))

for explanation in explain_all(row, sel, history, result):
    with st.container(border=True):
        left, right = st.columns([1, 3])
        left.metric(explanation.title, explanation.value)
        with right:
            # lead with this market's numbers and the action; the generic
            # definition is one tap away instead of opening every card
            st.markdown(_md(explanation.for_you))
            st.markdown(_md(f"**What to do:** {explanation.action}"))
            if explanation.caveat:
                st.caption(_md(f":material/warning: {explanation.caveat}"))
            with st.expander("What this measures", icon=":material/info:"):
                st.markdown(_md(explanation.meaning))

        if explanation.key == "trend_r2_adj":
            context["log_scale"] = (
                st.segmented_control(
                    "Scale",
                    ["Actual values", "Log scale"],
                    default="Actual values",
                    required=True,
                    key="explain_history_scale",
                    help="Log scale makes small early years visible: each step up is 10x.",
                )
                == "Log scale"
            )
        context_chart, comparison = _charts_for(explanation.key, context)
        chart_left, chart_right = st.columns(2, gap="large")
        with chart_left:
            if context_chart is None:
                st.caption("Not enough history to chart this.")
            else:
                st.altair_chart(style_chart(context_chart), width="stretch")
        with chart_right:
            st.altair_chart(style_chart(comparison), width="stretch")
