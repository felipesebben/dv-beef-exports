"""
Smoke tests for the Streamlit prototype, via streamlit.testing.v1.AppTest.

Runs against the real tracked comexstat.duckdb (like the opportunity_scoring
spot-checks in PR #18) rather than a fixture DB: main.py wires
get_connection()/DB_PATH together at import time, so swapping in an isolated
tmp_path DB would mean monkeypatching module-level state the app doesn't
expose as a parameter - more machinery than a UI-wiring smoke test warrants.
These just prove the widgets, queries, and chart/table rendering don't throw
across the app's real code paths; opportunity_scoring's own correctness is
already covered by tests/analysis/test_opportunity_scoring.py.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from dv_beef_exports.app.main import _product_options
from dv_beef_exports.ingestion.duckdb_loader import get_connection

APP_PATH = str(Path(__file__).parents[2] / "src" / "dv_beef_exports" / "app" / "main.py")


def _app() -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    return at


def _first_ncm_option() -> tuple[str, str]:
    """A real (code, label) tuple, matching what main.py's own selectbox
    holds - AppTest's Selectbox.options exposes the *formatted* label
    strings (post format_func), not the underlying value set_value() needs,
    so this goes straight to the same helper the app itself calls."""
    con = get_connection()
    return _product_options(con, "ncm_code")[0]


def test_default_view_renders_ranked_markets_table() -> None:
    at = _app()

    assert not at.exception
    assert len(at.dataframe) == 1
    assert not at.dataframe[0].value.empty


def test_country_to_products_lens_renders() -> None:
    at = _app()

    at.sidebar.radio[0].set_value("Country → Products").run()

    assert not at.exception
    assert len(at.dataframe) == 1


def test_fixing_a_specific_product_renders() -> None:
    at = _app()

    at.sidebar.selectbox[0].set_value("ncm_code").run()
    at.sidebar.selectbox[1].set_value(_first_ncm_option()).run()

    assert not at.exception
    assert len(at.dataframe) == 1


def test_no_qualifying_groups_shows_warning_not_a_crash() -> None:
    at = _app()

    at.sidebar.selectbox[0].set_value("ncm_code").run()
    at.sidebar.selectbox[1].set_value(_first_ncm_option()).run()
    at.sidebar.selectbox[2].set_value("trade_bloc").run()
    at.sidebar.slider[0].set_value(25).run()
    at.sidebar.slider[1].set_value(25).run()

    assert not at.exception
    # Either it renders a (possibly single-row, "(no bloc)") table, or it
    # hits the empty-result branch - both are valid, non-crashing outcomes.
    if at.dataframe:
        assert len(at.dataframe) == 1
    else:
        assert len(at.warning) == 1
