"""
Market overview page: what Brazil sells, how it's trending, and who buys
what - analysis/market_overview.py, in the same trailing 12-month periods as
the opportunity scores. Has its own product/market pickers; the sidebar
query (a ranking query) doesn't apply here.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.market_overview import market_trend, top_destinations_matrix
from dv_beef_exports.app.common import (
    CATEGORY_PHRASES,
    CHART_INK,
    PRODUCT_PHRASES,
    connection,
    geo_label,
    market_scope_options,
    product_phrase,
    product_scope_options,
    style_chart,
)
from dv_beef_exports.app.explanations import (
    concentration_note,
    money,
    overview_summary,
    tons,
)

# measure picker label -> (column, axis title, yoy column, tooltip format)
_MEASURES = {
    "Value (USD)": ("fob_usd", "Total sold in the 12-month period (USD)", "fob_yoy_pct", "$,.0f"),
    "Volume (tons)": ("metric_ton", "Total tons in the 12-month period", "ton_yoy_pct", ",.0f"),
    "Average price ($/t)": (
        "unit_price_usd_per_ton",
        "Average price in the 12-month period (USD/t)",
        "price_yoy_pct",
        "$,.0f",
    ),
    "Destinations": ("destinations", "Countries buying in the 12-month period", None, ",.0f"),
}
# the same picker, applied to the who-buys-what matrix: measure label ->
# (cell column, its yoy column, what ranks a destination as "top", share-of-
# total column for the concentration note, basis for that note). Average
# price ranks by value - by price, tiny niche buyers would crowd out the
# real markets. Destinations has no per-cell meaning (each row already is
# one), so the matrix falls back to value.
_MATRIX_MEASURES = {
    "Value (USD)": ("fob_usd", "fob_yoy_pct", "fob_usd", "share_of_total", "value"),
    "Volume (tons)": ("metric_ton", "ton_yoy_pct", "metric_ton", "ton_share_of_total", "volume"),
    "Average price ($/t)": (
        "unit_price_usd_per_ton",
        "price_yoy_pct",
        "fob_usd",
        "share_of_total",
        "value",
    ),
}
_HIGHEST, _LOWEST, _OTHER = "Highest", "Lowest", "Other periods"
_ROWS = {"Countries": ("country", "country"), "Regions": ("region", "region")}
_ROWS["Trade blocs"] = ("trade_bloc", "trade bloc")
_COLUMNS = {"Products": "ncm_code", "Categories": "category"}


def _md(text: str) -> str:
    """Escape $ - st.markdown renders text between two $ signs as LaTeX."""
    return text.replace("$", "\\$")


def _period_label(df: pd.DataFrame) -> list[str]:
    return [
        f"{start:%b %Y}–{end:%b %Y}"
        for start, end in zip(df["period_start"], df["period_end"], strict=True)
    ]


def _kpis(trend: pd.DataFrame) -> None:
    latest, previous = trend.iloc[-1], trend.iloc[-2]

    def delta(yoy: float) -> str | None:
        return None if pd.isna(yoy) else f"{yoy * 100:+.1f}% vs previous 12 months"

    with st.container(horizontal=True):
        st.metric(
            "Value, last 12 months",
            money(latest["fob_usd"]),
            delta(latest["fob_yoy_pct"]),
            border=True,
            help="Total FOB value of Brazil's exports in scope, latest 12-month period.",
        )
        st.metric(
            "Volume, last 12 months",
            tons(latest["metric_ton"]),
            delta(latest["ton_yoy_pct"]),
            border=True,
            help="Total weight shipped, latest 12-month period.",
        )
        st.metric(
            "Average price",
            "—"
            if pd.isna(latest["unit_price_usd_per_ton"])
            else f"${latest['unit_price_usd_per_ton']:,.0f}/t",
            delta(latest["price_yoy_pct"]),
            border=True,
            help="Total value divided by total weight - an average across every cut and "
            "buyer in scope, so a change can mean a different mix, not just new prices.",
        )
        st.metric(
            "Destinations",
            int(latest["destinations"]),
            f"{int(latest['destinations'] - previous['destinations']):+d} vs previous 12 months",
            border=True,
            help="Countries that bought anything in scope in the latest 12-month period.",
        )


def _value_label(column: str, value: float) -> str:
    if column == "destinations":
        return f"{value:,.0f} countries"
    return _cell_label(column, value)


def _trend_chart(trend: pd.DataFrame, measure: str) -> alt.LayerChart:
    column, title, yoy_column, fmt = _MEASURES[measure]
    values = trend[column].where(trend[column] > 0)
    # highlight the extremes only - a number on every bar goes unread
    highlight = pd.Series(_OTHER, index=trend.index)
    label = pd.Series("", index=trend.index)
    if values.notna().any():
        for idx, name in ((values.idxmin(), _LOWEST), (values.idxmax(), _HIGHEST)):
            highlight[idx] = name
            label[idx] = f"{name}: {_value_label(column, values[idx])}"
    data = trend.assign(period=_period_label(trend), highlight=highlight, label=label)
    top = values.max() if values.notna().any() else 1
    tooltip = [
        alt.Tooltip("period:N", title="Period"),
        alt.Tooltip(f"{column}:Q", title=measure, format=fmt),
    ]
    if yoy_column:
        tooltip.append(alt.Tooltip(f"{yoy_column}:Q", title="vs previous 12 months", format="+.1%"))
    base = alt.Chart(data).encode(
        x=alt.X(
            "period:N",
            sort=None,
            title="12-month period (one bar = the total for all 12 months)",
            axis=alt.Axis(labelAngle=-35),
        ),
        # headroom above the tallest bar for its label
        y=alt.Y(f"{column}:Q", title=title, scale=alt.Scale(domain=[0, top * 1.15])),
        tooltip=tooltip,
    )
    bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        color=alt.Color(
            "highlight:N",
            scale=alt.Scale(
                domain=[_HIGHEST, _LOWEST, _OTHER], range=["#1c5cab", "#eb6834", "#b7d3f6"]
            ),
            legend=None,  # the direct labels name the two highlighted bars
        ),
    )
    labels = (
        base.mark_text(dy=-9, fontSize=12, fontWeight="bold", color=CHART_INK)
        .encode(text="label:N")
        .transform_filter(alt.datum.label != "")
    )
    return (bars + labels).properties(height=420)


def _product_name(level: str, value: str) -> str:
    phrase = PRODUCT_PHRASES[value] if level == "ncm_code" else CATEGORY_PHRASES[value]
    return phrase[:1].upper() + phrase[1:]


def _short_tons(t: float) -> str:
    if t >= 1e6:
        return f"{t / 1e6:.1f}M t"
    if t >= 1e3:
        return f"{t / 1e3:.1f}k t"
    return f"{t:,.0f} t"


def _cell_label(measure_column: str, value: float) -> str:
    if pd.isna(value) or value <= 0:
        return "—"
    if measure_column == "metric_ton":
        return _short_tons(value)
    if measure_column == "unit_price_usd_per_ton":
        return f"${value:,.0f}/t"
    return money(value)


def _matrix_chart(
    matrix: pd.DataFrame, columns_level: str, rows_label: str, measure: str
) -> alt.LayerChart:
    column, yoy_column, _, _, _ = _MATRIX_MEASURES[measure]
    cell_value = matrix[column].where(matrix[column] > 0)
    # colour each product column on its own scale: 100% = the column's top
    # destination. One scale for the whole grid would just say "frozen cuts
    # are big" - products differ in size by orders of magnitude.
    relative = cell_value / cell_value.groupby(matrix["product"]).transform("max")
    data = matrix.assign(
        product_name=[_product_name(columns_level, v) for v in matrix["product"]],
        cell_label=[_cell_label(column, v) for v in cell_value],
        relative=relative,
        is_top=relative == 1,
    )
    destination_order = list(dict.fromkeys(data["destination"]))
    product_order = list(dict.fromkeys(data["product_name"]))
    base = alt.Chart(data).encode(
        x=alt.X(
            "product_name:N",
            sort=product_order,
            title=None,
            axis=alt.Axis(orient="top", labelAngle=-30, labelLimit=180, domain=False),
        ),
        y=alt.Y(
            "destination:N",
            sort=destination_order,
            title=None,
            axis=alt.Axis(domain=False, labelColor="#0b0b0b"),
        ),
        tooltip=[
            alt.Tooltip("destination:N", title=rows_label.capitalize()),
            alt.Tooltip("product_name:N", title="Product"),
            alt.Tooltip("fob_usd:Q", title="Value, last 12 months", format="$,.0f"),
            alt.Tooltip("metric_ton:Q", title="Tons, last 12 months", format=",.0f"),
            alt.Tooltip("unit_price_usd_per_ton:Q", title="Average price ($/t)", format="$,.0f"),
            alt.Tooltip(f"{yoy_column}:Q", title=f"{measure} vs previous 12 months", format="+.0%"),
            alt.Tooltip("relative:Q", title="vs this product's top destination", format=".0%"),
            alt.Tooltip(
                "share_of_destination:Q",
                title=f"Share of this {rows_label}'s purchases (value)",
                format=".1%",
            ),
        ],
    )
    cells = base.mark_rect(stroke="white", strokeWidth=1).encode(
        color=alt.Color(
            "relative:Q",
            scale=alt.Scale(domain=[0, 1], scheme="blues"),
            legend=alt.Legend(title="vs the product's top destination", format=".0%"),
        ),
    )
    # font weight is a mark property in Vega-Lite, not an encoding channel,
    # so the bold top-of-column labels are their own filtered layer
    text_color = alt.condition(alt.datum.relative > 0.55, alt.value("white"), alt.value(CHART_INK))
    labels = (
        base.mark_text(fontSize=11)
        .encode(text="cell_label:N", color=text_color)
        .transform_filter(~alt.datum.is_top)
    )
    top_labels = (
        base.mark_text(fontSize=11, fontWeight="bold")
        .encode(text="cell_label:N", color=text_color)
        .transform_filter(alt.datum.is_top)
    )
    return (cells + labels + top_labels).properties(height=alt.Step(30))


st.title("Market overview")
st.caption(
    "What Brazil sells, how it's trending, and who buys what. Every figure covers a full "
    "12-month period ending at the latest month in the data, so a half-finished calendar "
    "year never looks like a drop."
)

con = connection()
with st.container(horizontal=True):
    product_scope = st.selectbox(
        "Product",
        product_scope_options(con),
        format_func=lambda option: option[2],
        key="overview_product",
        persist_state="session",
    )
    market_scope = st.selectbox(
        "Market",
        market_scope_options(con),
        format_func=lambda option: option[2],
        key="overview_market",
        persist_state="session",
    )

product_level, product_value, _ = product_scope
geo_level, geo_value, _ = market_scope
trend = market_trend(
    con,
    product_level=product_level,
    product_value=product_value,
    geo_level=geo_level,
    geo_value=geo_value,
)
scope = (
    f"Brazil's exports of {product_phrase(product_level, product_value)} "
    f"to {geo_label(geo_level, geo_value)}"
)

with st.container(border=True):
    st.markdown(_md(overview_summary(trend, scope)))
_kpis(trend)

st.subheader("How it's trending")
measure = st.segmented_control(
    "Show",
    list(_MEASURES),
    default="Value (USD)",
    key="overview_measure",
    persist_state="session",
)
st.altair_chart(style_chart(_trend_chart(trend, measure or "Value (USD)")), width="stretch")

st.subheader("Who buys what")
matrix_measure = measure if measure in _MATRIX_MEASURES else "Value (USD)"
ranked_by = "volume" if matrix_measure == "Volume (tons)" else "value"
st.caption(
    f"The biggest destinations by {ranked_by} in the latest 12 months, showing "
    f"**{matrix_measure.lower()}** - follows the measure picked above. "
    "**Each column is coloured on its own**: the darkest cell (in bold) is that product's "
    "top destination, and the others are shaded by how they compare with it - so you can "
    "spot the leading buyer of each product even though products differ hugely in size. "
    "Hover a cell for value, tons, price and the change vs the previous 12 months."
)
if measure == "Destinations":
    st.caption(
        ":material/info: Each row already is one destination, so a count doesn't apply "
        "here - showing value instead."
    )
with st.container(horizontal=True):
    rows_choice = st.segmented_control(
        "Destinations as",
        list(_ROWS),
        default="Countries",
        key="overview_rows",
        persist_state="session",
    )
    columns_choice = st.segmented_control(
        "Products as",
        list(_COLUMNS),
        default="Products",
        key="overview_columns",
        persist_state="session",
    )
    top_n = st.slider(
        "How many destinations",
        min_value=5,
        max_value=25,
        value=10,
        key="overview_top_n",
        persist_state="session",
    )
rows_level, rows_noun = _ROWS[rows_choice or "Countries"]
columns_level = _COLUMNS[columns_choice or "Products"]
matrix = top_destinations_matrix(
    con,
    product_level=product_level,
    product_value=product_value,
    geo_level=geo_level,
    geo_value=geo_value,
    rows=rows_level,
    columns=columns_level,
    top_n=top_n,
    rank_by=_MATRIX_MEASURES[matrix_measure][2],
)
if matrix.empty:
    st.info("No destination bought anything in scope in the latest 12 months.")
else:
    _, _, _, share_column, basis = _MATRIX_MEASURES[matrix_measure]
    note = concentration_note(matrix, rows_noun, share_column=share_column, basis=basis)
    if note:
        st.warning(_md(note), icon=":material/pie_chart:")
    st.altair_chart(
        style_chart(_matrix_chart(matrix, columns_level, rows_noun, matrix_measure)),
        width="stretch",
    )
