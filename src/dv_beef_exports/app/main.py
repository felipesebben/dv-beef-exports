"""
Streamlit prototype (Phase 3) surfacing analysis.opportunity_scoring's two
lenses: "for this product, which markets look promising?" (rank_markets)
and "for this country, which products look promising?" (rank_products).

Entry point for a two-page app. The sidebar query and the query itself run
here, before navigation, so both pages share one selection:
- app_pages/opportunities.py - the ranked results
- app_pages/explain.py - the same results explained in plain language

Run with: uv run streamlit run src/dv_beef_exports/app/main.py
"""

from __future__ import annotations

import streamlit as st

from dv_beef_exports.app.common import connection, run_query, sidebar_controls

st.set_page_config(page_title="Beef Export Opportunities", layout="wide")

page = st.navigation(
    [
        st.Page(
            "app_pages/opportunities.py",
            title="Opportunities",
            icon=":material/leaderboard:",
            default=True,
        ),
        st.Page(
            "app_pages/explain.py",
            title="Explain the numbers",
            icon=":material/lightbulb:",
        ),
    ],
    position="top",
)

con = connection()
query = sidebar_controls(con)
st.session_state.query = query
st.session_state.result = run_query(con, query)

page.run()
