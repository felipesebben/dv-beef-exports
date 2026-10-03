"""
Smoke tests for the Streamlit prototype, via streamlit.testing.v1.AppTest.

Runs against the real tracked comexstat.duckdb (like the opportunity_scoring
spot-checks in PR #18) rather than a fixture DB: common.connection() wires
get_connection()/DB_PATH together without a parameter, so swapping in an isolated
tmp_path DB would mean monkeypatching module-level state the app doesn't
expose as a parameter - more machinery than a UI-wiring smoke test warrants.
These just prove the widgets, queries, and chart/table rendering don't throw
across the app's real code paths; opportunity_scoring's own correctness is
already covered by tests/analysis/test_opportunity_scoring.py.
"""

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dv_beef_exports.analysis.opportunity_scoring import _OUTPUT_COLUMNS
from dv_beef_exports.app.common import (
    COLUMN_FORMATS,
    COLUMN_LABELS,
    product_options,
    product_scope_options,
)
from dv_beef_exports.ingestion.duckdb_loader import get_connection

APP_PATH = str(Path(__file__).parents[2] / "src" / "dv_beef_exports" / "app" / "main.py")


def _overview() -> AppTest:
    """The app as it opens: the market overview landing page."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    return at


def _app() -> AppTest:
    """The Opportunities page, where the sidebar query lives."""
    at = _overview()
    at.switch_page("app_pages/opportunities.py").run(timeout=30)
    return at


def _first_ncm_option() -> tuple[str, str]:
    """A real (code, label) tuple, matching what main.py's own selectbox
    holds - AppTest's Selectbox.options exposes the *formatted* label
    strings (post format_func), not the underlying value set_value() needs,
    so this goes straight to the same helper the app itself calls."""
    con = get_connection()
    return product_options(con, "ncm_code")[0]


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


def test_every_output_column_has_a_label_and_format() -> None:
    """Regression: after the total_kg -> total_metric_ton rename, the label
    dict kept the old key, so the column silently showed its raw name with
    no formatting."""
    assert set(COLUMN_LABELS) == set(_OUTPUT_COLUMNS)
    assert set(COLUMN_FORMATS) == set(_OUTPUT_COLUMNS)


def test_reading_guide_expander_renders() -> None:
    at = _app()

    assert not at.exception
    # AppTest files an expander that has an icon under its Status block
    # type, not at.expander - so check the guide's content instead
    assert any("Hover a column header" in m.value for m in at.markdown)


def _explain_page(at: AppTest) -> AppTest:
    at.switch_page("app_pages/explain.py").run(timeout=30)
    return at


def test_explain_page_renders_bottom_line_and_every_metric() -> None:
    at = _explain_page(_app())

    assert not at.exception
    assert at.title[0].value == "Explain the numbers"
    # 9 metric cards, each with an st.metric
    assert len(at.metric) == 9
    assert any("Bottom line" in s.value for s in at.subheader)


def test_explain_page_uses_the_shared_sidebar_selection() -> None:
    at = _app()
    at.sidebar.selectbox[0].set_value("ncm_code").run()
    at.sidebar.selectbox[1].set_value(("02062200", "Livers, frozen (02062200)")).run()
    at = _explain_page(at)

    assert not at.exception
    # the default subject is the #1 market for frozen livers
    assert at.selectbox[0].value == "Singapore"
    assert any("frozen livers to Singapore" in m.value for m in at.markdown)


def test_explain_page_escapes_every_dollar_sign() -> None:
    """st.markdown renders text between two $ as a LaTeX formula, which
    garbled sentences like "$309k and 182 t - about $38.6k a year"."""
    at = _app()
    at.sidebar.selectbox[0].set_value("ncm_code").run()
    at.sidebar.selectbox[1].set_value(("02062200", "Livers, frozen (02062200)")).run()
    at = _explain_page(at)

    texts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    assert any("\\$" in t for t in texts)
    assert not [t for t in texts if re.search(r"(?<!\\)\$", t)]


def test_explain_page_with_no_results_shows_info_not_a_crash() -> None:
    at = _app()
    at.sidebar.selectbox[0].set_value("ncm_code").run()
    at.sidebar.selectbox[1].set_value(_first_ncm_option()).run()
    at.sidebar.slider[0].set_value(25).run()
    at.sidebar.slider[1].set_value(25).run()
    at = _explain_page(at)

    assert not at.exception


def test_app_opens_on_market_overview_without_the_ranking_sidebar() -> None:
    at = _overview()

    assert not at.exception
    assert at.title[0].value == "Market overview"
    assert len(at.metric) == 4
    assert any("came to" in m.value for m in at.markdown)
    # the ranking query doesn't apply here, so its sidebar isn't rendered
    assert len(at.sidebar.radio) == 0


def test_overview_follows_its_own_product_picker() -> None:
    at = _overview()
    livers = next(o for o in product_scope_options(get_connection()) if o[1] == "02062200")

    at.selectbox(key="overview_product").set_value(livers).run()

    assert not at.exception
    assert any("frozen livers" in m.value for m in at.markdown)


def test_overview_alternate_matrix_axes_render() -> None:
    at = _overview()

    at.segmented_control(key="overview_rows").set_value("Regions").run()
    at.segmented_control(key="overview_columns").set_value("Categories").run()
    at.segmented_control(key="overview_measure").set_value("Average price ($/t)").run()

    assert not at.exception


def test_sidebar_query_survives_a_visit_to_the_overview() -> None:
    at = _app()
    at.sidebar.radio[0].set_value("Country → Products").run()

    at.switch_page("app_pages/overview.py").run(timeout=30)
    at.switch_page("app_pages/opportunities.py").run(timeout=30)

    assert not at.exception
    assert at.sidebar.radio[0].value == "Country → Products"


def test_who_buys_what_follows_the_measure_picker() -> None:
    at = _overview()

    at.segmented_control(key="overview_measure").set_value("Volume (tons)").run()
    volume_captions = [c.value for c in at.caption]
    at.segmented_control(key="overview_measure").set_value("Destinations").run()
    destination_captions = [c.value for c in at.caption]

    assert not at.exception
    assert any("biggest destinations by volume" in c for c in volume_captions)
    # a per-cell destination count is meaningless, so the matrix falls back to value
    assert any("showing value instead" in c for c in destination_captions)
