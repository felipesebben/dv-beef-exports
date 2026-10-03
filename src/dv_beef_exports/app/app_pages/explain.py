"""
Explain-the-numbers page: every metric for one result of the sidebar's
query, in plain business language, using that result's real numbers - see
app/explanations.py for the wording.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import period_history
from dv_beef_exports.app.common import (
    RANKED_NOUNS,
    connection,
    geo_label,
    product_label,
    product_phrase,
)
from dv_beef_exports.app.explanations import Selection, bottom_line, explain_all

_COUNTED = "Actual sales (counted)"
_LEFT_OUT = "Actual sales (none, or too small to count)"
_TREND = "Steady-growth trend"


def _md(text: str) -> str:
    """Escape $ for st.markdown, which renders text between two $ signs as a
    LaTeX formula - "$309k and 182 t - about $38.6k" would come out garbled."""
    return text.replace("$", "\\$")


def _history_chart(history: pd.DataFrame, log_scale: bool) -> alt.LayerChart:
    data = history.assign(
        status=history["in_fit"].map({True: _COUNTED, False: _LEFT_OUT}),
        trend=history["trend_fob_usd"].where(history["in_fit"]),
        # spell out the whole 12-month block, so no bar reads as a single month
        period=[
            f"{start:%b %Y}–{end:%b %Y}"
            for start, end in zip(history["period_start"], history["period_end"], strict=True)
        ],
    )
    if log_scale:
        data = data[data["fob_usd"] > 0]
    y_scale = alt.Scale(type="log") if log_scale else alt.Scale()
    # one shared colour scale, so bars and the trend line share a legend
    colors = alt.Scale(
        domain=[_COUNTED, _LEFT_OUT, _TREND], range=["#2a78d6", "#c3c2b7", "#e07b39"]
    )
    legend = alt.Legend(title=None, orient="top")
    base = alt.Chart(data).encode(
        x=alt.X(
            "period:N",
            sort=None,  # keep chronological (data) order, not alphabetical
            title="12-month period (one bar = the total for all 12 months)",
            axis=alt.Axis(labelAngle=-35),
        ),
    )
    tooltip = [
        alt.Tooltip("period:N", title="Period"),
        alt.Tooltip("fob_usd:Q", title="Total sold in these 12 months (USD)", format="$,.0f"),
        alt.Tooltip("trend:Q", title="Trend for these 12 months (USD)", format="$,.0f"),
        alt.Tooltip("metric_ton:Q", title="Total tons in these 12 months", format=",.1f"),
    ]
    bars = base.mark_bar(opacity=0.85).encode(
        y=alt.Y("fob_usd:Q", title="Total sold in the 12-month period (USD)", scale=y_scale),
        color=alt.Color("status:N", scale=colors, legend=legend),
        tooltip=tooltip,
    )
    trend = (
        base.mark_line(strokeWidth=3, point=alt.OverlayMarkDef(filled=True, size=60))
        .encode(
            y=alt.Y("trend:Q", scale=y_scale),
            color=alt.Color("legend_label:N", scale=colors, legend=legend),
            tooltip=tooltip,
        )
        .transform_calculate(legend_label=f"'{_TREND}'")
    )
    return (bars + trend).properties(height=280)


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


st.title("Explain the numbers")
st.caption(
    "Every metric for one result of your query, in plain language - what it means, "
    "what it says for your selection, and what to do with it. Change the query in the sidebar."
)

query = st.session_state.query
result = st.session_state.result
explainable = result[result.iloc[:, 0].notna()]

if explainable.empty:
    st.info(
        "Nothing to explain for this selection - no result cleared the minimum active-years "
        "threshold. Try a wider window or a broader product in the sidebar."
    )
    st.stop()

ranks = {value: i + 1 for i, value in enumerate(result.iloc[:, 0])}
con = connection()
subject_labels = {
    value: (
        geo_label(query["geo_level"], value)
        if query["mode"] == "markets"
        else product_label(con, query["product_level"], value)
    )
    for value in explainable.iloc[:, 0]
}
subject = st.selectbox(
    "Explain the numbers for",
    list(subject_labels),
    format_func=lambda v: f"#{ranks[v]}  {subject_labels[v]}",
)
if len(explainable) < len(result):
    st.caption(
        "Countries outside the four tracked trade blocs are grouped as '(no bloc)' and "
        "can't be explained as one market."
    )

row = result[result.iloc[:, 0] == subject].iloc[0]
history = _history_for(subject, query)
window = f"{history['period_start'].iloc[0]:%b %Y} - {history['period_end'].iloc[-1]:%b %Y}"
sel = _selection_for(row, ranks[subject], len(result), query, window)

with st.container(border=True):
    st.subheader(":material/flag: Bottom line")
    st.markdown(_md(bottom_line(row, sel, history)))

for explanation in explain_all(row, sel, history, result):
    with st.container(border=True):
        left, right = st.columns([1, 3])
        left.metric(explanation.title, explanation.value)
        with right:
            st.markdown(_md(f"**What it means.** {explanation.meaning}"))
            st.markdown(_md(f"**For your selection.** {explanation.for_you}"))
            st.markdown(_md(f"**What to do with it.** {explanation.action}"))
            if explanation.caveat:
                st.caption(_md(f":material/warning: {explanation.caveat}"))
        if explanation.key == "trend_r2_adj":
            scale = st.segmented_control(
                "Scale",
                ["Actual values", "Log scale"],
                default="Actual values",
                key="explain_history_scale",
                help="Log scale makes small early years visible: each step up is 10x.",
            )
            st.altair_chart(_history_chart(history, scale == "Log scale"), width="stretch")
            latest = history.iloc[-1]
            latest_label = f"{latest['period_start']:%b %Y}–{latest['period_end']:%b %Y}"
            st.caption(
                "**Each bar is a full 12-month block, not a single month**: the bar labelled "
                f"{latest_label} is everything sold across those 12 months added up. Blocks "
                "don't overlap - one bar per year, with years ending at the latest month in the "
                "data so a half-finished calendar year never looks like a drop. The orange line "
                "is the steady-growth trend the growth rate comes from. Where a bar falls short "
                "of its dot, that year sold less than the trend; where it overshoots, more. The "
                "smaller those gaps overall, the higher the steadiness score."
            )
