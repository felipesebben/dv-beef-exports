"""Opportunities page: the ranked results for the sidebar's query."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import METRIC_GLOSSARY, latest_period_end
from dv_beef_exports.app.common import (
    COLUMN_FORMATS,
    COLUMN_LABELS,
    NO_BLOC_LABEL,
    PERCENT_COLUMNS,
    RANKED_COL_LABELS,
    connection,
)


def _opportunity_chart(result: pd.DataFrame, ranked_col: str) -> alt.LayerChart:
    top = result.nlargest(15, "opportunity_score").sort_values("opportunity_score")
    base = alt.Chart(top).encode(
        x=alt.X(
            "opportunity_score:Q",
            title="Opportunity score",
            axis=alt.Axis(
                gridColor="#e1e0d9",
                domainColor="#c3c2b7",
                labelColor="#898781",
                titleColor="#52514e",
            ),
        ),
        y=alt.Y(
            f"{ranked_col}:N",
            sort="-x",
            title=None,
            axis=alt.Axis(labelColor="#0b0b0b", domain=False, ticks=False),
        ),
        tooltip=[
            alt.Tooltip(f"{ranked_col}:N", title=RANKED_COL_LABELS.get(ranked_col, ranked_col)),
            alt.Tooltip("opportunity_score:Q", title="Opportunity score", format=".3f"),
            alt.Tooltip("confidence:Q", title="Confidence", format=".0%"),
            alt.Tooltip("annual_growth_pct:Q", title="Growth %/yr", format=".1%"),
            alt.Tooltip("share_pct:Q", title="Share %", format=".1%"),
        ],
    )
    bars = base.mark_bar(color="#2a78d6", cornerRadiusEnd=4)
    labels = base.mark_text(align="left", dx=4, color="#52514e").encode(
        text=alt.Text("opportunity_score:Q", format=".2f")
    )
    return (bars + labels).properties(height=alt.Step(22))


def _results_column_config(ranked_col: str, ranked_label: str) -> dict:
    """Label, number format and hover tooltip for every results column. The
    tooltips come from METRIC_GLOSSARY - the same one-liners as the
    methodology doc's quick reference."""
    config: dict = {ranked_col: st.column_config.TextColumn(ranked_label)}
    for col, label in COLUMN_LABELS.items():
        config[col] = st.column_config.NumberColumn(
            label, format=COLUMN_FORMATS[col], help=METRIC_GLOSSARY[col]
        )
    return config


def _reading_guide() -> None:
    with st.expander("How to read these numbers", icon=":material/help:"):
        st.markdown(
            "Hover a column header for what it means. The score is a **lead "
            "generator for manual research, never the decision**:\n\n"
            "1. **Opportunity score** narrows the list - it is a ranking key, "
            "not a magnitude, and for any market under ~5% share it is "
            "effectively just the growth rate.\n"
            "2. **Confidence** says which leads are backed by evidence.\n"
            "3. **Total tons and FOB** say which are worth a sales conversation "
            "- a high score and high confidence can still be a tiny market.\n\n"
            "| Confidence | Reading |\n"
            "| --- | --- |\n"
            "| 70% and above | well-evidenced - history, fit, and size all hold up |\n"
            "| 40% - 70% | one leg is weak; check coverage, trend fit and volume |\n"
            "| below 40% | thin evidence - treat the score as a hypothesis only |\n\n"
            "Full reference, with worked examples and known distortions: "
            "`docs/analysis-methodology.md`."
        )
        st.page_link(
            "app_pages/explain.py",
            label="Explain any result in plain language",
            icon=":material/lightbulb:",
        )


st.title("Beef export opportunities")
st.caption(
    "opportunity_score = annual growth % × (1 − current share %); "
    "confidence = geometric mean of years-coverage, trend fit, and volume "
    "(docs/decisions/0005-opportunity-scoring-methodology.md)."
)
end_year, end_month = latest_period_end(connection())
st.caption(
    f"Each year is a trailing 12-month period ending {date(end_year, end_month, 1):%b %Y}, "
    "so a partial calendar year is never scored as a full one."
)

result = st.session_state.result
if result.empty:
    st.warning("No groups cleared the minimum active-years threshold for this selection.")
    st.stop()

ranked_col = result.columns[0]
ranked_label = RANKED_COL_LABELS.get(ranked_col, ranked_col)
result = result.assign(**{ranked_col: result[ranked_col].fillna(NO_BLOC_LABEL)})

st.subheader("Top opportunities")
st.altair_chart(_opportunity_chart(result, ranked_col), width="stretch")

st.subheader(f"All results ({len(result)})")
display = result.copy()
for pct_col in PERCENT_COLUMNS:
    display[pct_col] = display[pct_col] * 100

st.dataframe(
    display,
    width="stretch",
    hide_index=True,
    column_config=_results_column_config(ranked_col, ranked_label),
)
_reading_guide()
