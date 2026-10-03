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

import sys
from pathlib import Path

# Streamlit Community Cloud installs the dependencies from uv.lock but isn't
# documented to install this project itself - so put src/ on the import path,
# letting `dv_beef_exports` import from the checkout either way.
_SRC = str(Path(__file__).resolve().parents[2])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import streamlit as st  # noqa: E402

from dv_beef_exports.app.common import connection, run_query, sidebar_controls  # noqa: E402

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
