"""
Streamlit prototype (Phase 3) surfacing analysis.opportunity_scoring's two
lenses: "for this product, which markets look promising?" (rank_markets)
and "for this country, which products look promising?" (rank_products).
Both lenses share the same parametrized levels/params the scoring module
already exposes - this file is UI wiring, not new analysis logic.

Run with: uv run streamlit run src/dv_beef_exports/app/main.py
"""

from __future__ import annotations

import altair as alt
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

st.set_page_config(page_title="Beef Export Opportunities", layout="wide")

_PRODUCT_LEVEL_LABELS = {
    "ncm_code": "Specific product (NCM code)",
    "category": "Product category",
    "overall": "All beef (overall)",
}
_GEO_LEVEL_LABELS = {
    "country": "Country",
    "region": "Region",
    "trade_bloc": "Trade bloc",
    "overall": "All markets (overall)",
}
_RANKED_COL_LABELS = {
    "ncm_code": "NCM code",
    "category": "Category",
    "country": "Country",
    "region": "Region",
    "trade_bloc": "Trade bloc",
}
_COLUMN_LABELS = {
    "years_active": "Years active",
    "annual_growth_pct": "Growth %/yr",
    "trend_r2_adj": "Trend fit (adj. R²)",
    "coverage_score": "Coverage",
    "volume_confidence": "Volume confidence",
    "confidence": "Confidence %",
    "share_pct": "Share %",
    "opportunity_score": "Opportunity score",
    "total_fob_usd": "Total FOB (USD)",
    "total_kg": "Total KG",
    "unit_price_usd_per_ton": "USD / ton",
}


@st.cache_resource
def _connection() -> duckdb.DuckDBPyConnection:
    return get_connection(DB_PATH)


@st.cache_data
def _product_options(_con: duckdb.DuckDBPyConnection, level: str) -> list[tuple[str, str]]:
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
def _geo_options(_con: duckdb.DuckDBPyConnection, level: str) -> list[str]:
    """Distinct values for the geo dropdown at the given level."""
    if level == "overall":
        return []
    column = GEO_LEVELS[level]
    rows = _con.execute(
        f"SELECT DISTINCT {column} FROM marts.exports WHERE {column} IS NOT NULL ORDER BY {column}"
    ).fetchall()
    return [row[0] for row in rows]


def _sidebar_controls(con: duckdb.DuckDBPyConnection) -> dict:
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
            format_func=lambda lvl: _PRODUCT_LEVEL_LABELS[lvl],
        )
        product_value = None
        if product_level != "overall":
            options = _product_options(con, product_level)
            product_value = st.sidebar.selectbox(
                "Product", options, format_func=lambda pair: pair[1]
            )[0]

        geo_choices = [lvl for lvl in GEO_LEVELS if lvl != "overall"]
        geo_level = st.sidebar.selectbox(
            "Rank markets by", geo_choices, format_func=lambda lvl: _GEO_LEVEL_LABELS[lvl]
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
        format_func=lambda lvl: _GEO_LEVEL_LABELS[lvl],
    )
    geo_value = None
    if geo_level != "overall":
        options = _geo_options(con, geo_level)
        geo_value = st.sidebar.selectbox("Market", options)

    product_choices = [lvl for lvl in PRODUCT_LEVELS if lvl != "overall"]
    product_level = st.sidebar.selectbox(
        "Rank products by",
        product_choices,
        format_func=lambda lvl: _PRODUCT_LEVEL_LABELS[lvl],
    )
    return {
        "mode": "products",
        "geo_level": geo_level,
        "geo_value": geo_value,
        "product_level": product_level,
        "window_years": window_years,
        "min_years_active": min_years_active,
    }


def _run_query(con: duckdb.DuckDBPyConnection, params: dict) -> pd.DataFrame:
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


def _opportunity_chart(result: pd.DataFrame, ranked_col: str) -> alt.LayerChart | alt.FacetChart:
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
            alt.Tooltip(f"{ranked_col}:N", title=_RANKED_COL_LABELS.get(ranked_col, ranked_col)),
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


def main() -> None:
    con = _connection()
    st.title("Beef Export Opportunity Scoring")
    st.caption(
        "opportunity_score = annual growth % × (1 − current share %); "
        "confidence = geometric mean of years-coverage, trend fit, and volume "
        "(docs/decisions/0005-opportunity-scoring-methodology.md)."
    )

    params = _sidebar_controls(con)
    result = _run_query(con, params)

    if result.empty:
        st.warning("No groups cleared the minimum active-years threshold for this selection.")
        return

    ranked_col = result.columns[0]
    ranked_label = _RANKED_COL_LABELS.get(ranked_col, ranked_col)

    # trade_bloc (unlike region) only covers 4 named blocs - most countries
    # belong to none, which surfaces here as a null group, not a bug.
    result[ranked_col] = result[ranked_col].fillna("(no bloc)")

    st.subheader("Top opportunities")
    st.altair_chart(_opportunity_chart(result, ranked_col), width="stretch")

    st.subheader(f"All results ({len(result)})")
    display = result.copy()
    for pct_col in ("annual_growth_pct", "share_pct", "confidence"):
        display[pct_col] = display[pct_col] * 100
    display = display.rename(columns={ranked_col: ranked_label, **_COLUMN_LABELS})

    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Growth %/yr": st.column_config.NumberColumn(format="%.1f%%"),
            "Share %": st.column_config.NumberColumn(format="%.1f%%"),
            "Confidence %": st.column_config.NumberColumn(format="%.0f%%"),
            "Trend fit (adj. R²)": st.column_config.NumberColumn(format="%.2f"),
            "Coverage": st.column_config.NumberColumn(format="%.2f"),
            "Volume confidence": st.column_config.NumberColumn(format="%.2f"),
            "Opportunity score": st.column_config.NumberColumn(format="%.3f"),
            "Total FOB (USD)": st.column_config.NumberColumn(format="$%,.0f"),
            "Total KG": st.column_config.NumberColumn(format="%,.0f"),
            "USD / ton": st.column_config.NumberColumn(format="$%,.0f"),
        },
    )


main()
