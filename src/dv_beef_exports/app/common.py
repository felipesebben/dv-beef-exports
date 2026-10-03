"""
Shared pieces of the Streamlit app: the DB connection, the sidebar query
controls (rendered once by main.py, so every page sees the same selection),
the query itself, and display labels/formats.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.opportunity_scoring import (
    GEO_LEVELS,
    PRODUCT_LEVELS,
    rank_markets,
    rank_products,
)
from dv_beef_exports.ingestion.duckdb_loader import DB_PATH, get_connection

PRODUCT_LEVEL_LABELS = {
    "ncm_code": "Specific product (NCM code)",
    "category": "Product category",
    "overall": "All beef (overall)",
}
GEO_LEVEL_LABELS = {
    "country": "Country",
    "region": "Region",
    "trade_bloc": "Trade bloc",
    "overall": "All markets (overall)",
}
RANKED_COL_LABELS = {
    "ncm_code": "NCM code",
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
COLUMN_LABELS = {
    "years_active": "Years active",
    "annual_growth_pct": "Growth %/yr",
    "trend_r2_adj": "Trend fit (adj. R²)",
    "coverage_score": "Coverage",
    "volume_confidence": "Volume confidence",
    "confidence": "Confidence %",
    "share_pct": "Share %",
    "opportunity_score": "Opportunity score",
    "total_fob_usd": "Total FOB (USD)",
    "total_metric_ton": "Total tons",
    "unit_price_usd_per_ton": "USD / ton",
}
# percent columns are scaled x100 before display, hence "%%" formats
COLUMN_FORMATS = {
    "years_active": "%d",
    "annual_growth_pct": "%.1f%%",
    "trend_r2_adj": "%.2f",
    "coverage_score": "%.2f",
    "volume_confidence": "%.2f",
    "confidence": "%.0f%%",
    "share_pct": "%.1f%%",
    "opportunity_score": "%.3f",
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
        return "beef (all tracked products)"
    if level == "ncm_code":
        return PRODUCT_PHRASES[value]
    return CATEGORY_PHRASES[value]


def geo_label(level: str, value: str | None) -> str:
    if level == "overall":
        return "all markets"
    return value if value is not None else NO_BLOC_LABEL


def sidebar_controls(con: duckdb.DuckDBPyConnection) -> dict:
    st.sidebar.header("Query")
    lens = st.sidebar.radio("Lens", ["Product → Markets", "Country → Products"])

    window_years = st.sidebar.slider("Trailing window (years)", min_value=4, max_value=25, value=10)
    min_years_active = st.sidebar.slider(
        "Minimum active years",
        min_value=4,
        max_value=window_years,
        value=4,
        help="Adjusted R² is unstable below ~4 active years (ADR 0005 amendment).",
    )

    if lens == "Product → Markets":
        product_level = st.sidebar.selectbox(
            "Fix product",
            list(PRODUCT_LEVELS),
            index=list(PRODUCT_LEVELS).index("overall"),
            format_func=lambda lvl: PRODUCT_LEVEL_LABELS[lvl],
        )
        product_value = None
        if product_level != "overall":
            options = product_options(con, product_level)
            product_value = st.sidebar.selectbox(
                "Product", options, format_func=lambda pair: pair[1]
            )[0]

        geo_choices = [lvl for lvl in GEO_LEVELS if lvl != "overall"]
        geo_level = st.sidebar.selectbox(
            "Rank markets by", geo_choices, format_func=lambda lvl: GEO_LEVEL_LABELS[lvl]
        )
        return {
            "mode": "markets",
            "product_level": product_level,
            "product_value": product_value,
            "geo_level": geo_level,
            "window_years": window_years,
            "min_years_active": min_years_active,
        }

    geo_level = st.sidebar.selectbox(
        "Fix market",
        list(GEO_LEVELS),
        index=list(GEO_LEVELS).index("overall"),
        format_func=lambda lvl: GEO_LEVEL_LABELS[lvl],
    )
    geo_value = None
    if geo_level != "overall":
        options = geo_options(con, geo_level)
        geo_value = st.sidebar.selectbox("Market", options)

    product_choices = [lvl for lvl in PRODUCT_LEVELS if lvl != "overall"]
    product_level = st.sidebar.selectbox(
        "Rank products by",
        product_choices,
        format_func=lambda lvl: PRODUCT_LEVEL_LABELS[lvl],
    )
    return {
        "mode": "products",
        "geo_level": geo_level,
        "geo_value": geo_value,
        "product_level": product_level,
        "window_years": window_years,
        "min_years_active": min_years_active,
    }


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
