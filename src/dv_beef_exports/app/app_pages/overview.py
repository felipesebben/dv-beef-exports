"""
Market overview page: what Brazil sells, how it's trending, and who buys
what - analysis/market_overview.py, in the same trailing 12-month periods as
the opportunity scores. Has its own product/market pickers; the sidebar
query (a ranking query) doesn't apply here.
"""

from __future__ import annotations

from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.market_overview import market_trend, top_destinations_matrix
from dv_beef_exports.app.common import (
    CHART_INK,
    MONEY_AXIS_LABELS,
    TONS_AXIS_LABELS,
    connection,
    display_name,
    geo_label,
    market_scope_options,
    period_label,
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
# the measure as a plain noun, for sentences ("showing volume")
_MEASURE_NOUNS = {
    "Value (USD)": "value",
    "Volume (tons)": "volume",
    "Average price ($/t)": "average price",
    "Destinations": "destinations",
}
_AXIS_LABELS = {
    "fob_usd": MONEY_AXIS_LABELS,
    "metric_ton": TONS_AXIS_LABELS,
    "unit_price_usd_per_ton": "format(datum.value, '$,.0f')",
}
_ROWS = {"Countries": ("country", "country"), "Regions": ("region", "region")}
_ROWS["Trade blocs"] = ("trade_bloc", "trade bloc")
_COLUMNS = {"Products": "ncm_code", "Categories": "category"}


def _md(text: str) -> str:
    """Escape $ - st.markdown renders text between two $ signs as LaTeX."""
    return text.replace("$", "\\$")


def _period_label(df: pd.DataFrame) -> list[str]:
    return [
        period_label(start, end)
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
            title="Each bar = the total for one 12-month period",
            axis=alt.Axis(labelAngle=-40),
        ),
        # headroom above the tallest bar for its label
        y=alt.Y(
            f"{column}:Q",
            title=title,
            scale=alt.Scale(domain=[0, top * 1.15]),
            axis=alt.Axis(labelExpr=_AXIS_LABELS[column]) if column in _AXIS_LABELS else alt.Axis(),
        ),
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
    noun = _MEASURE_NOUNS[measure]
    return (bars + labels).properties(
        title=alt.TitleParams(
            text=f"{noun[:1].upper()}{noun[1:]}, each 12-month period",
            subtitle="Highest and lowest periods labelled",
            anchor="start",
        ),
        height=420,
    )


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


# The who-buys-what matrix is an HTML table, not a chart: it scrolls sideways
# on a phone with the destination column pinned, where an 11-column chart
# just squeezes every cell. Each product column is shaded on its own scale.
_SHADE_LOW = (232, 241, 252)  # near-white blue
_SHADE_HIGH = (28, 92, 171)  # #1c5cab
_MATRIX_CSS = """
<style>
.bm-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
  border: 1px solid #ecebe7; border-radius: 8px; }
.bm { border-collapse: separate; border-spacing: 0; width: 100%;
  font-size: 13px; font-variant-numeric: tabular-nums; }
.bm th, .bm td { padding: 7px 10px; white-space: nowrap; text-align: right;
  border-bottom: 1px solid #ffffff; }
.bm thead th { color: #52514e; font-weight: 600; font-size: 12px; white-space: normal;
  min-width: 84px; max-width: 120px; vertical-align: bottom;
  border-bottom: 1px solid #d5d4ce; background: #ffffff; }
.bm th[scope="row"], .bm th.bm-corner { position: sticky; left: 0; z-index: 1;
  background: #ffffff; text-align: left; color: #0b0b0b; font-weight: 600;
  border-right: 1px solid #d5d4ce; }
.bm td.bm-empty { color: #c3c2b7; }
.bm td.bm-top { font-weight: 700; }
.bm-legend { font-size: 12px; color: #898781; margin: 8px 2px 0; }
.bm-swatch { display: inline-block; width: 72px; height: 8px; border-radius: 4px;
  vertical-align: middle; margin: 0 6px;
  background: linear-gradient(90deg, rgb(232,241,252), rgb(28,92,171)); }
</style>
"""


def _shade(relative: float) -> str:
    rgb = (round(lo + (hi - lo) * relative) for lo, hi in zip(_SHADE_LOW, _SHADE_HIGH, strict=True))
    return "rgb({},{},{})".format(*rgb)


def _cell_tooltip(row: pd.Series, destination_noun: str, yoy_column: str, measure: str) -> str:
    def pct(value: float, fmt: str) -> str:
        return "n/a" if pd.isna(value) else format(value, fmt)

    lines = [
        f"{row['destination']} · {row['product_name']}",
        f"Value: {money(row['fob_usd'])}" if row["fob_usd"] > 0 else "Value: none",
        f"Tons: {_short_tons(row['metric_ton'])}" if row["metric_ton"] > 0 else "Tons: none",
        "Avg. price: n/a"
        if pd.isna(row["unit_price_usd_per_ton"])
        else f"Avg. price: ${row['unit_price_usd_per_ton']:,.0f}/t",
        f"{measure} vs previous 12 months: {pct(row[yoy_column], '+.0%')}",
        f"Share of this {destination_noun}'s purchases: {pct(row['share_of_destination'], '.1%')}",
    ]
    return "&#10;".join(escape(line) for line in lines)


def _matrix_html(
    matrix: pd.DataFrame, columns_level: str, destination_noun: str, measure: str
) -> str:
    column, yoy_column, _, _, _ = _MATRIX_MEASURES[measure]
    value = matrix[column].where(matrix[column] > 0)
    # 1.0 = the column's top destination; products differ in size by orders of
    # magnitude, so one scale for the whole grid would only say "frozen is big"
    relative = value / value.groupby(matrix["product"]).transform("max")
    data = matrix.assign(
        product_name=[display_name(columns_level, v) for v in matrix["product"]],
        value=value,
        relative=relative,
    )
    products = list(dict.fromkeys(data["product_name"]))
    header = "".join(f'<th scope="col">{escape(name)}</th>' for name in products)
    body = []
    for destination, rows in data.groupby("destination", sort=False):
        cells = []
        for _, row in rows.iterrows():
            tooltip = _cell_tooltip(row, destination_noun, yoy_column, measure)
            if pd.isna(row["relative"]):
                cells.append(f'<td class="bm-empty" title="{tooltip}">—</td>')
                continue
            ink = "#ffffff" if row["relative"] > 0.55 else CHART_INK
            top = ' class="bm-top"' if row["relative"] == 1 else ""
            cells.append(
                f'<td{top} title="{tooltip}" style="background:{_shade(row["relative"])};'
                f'color:{ink}">{escape(_cell_label(column, row["value"]))}</td>'
            )
        body.append(f'<tr><th scope="row">{escape(str(destination))}</th>{"".join(cells)}</tr>')
    legend = (
        '<p class="bm-legend">Each product column is shaded on its own:'
        '<span class="bm-swatch"></span>darker = closer to that product\'s top buyer '
        "(in <b>bold</b>). Hover or long-press a cell for details.</p>"
    )
    return (
        f'{_MATRIX_CSS}<div class="bm-wrap"><table class="bm"><thead><tr>'
        f'<th scope="col" class="bm-corner">{escape(destination_noun.capitalize())}</th>'
        f"{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>{legend}"
    )


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
    f"**{_MEASURE_NOUNS[matrix_measure]}** (follows the measure picked above)."
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
    st.html(_matrix_html(matrix, columns_level, rows_noun, matrix_measure))
