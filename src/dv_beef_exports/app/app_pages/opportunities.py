"""Opportunities page: the ranked results for the sidebar's query."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import METRIC_GLOSSARY, latest_period_end
from dv_beef_exports.app.common import (
    BLUE,
    CHART_INK,
    COLUMN_FORMATS,
    COLUMN_LABELS,
    PERCENT_COLUMNS,
    RANKED_COL_LABELS,
    card,
    card_header,
    connection,
    display_name,
    headline,
    left_labels,
    page_header,
    query_summary,
    show_chart,
)
from dv_beef_exports.app.explanations import ranking_note

# Table column order: what a reader needs first (the score, how much to trust
# it, the size of the trade), then the evidence behind confidence.
_TABLE_ORDER = [
    "name",
    "opportunity_score",
    "confidence",
    "annual_growth_pct",
    "recent_growth_pct",
    "share_pct",
    "tons_per_year",
    "total_fob_usd",
    "unit_price_usd_per_ton",
    "price_trend_pct",
    "attractiveness",
    "materiality",
    "recent_consistency",
    "years_active",
    "trend_r2_adj",
    "coverage_score",
    "volume_confidence",
    "total_metric_ton",
]


def _opportunity_chart(result: pd.DataFrame, ranked_label: str) -> alt.LayerChart:
    top = result.nlargest(15, "opportunity_score")
    base = alt.Chart(top).encode(
        x=alt.X(
            "opportunity_score:Q",
            title=None,
            scale=alt.Scale(domain=[0, 100]),
            axis=alt.Axis(values=[0, 20, 40, 60, 80, 100]),
        ),
        y=alt.Y(
            "name:N",
            sort="-x",
            title=None,
            # names read left to right from the card's edge
            axis=alt.Axis(**left_labels(200)),
        ),
        tooltip=[
            alt.Tooltip("name:N", title=ranked_label),
            alt.Tooltip("opportunity_score:Q", title="Opportunity index", format=".0f"),
            alt.Tooltip("confidence:Q", title="Confidence", format=".0%"),
            alt.Tooltip("annual_growth_pct:Q", title="Growth / yr", format="+.0%"),
            alt.Tooltip("share_pct:Q", title="Share", format=".1%"),
            alt.Tooltip("total_fob_usd:Q", title="Value in the window", format="$,.0f"),
        ],
    )
    bars = base.mark_bar(color=BLUE, cornerRadiusEnd=4)
    labels = base.mark_text(align="left", dx=4, color=CHART_INK).encode(
        text=alt.Text("opportunity_score:Q", format=".0f")
    )
    return (bars + labels).properties(
        title=alt.TitleParams(
            text="Opportunity index (0-100)",
            subtitle="Higher = a better lead within this selection",
            anchor="start",
        ),
        height=alt.Step(24),
    )


def _column_config(ranked_label: str) -> dict:
    """Label, format and hover tooltip for every column. Tooltips come from
    METRIC_GLOSSARY - the same one-liners as the methodology doc."""
    config: dict = {"name": st.column_config.TextColumn(ranked_label, pinned=True, width="medium")}
    for col, label in COLUMN_LABELS.items():
        config[col] = st.column_config.NumberColumn(
            label, format=COLUMN_FORMATS[col], help=METRIC_GLOSSARY[col]
        )
    # the index and confidence read faster as bars than as numbers
    config["opportunity_score"] = st.column_config.ProgressColumn(
        COLUMN_LABELS["opportunity_score"],
        format="%.0f",
        min_value=0,
        max_value=100,
        help=METRIC_GLOSSARY["opportunity_score"],
    )
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
            "The **opportunity index** (0-100) is a **starting point for research, not a "
            "decision**. It multiplies four factors, so one weak factor pulls it down:\n\n"
            "1. **Attractiveness** - long-run growth, recent momentum, size and a rising "
            "price, each ranked against the rest.\n"
            "2. **Room to grow** - the part of the market not already Brazil's, so a "
            "saturated market's size counts for little.\n"
            "3. **Commercial size** - tons a year against one 25-ton container: a market "
            "buying less than a container a year is heavily discounted.\n"
            "4. **Evidence** - confidence in the trend:\n"
            "    - **70% and above**: history, steadiness and size all hold up.\n"
            "    - **40-70%**: one piece is weak - check which.\n"
            "    - **below 40%**: treat it as a hypothesis.\n\n"
            "Hover any column header for what it means."
        )
        st.page_link(
            "app_pages/explain.py",
            label="See any result explained in plain language",
            icon=":material/lightbulb:",
        )


end_year, end_month = latest_period_end(connection())
page_header(
    f"Opportunities · data to {date(end_year, end_month, 1):%b %Y}",
    "Opportunities",
    "Where Brazil's beef exports are growing steadily, in material volumes, with room left "
    "to grow - each year a full 12-month period.",
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

with card("top"):
    card_header(
        "The 15 best leads",
        "Ranked by the opportunity index: growth, room left, commercial size and evidence",
    )
    headline(ranking_note(result))
    show_chart(_opportunity_chart(result, ranked_label))
    st.page_link(
        "app_pages/explain.py",
        label="Explain any of these in plain language",
        icon=":material/lightbulb:",
    )

with card("all"):
    card_header(
        f"All {len(result)} results",
        "Click a column to sort; hover a column header for what it means",
    )
    display = result.copy()
    for pct_col in PERCENT_COLUMNS:
        display[pct_col] = display[pct_col] * 100
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        row_height=40,
        column_order=_TABLE_ORDER,
        column_config=_column_config(ranked_label),
    )
    _reading_guide()
