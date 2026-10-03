"""
Streamlit prototype (Phase 3) over the Phase 2 analysis.

Entry point for a three-page app:
- app_pages/overview.py - market overview (analysis.market_overview), with
  its own product/market pickers
- app_pages/opportunities.py - opportunity ranking (analysis.opportunity_scoring)
- app_pages/explain.py - the same ranking explained in plain language

The two ranking pages share one sidebar query, rendered and run here
before the page, so they always show the same selection. The overview page
doesn't use it, so the sidebar is hidden there; its widgets keep their
values across the switch (persist_state="session" in sidebar_controls).

Run with: uv run streamlit run src/dv_beef_exports/app/main.py
"""

from __future__ import annotations

import streamlit as st

from dv_beef_exports.app.common import connection, run_query, sidebar_controls

st.set_page_config(page_title="Beef Export Opportunities", layout="wide")

overview = st.Page(
    "app_pages/overview.py",
    title="Market overview",
    icon=":material/public:",
    default=True,
)
page = st.navigation(
    [
        overview,
        st.Page(
            "app_pages/opportunities.py",
            title="Opportunities",
            icon=":material/leaderboard:",
        ),
        st.Page(
            "app_pages/explain.py",
            title="Explain the numbers",
            icon=":material/lightbulb:",
        ),
    ],
    position="top",
)

if page.title != overview.title:
    con = connection()
    query = sidebar_controls(con)
    st.session_state.query = query
    st.session_state.result = run_query(con, query)

page.run()
