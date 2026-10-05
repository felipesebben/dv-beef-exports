"""Opportunities page: the ranked results for the sidebar's query."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import METRIC_GLOSSARY, latest_period_end
from dv_beef_exports.app.common import (
    CHART_INK,
    COLUMN_FORMATS,
    COLUMN_LABELS,
    PERCENT_COLUMNS,
    RANKED_COL_LABELS,
    connection,
    display_name,
    query_summary,
    style_chart,
)

# Table column order: what a reader needs first (the score, how much to trust
# it, the size of the trade), then the evidence behind confidence.
_TABLE_ORDER = [
    "name",
    "opportunity_score",
    "confidence",
    "annual_growth_pct",
    "share_pct",
    "total_fob_usd",
    "total_metric_ton",
    "unit_price_usd_per_ton",
    "years_active",
    "trend_r2_adj",
    "coverage_score",
    "volume_confidence",
]


def _opportunity_chart(result: pd.DataFrame, ranked_label: str) -> alt.LayerChart:
    top = result.nlargest(15, "opportunity_score")
    base = alt.Chart(top).encode(
        x=alt.X("opportunity_score:Q", title="Opportunity score"),
        y=alt.Y(
            "name:N",
            sort="-x",
            title=None,
            # country/product names are the data's identity: primary ink
            axis=alt.Axis(labelColor="#0b0b0b", domain=False, labelLimit=200),
        ),
        tooltip=[
            alt.Tooltip("name:N", title=ranked_label),
            alt.Tooltip("opportunity_score:Q", title="Opportunity score", format=".2f"),
            alt.Tooltip("confidence:Q", title="Confidence", format=".0%"),
            alt.Tooltip("annual_growth_pct:Q", title="Growth / yr", format="+.0%"),
            alt.Tooltip("share_pct:Q", title="Share", format=".1%"),
            alt.Tooltip("total_fob_usd:Q", title="Value in the window", format="$,.0f"),
        ],
    )
    bars = base.mark_bar(color="#2a78d6", cornerRadiusEnd=4)
    labels = base.mark_text(align="left", dx=4, color=CHART_INK).encode(
        text=alt.Text("opportunity_score:Q", format=".2f")
    )
    return (bars + labels).properties(height=alt.Step(24))


def _column_config(ranked_label: str) -> dict:
    """Label, format and hover tooltip for every column. Tooltips come from
    METRIC_GLOSSARY - the same one-liners as the methodology doc."""
    config: dict = {"name": st.column_config.TextColumn(ranked_label, pinned=True, width="medium")}
    for col, label in COLUMN_LABELS.items():
        config[col] = st.column_config.NumberColumn(
            label, format=COLUMN_FORMATS[col], help=METRIC_GLOSSARY[col]
        )
    # confidence reads faster as a bar than as a number
    config["confidence"] = st.column_config.ProgressColumn(
        COLUMN_LABELS["confidence"],
        format="%.0f%%",
        min_value=0,
        max_value=100,
        help=METRIC_GLOSSARY["confidence"],
    )
    return config


def _reading_guide() -> None:
    with st.expander("How to read these numbers", icon=":material/help:"):
        st.markdown(
            "The score is a **starting point for research, not a decision**. Read it in "
            "this order:\n\n"
            "1. **Score** - fast growth with lots of room left ranks highest. It's a "
            "ranking, not a size: a tiny market can score high.\n"
            "2. **Confidence** - how much evidence backs the score:\n"
            "    - **70% and above**: history, steadiness and size all hold up.\n"
            "    - **40-70%**: one piece is weak - check which.\n"
            "    - **below 40%**: treat it as a hypothesis.\n"
            "3. **Value and tons** - whether the trade is big enough to be worth a sales "
            "conversation.\n\n"
            "Hover any column header for what it means."
        )
        st.page_link(
            "app_pages/explain.py",
            label="See any result explained in plain language",
            icon=":material/lightbulb:",
        )


st.title("Opportunities")
end_year, end_month = latest_period_end(connection())
st.caption(
    "Where Brazil's beef exports are growing fast with plenty of room left to grow. "
    f"Each year is a 12-month period ending {date(end_year, end_month, 1):%b %Y}."
)
query = st.session_state.query
st.caption(query_summary(query))

result = st.session_state.result
if result.empty:
    st.warning(
        "Nothing qualifies for this selection - nothing sold in enough years. Try a broader "
        "product or market, or more years of history under **Advanced** in the sidebar.",
        icon=":material/search_off:",
    )
    st.stop()

ranked_col = result.columns[0]
ranked_label = RANKED_COL_LABELS.get(ranked_col, ranked_col)
result = result.assign(name=[display_name(ranked_col, v) for v in result[ranked_col]])

st.subheader("Top 15")
st.altair_chart(style_chart(_opportunity_chart(result, ranked_label)), width="stretch")
st.page_link(
    "app_pages/explain.py",
    label="Explain any of these in plain language",
    icon=":material/lightbulb:",
)

st.subheader(f"All {len(result)} results")
display = result.copy()
for pct_col in PERCENT_COLUMNS:
    display[pct_col] = display[pct_col] * 100

st.dataframe(
    display,
    width="stretch",
    hide_index=True,
    column_order=_TABLE_ORDER,
    column_config=_column_config(ranked_label),
)
_reading_guide()
