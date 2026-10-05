"""
Shared pieces of the Streamlit app: the DB connection, the sidebar query
controls (rendered once by main.py, so every page sees the same selection),
the query itself, and display labels/formats.
"""

from __future__ import annotations

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import (
    GEO_LEVELS,
    rank_markets,
    rank_products,
)
from dv_beef_exports.ingestion.duckdb_loader import DB_PATH, get_connection

RANKED_COL_LABELS = {
    "ncm_code": "Product",
    "category": "Category",
    "country": "Country",
    "region": "Region",
    "trade_bloc": "Trade bloc",
}
# singular nouns for the ranked axis, for sentences ("52 countries ranked")
RANKED_NOUNS = {
    "ncm_code": "product",
    "category": "product category",
    "country": "country",
    "region": "region",
    "trade_bloc": "trade bloc",
}
# Business-language names, matching the Explain page's card titles - no
# "adj. R²" or "FOB" in the table a business reader sees first.
COLUMN_LABELS = {
    "years_active": "Years with sales",
    "annual_growth_pct": "Growth / yr",
    "trend_r2_adj": "Trend steadiness",
    "coverage_score": "History",
    "volume_confidence": "Size vs. typical",
    "confidence": "Confidence",
    "share_pct": "Share",
    "opportunity_score": "Score",
    "total_fob_usd": "Value",
    "total_metric_ton": "Tons",
    "unit_price_usd_per_ton": "Avg. price ($/t)",
}
# percent columns are scaled x100 before display, hence "%%" formats
COLUMN_FORMATS = {
    "years_active": "%d",
    "annual_growth_pct": "%+.0f%%",
    "trend_r2_adj": "%.2f",
    "coverage_score": "%.2f",
    "volume_confidence": "%.2f",
    "confidence": "%.0f%%",
    "share_pct": "%.1f%%",
    "opportunity_score": "%.2f",
    "total_fob_usd": "$%,.0f",
    "total_metric_ton": "%,.0f",
    "unit_price_usd_per_ton": "$%,.0f",
}
PERCENT_COLUMNS = ("annual_growth_pct", "share_pct", "confidence")
# How each product reads mid-sentence ("Brazil's exports of frozen livers
# to Singapore") - ComexStat's list-style descriptions ("Livers, frozen")
# don't, and no single reordering rule fixes all 11.
PRODUCT_PHRASES = {
    "02021000": "frozen carcasses and half-carcasses",
    "02022010": "frozen bone-in forequarters",
    "02022020": "frozen bone-in hindquarters",
    "02022090": "other frozen bone-in cuts",
    "02023000": "frozen boneless beef",
    "02062100": "frozen tongues",
    "02062200": "frozen livers",
    "02062910": "frozen tails",
    "02062990": "other frozen edible offal",
    "02102000": "salted, dried or smoked beef",
    "16025000": "prepared or preserved beef",
}
CATEGORY_PHRASES = {
    "frozen": "frozen beef cuts",
    "offal": "frozen offal",
    "salted_dried": "salted and dried beef",
    "processed": "processed beef",
}


# One shared period label for every chart axis - short enough for a phone,
# while still naming both ends so a bar never reads as a single month.
def period_label(start: pd.Timestamp, end: pd.Timestamp) -> str:
    """Sep '25–Aug '26"""
    return f"{start:%b '%y}–{end:%b '%y}"


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def display_name(level: str, value: str | None) -> str:
    """How a ranked item is shown in charts and tables: products by name
    ("Frozen livers"), never by NCM code; geographies as they are."""
    if level == "ncm_code":
        return capitalize(PRODUCT_PHRASES.get(value, value))
    if level == "category":
        return capitalize(CATEGORY_PHRASES.get(value, value))
    return value if value is not None else NO_BLOC_LABEL


# Compact axis labels: "$20B", "3M t" instead of "20,000,000,000". d3's SI
# format says G for billions; business readers expect B.
MONEY_AXIS_LABELS = "replace(format(datum.value, '$~s'), 'G', 'B')"
TONS_AXIS_LABELS = "replace(format(datum.value, '~s'), 'G', 'B') + ' t'"


# Chart chrome is deliberately recessive, so the ink goes to the data:
# hairline gridlines on the value axis only, no ticks, muted axis text, and
# axis titles anchored at the tip of the axis rather than its middle.
CHART_INK = "#52514e"
CHART_MUTED = "#898781"
CHART_GRID = "#ecebe7"
CHART_DOMAIN = "#d5d4ce"


def style_chart(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    """Apply the app's shared chart style. Call on the finished (layered)
    chart, right before st.altair_chart."""
    return (
        chart.configure_axis(
            grid=True,
            gridColor=CHART_GRID,
            gridWidth=1,
            domainColor=CHART_DOMAIN,
            ticks=False,
            labelColor=CHART_MUTED,
            labelFontSize=11,
            labelPadding=6,
            titleColor=CHART_INK,
            titleFontSize=11,
            titleFontWeight="normal",
            titleAnchor="end",
            titlePadding=10,
        )
        # categories (bars, heatmap rows/columns) never get gridlines
        .configure_axisBand(grid=False)
        .configure_view(strokeWidth=0)
        .configure_legend(labelColor=CHART_INK, titleColor=CHART_INK, labelFontSize=11)
        # every chart is titled: the title names it, the subtitle says how to read it
        .configure_title(
            anchor="start",
            color="#0b0b0b",
            fontSize=13,
            fontWeight=600,
            subtitleColor=CHART_MUTED,
            subtitleFontSize=11,
            offset=10,
        )
    )


# trade_bloc (unlike region) only covers 4 named blocs - most countries
# belong to none, which surfaces as a null group, not a bug.
NO_BLOC_LABEL = "(no bloc)"


@st.cache_resource
def connection() -> duckdb.DuckDBPyConnection:
    return get_connection(DB_PATH)


@st.cache_data
def product_options(_con: duckdb.DuckDBPyConnection, level: str) -> list[tuple[str, str]]:
    """(value, label) pairs for the product dropdown at the given level."""
    if level == "ncm_code":
        rows = _con.execute(
            "SELECT ncm_code, description_en FROM staging.dim_ncm ORDER BY description_en"
        ).fetchall()
        return [(code, f"{name} ({code})") for code, name in rows]
    if level == "category":
        rows = _con.execute(
            "SELECT DISTINCT category FROM staging.dim_ncm ORDER BY category"
        ).fetchall()
        return [(row[0], row[0]) for row in rows]
    return []


@st.cache_data
def geo_options(_con: duckdb.DuckDBPyConnection, level: str) -> list[str]:
    """Distinct values for the geo dropdown at the given level."""
    if level == "overall":
        return []
    column = GEO_LEVELS[level]
    rows = _con.execute(
        f"SELECT DISTINCT {column} FROM marts.exports WHERE {column} IS NOT NULL ORDER BY {column}"
    ).fetchall()
    return [row[0] for row in rows]


@st.cache_data
def ncm_names(_con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(_con.execute("SELECT ncm_code, description_en FROM staging.dim_ncm").fetchall())


def product_label(con: duckdb.DuckDBPyConnection, level: str, value: str | None) -> str:
    """A product (fixed or ranked) in words: an NCM code's name, a category,
    or all beef."""
    if level == "overall":
        return "all tracked beef products"
    if level == "ncm_code":
        return ncm_names(con).get(value, value)
    return f"{value} beef products"


def product_phrase(level: str, value: str | None) -> str:
    """A product as it reads mid-sentence, e.g. "frozen livers"."""
    if level == "overall":
        return "beef"
    if level == "ncm_code":
        return PRODUCT_PHRASES[value]
    return CATEGORY_PHRASES[value]


@st.cache_data
def product_scope_options(_con: duckdb.DuckDBPyConnection) -> list[tuple[str, str | None, str]]:
    """(level, value, label) for the overview page's product picker: all
    beef, then each category, then each NCM product."""
    options = [("overall", None, "All beef products")]
    options += [
        ("category", value, f"All {CATEGORY_PHRASES[value]}")
        for value, _ in product_options(_con, "category")
    ]
    options += [
        ("ncm_code", code, f"{display_name('ncm_code', code)} (NCM {code})")
        for code, _ in sorted(
            product_options(_con, "ncm_code"), key=lambda pair: PRODUCT_PHRASES[pair[0]]
        )
    ]
    return options


@st.cache_data
def market_scope_options(_con: duckdb.DuckDBPyConnection) -> list[tuple[str, str | None, str]]:
    """(level, value, label) for the overview page's market picker: all
    markets, then regions, trade blocs, and countries."""
    options = [("overall", None, "All markets")]
    options += [("region", v, f"Region: {v}") for v in geo_options(_con, "region")]
    options += [("trade_bloc", v, f"Trade bloc: {v}") for v in geo_options(_con, "trade_bloc")]
    options += [("country", v, v) for v in geo_options(_con, "country")]
    return options


def geo_label(level: str, value: str | None) -> str:
    if level == "overall":
        return "all markets"
    return value if value is not None else NO_BLOC_LABEL


def _keep(name: str) -> dict:
    """Widget kwargs that keep a sidebar value for the whole session - the
    sidebar isn't rendered on the overview page, and without this Streamlit
    would reset the query when the user comes back."""
    return {"key": f"query_{name}", "persist_state": "session"}


_COMPARE_GEO = {"Countries": "country", "Regions": "region", "Trade blocs": "trade_bloc"}
_COMPARE_PRODUCT = {"Products": "ncm_code", "Categories": "category"}
_GOALS = ("Best markets", "Best products")


def sidebar_controls(con: duckdb.DuckDBPyConnection) -> dict:
    """The ranking query, shared by the Opportunities and Explain pages.

    One searchable picker per side (the same options as the overview page)
    instead of a level dropdown followed by a value dropdown, and plain
    labels - the reader is a business user, not an analyst.
    """
    sb = st.sidebar
    sb.header("What to rank")
    goal = sb.segmented_control(
        "Find the",
        _GOALS,
        default=_GOALS[0],
        required=True,
        width="stretch",
        **_keep("goal"),
    )

    if goal == _GOALS[0]:
        product_level, product_value, _ = sb.selectbox(
            "For this product",
            product_scope_options(con),
            format_func=lambda option: option[2],
            **_keep("product"),
        )
        compare = sb.segmented_control(
            "Compare",
            list(_COMPARE_GEO),
            default="Countries",
            required=True,
            width="stretch",
            **_keep("compare_geo"),
        )
        query = {
            "mode": "markets",
            "product_level": product_level,
            "product_value": product_value,
            "geo_level": _COMPARE_GEO[compare],
        }
    else:
        geo_level, geo_value, _ = sb.selectbox(
            "In this market",
            market_scope_options(con),
            format_func=lambda option: option[2],
            **_keep("market"),
        )
        compare = sb.segmented_control(
            "Compare",
            list(_COMPARE_PRODUCT),
            default="Products",
            required=True,
            width="stretch",
            **_keep("compare_product"),
        )
        query = {
            "mode": "products",
            "geo_level": geo_level,
            "geo_value": geo_value,
            "product_level": _COMPARE_PRODUCT[compare],
        }

    with sb.expander("Advanced", icon=":material/tune:"):
        window_years = st.slider(
            "Years of history to use",
            min_value=4,
            max_value=25,
            value=10,
            help="How far back the growth trend looks. Longer is steadier; shorter reacts "
            "faster to recent change.",
            **_keep("window_years"),
        )
        min_years_active = st.slider(
            "Minimum years with sales",
            min_value=4,
            max_value=window_years,
            value=4,
            help="Leave out anything that sold in fewer years than this - too little "
            "history to call it a trend.",
            **_keep("min_years_active"),
        )
    return {**query, "window_years": window_years, "min_years_active": min_years_active}


def query_summary(query: dict) -> str:
    """The current ranking query in one line, for the top of each page - on a
    phone the sidebar is hidden behind the menu."""
    if query["mode"] == "markets":
        ranked = {"country": "countries", "region": "regions", "trade_bloc": "trade blocs"}
        subject = (
            f"Best **{ranked[query['geo_level']]}** for "
            f"**{product_phrase(query['product_level'], query['product_value'])}**"
        )
    else:
        ranked = {"ncm_code": "products", "category": "product categories"}
        subject = (
            f"Best **{ranked[query['product_level']]}** in "
            f"**{geo_label(query['geo_level'], query['geo_value'])}**"
        )
    return (
        f":material/tune: {subject} · {query['window_years']} years of history · "
        "change it in the sidebar (☰ on a phone)"
    )


def run_query(con: duckdb.DuckDBPyConnection, params: dict) -> pd.DataFrame:
    if params["mode"] == "markets":
        return rank_markets(
            con,
            product_level=params["product_level"],
            product_value=params["product_value"],
            geo_level=params["geo_level"],
            window_years=params["window_years"],
            min_years_active=params["min_years_active"],
        )
    return rank_products(
        con,
        geo_level=params["geo_level"],
        geo_value=params["geo_value"],
        product_level=params["product_level"],
        window_years=params["window_years"],
        min_years_active=params["min_years_active"],
    )
