"""
Market overview page: what Brazil sells, how it's trending, and who buys
what - analysis/market_overview.py, in the same trailing 12-month periods as
the opportunity scores. Has its own product/market pickers; the sidebar
query (a ranking query) doesn't apply here.
"""

from __future__ import annotations

from datetime import date
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from dv_beef_exports.analysis.market_overview import (
    change_bridge,
    concentration_trend,
    destination_movers,
    market_trend,
    monthly_comparison,
    price_volume_map,
    top_destinations_matrix,
)
from dv_beef_exports.analysis.opportunity_scoring import latest_period_end
from dv_beef_exports.app import overview_charts as charts
from dv_beef_exports.app.common import (
    BLUE,
    CHART_INK,
    GREY,
    INK,
    MONEY_AXIS_LABELS,
    TONS_AXIS_LABELS,
    card,
    card_header,
    connection,
    display_name,
    geo_label,
    headline,
    market_scope_options,
    note,
    page_header,
    period_label,
    product_phrase,
    product_scope_options,
    show_chart,
    time_axis_right,
)
from dv_beef_exports.app.design import comparison_text, kpi_grid_html
from dv_beef_exports.app.explanations import (
    bridge_note,
    concentration_note,
    dependency_note,
    money,
    monthly_note,
    movers_note,
    overview_summary,
    price_note,
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
_TREND_TITLES = {
    "Value (USD)": "Value per 12-month period (US$)",
    "Volume (tons)": "Volume per 12-month period (t)",
    "Average price ($/t)": "Average price per 12-month period (US$/t)",
    "Destinations": "Countries buying, per 12-month period",
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


def _kpis(trend: pd.DataFrame, monthly: pd.DataFrame) -> None:
    """Four KPI cards (design system): the number in ink, a short comparison
    line with the exact periods in its tooltip, and a sparkline of the last
    12 months over the same months a year earlier."""
    latest, previous = trend.iloc[-1], trend.iloc[-2]
    compared = (
        f"{period_label(latest['period_start'], latest['period_end'])} vs "
        f"{period_label(previous['period_start'], previous['period_end'])}"
    )
    basis = "a year earlier"
    price = monthly["fob_usd"] / monthly["metric_ton"].where(monthly["metric_ton"] > 0)
    prev_price = monthly["prev_fob_usd"] / monthly["prev_metric_ton"].where(
        monthly["prev_metric_ton"] > 0
    )
    destinations_change = latest["destinations"] - previous["destinations"]
    cards = [
        {
            "title": "Value, last 12 months",
            "value": money(latest["fob_usd"]),
            "compare": comparison_text(latest["fob_yoy_pct"], basis),
            "compare_title": compared,
            "spark": (
                monthly["fob_usd"].tolist(),
                monthly["prev_fob_usd"].tolist(),
                "Value per month, last 12 months over the same months a year earlier",
            ),
            "detail": "FOB, all destinations in scope",
            "help": "Total FOB value of Brazil's exports in scope, latest 12-month period.",
        },
        {
            "title": "Volume, last 12 months",
            "value": tons(latest["metric_ton"]),
            "compare": comparison_text(latest["ton_yoy_pct"], basis),
            "compare_title": compared,
            "spark": (
                monthly["metric_ton"].tolist(),
                monthly["prev_metric_ton"].tolist(),
                "Tons per month, last 12 months over the same months a year earlier",
            ),
            "detail": "Net weight shipped",
            "help": "Total weight shipped, latest 12-month period.",
        },
        {
            "title": "Average price",
            "value": "—"
            if pd.isna(latest["unit_price_usd_per_ton"])
            else f"${latest['unit_price_usd_per_ton']:,.0f}/t",
            "compare": comparison_text(latest["price_yoy_pct"], basis),
            "compare_title": compared,
            "spark": (
                price.tolist(),
                prev_price.tolist(),
                "Average price per month, last 12 months over the same months a year earlier",
            ),
            "detail": "Value ÷ weight, across every cut and buyer",
            "help": "Total value divided by total weight - a change can mean a different mix, "
            "not just new prices.",
        },
        {
            "title": "Destinations",
            "value": f"{int(latest['destinations'])}",
            "compare": comparison_text(destinations_change, basis, unit="count"),
            "compare_title": compared,
            "spark": (
                monthly["destinations"].tolist(),
                monthly["prev_destinations"].tolist(),
                "Countries buying per month, last 12 months over the same months a year earlier",
            ),
            "detail": "Countries that bought in the 12 months",
            "help": "Countries that bought anything in scope in the latest 12-month period.",
        },
    ]
    st.html(kpi_grid_html(cards))


def _value_label(column: str, value: float) -> str:
    if column == "destinations":
        return f"{value:,.0f} countries"
    return _cell_label(column, value)


def _trend_chart(trend: pd.DataFrame, measure: str) -> alt.LayerChart:
    """One bar per 12-month period, from zero. Emphasis is scarce: the latest
    period in blue and labelled, the rest in grey; the value axis on the
    right (time series), the unit in the title."""
    column, _, yoy_column, fmt = _MEASURES[measure]
    values = trend[column].where(trend[column] > 0)
    is_latest = [i == len(trend) - 1 for i in range(len(trend))]
    label = [
        "" if not latest else _value_label(column, v)
        for latest, v in zip(is_latest, values, strict=True)
    ]
    data = trend.assign(
        period=_period_label(trend),
        role=["Latest 12 months" if x else "Earlier periods" for x in is_latest],
        label=label,
    )
    top = values.max() if values.notna().any() else 1
    tooltip = [
        alt.Tooltip("period:N", title="Period"),
        alt.Tooltip(f"{column}:Q", title=measure, format=fmt),
    ]
    if yoy_column:
        tooltip.append(alt.Tooltip(f"{yoy_column}:Q", title="vs a year earlier", format="+.1%"))
    axis_kwargs = {"labelExpr": _AXIS_LABELS[column]} if column in _AXIS_LABELS else {}
    base = alt.Chart(data).encode(
        x=alt.X("period:N", sort=None, title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        # headroom above the tallest bar for its label
        y=alt.Y(
            f"{column}:Q",
            scale=alt.Scale(domain=[0, top * 1.15]),
            axis=time_axis_right(**axis_kwargs),
        ),
        tooltip=tooltip,
    )
    bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        color=alt.Color(
            "role:N",
            scale=alt.Scale(domain=["Latest 12 months", "Earlier periods"], range=[BLUE, GREY]),
            legend=None,  # the latest bar is labelled; the title says what it is
        ),
    )
    labels = (
        base.mark_text(dy=-10, fontWeight=600, color=INK)
        .encode(text="label:N")
        .transform_filter(alt.datum.label != "")
    )
    return (bars + labels).properties(
        title=alt.TitleParams(
            text=_TREND_TITLES[measure],
            subtitle="Each bar is a full 12-month period; the latest in blue",
        ),
        height=360,
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
  border: 1px solid rgba(13,79,74,.12); border-radius: 8px; background: #faf6ef; }
.bm { border-collapse: separate; border-spacing: 0; width: 100%;
  font: 400 14px/20px 'Inter Tight', -apple-system, 'Segoe UI', sans-serif;
  font-variant-numeric: tabular-nums; }
.bm th, .bm td { height: 40px; padding: 8px 12px; white-space: nowrap; text-align: right;
  border-bottom: 1px solid rgba(13,79,74,.12); }
.bm thead th { color: #1a1a1a; font-weight: 600; white-space: normal;
  min-width: 96px; max-width: 132px; vertical-align: bottom; background: #ebe3d4; }
.bm th[scope="row"], .bm th.bm-corner { position: sticky; left: 0; z-index: 1;
  background: #faf6ef; text-align: left; color: #1a1a1a; font-weight: 600;
  border-right: 1px solid rgba(13,79,74,.12); }
.bm th.bm-corner { background: #ebe3d4; }
.bm tbody tr:hover th[scope="row"] { background: #ebe3d4; }
.bm td.bm-empty { color: #6b6b6b; }
.bm td.bm-top { font-weight: 700; }
.bm-legend { font: 400 14px/20px 'Inter Tight', sans-serif; color: #6b6b6b; margin: 8px 0 0; }
.bm-swatch { display: inline-block; width: 72px; height: 8px; border-radius: 4px;
  vertical-align: middle; margin: 0 8px;
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


con = connection()
end_year, end_month = latest_period_end(con)
page_header(
    f"Market overview · data to {date(end_year, end_month, 1):%b %Y}",
    "Market overview",
    "What Brazil sells, where it goes, what drove the latest change, and who pays the most - "
    "every figure a full 12-month period, so a half-finished year never looks like a drop.",
)

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
scope_args = {
    "product_level": product_level,
    "product_value": product_value,
    "geo_level": geo_level,
    "geo_value": geo_value,
}
trend = market_trend(con, **scope_args)
monthly = monthly_comparison(con, **scope_args)
scope = (
    f"Brazil's exports of {product_phrase(product_level, product_value)} "
    f"to {geo_label(geo_level, geo_value)}"
)
# inside one country, "which country" is moot - break it down by product instead
single_country = geo_level == "country"
by_level = "ncm_code" if single_country else "country"
by_noun = "product" if single_country else "destination"

st.markdown(_md(overview_summary(trend, scope)))
_kpis(trend, monthly)

latest = trend.iloc[-1]
with card("trend"):
    card_header(
        "How it's trending",
        f"{money(latest['fob_usd'])} in the latest 12 months "
        f"({comparison_text(latest['fob_yoy_pct'], 'a year earlier')[2:]}) · "
        f"{len(trend)} periods shown",
    )
    measure = st.segmented_control(
        "Show",
        list(_MEASURES),
        default="Value (USD)",
        key="overview_measure",
        persist_state="session",
    )
    show_chart(_trend_chart(trend, measure or "Value (USD)"))
    if monthly["fob_usd"].sum() > 0 or monthly["prev_fob_usd"].sum() > 0:
        headline(monthly_note(monthly))
        show_chart(charts.monthly_chart(monthly))

bridge = change_bridge(trend)
movers = destination_movers(con, rows=by_level, top_n=6, **scope_args)
movers = movers.assign(destination=[display_name(by_level, d) for d in movers["destination"]])
with card("change"):
    card_header(
        "What changed in the latest 12 months",
        "Value against the 12 months before: what moved it, and who",
    )
    left, right = st.columns(2, gap="large")
    with left:
        if bridge is None:
            note("Nothing shipped in the previous 12 months, so there's no change to split.")
        else:
            headline(bridge_note(bridge))
            show_chart(charts.bridge_chart(bridge))
    with right:
        if movers.empty:
            note("No change to show.")
        else:
            headline(movers_note(movers, by_noun))
            show_chart(charts.movers_chart(movers, by_noun))

pv = price_volume_map(con, rows=by_level, top_n=20, **scope_args)
pv = pv.assign(destination=[display_name(by_level, d) for d in pv["destination"]])
concentration = concentration_trend(con, rows=by_level, **scope_args)
with card("buyers"):
    card_header(
        "Buyers: who pays more, and how dependent we are",
        "Latest 12 months; prices are value ÷ weight, so a different mix moves them too",
    )
    left, right = st.columns(2, gap="large")
    with left:
        latest_price = latest["unit_price_usd_per_ton"]
        if len(pv) < 2 or pd.isna(latest_price):
            note("Too few buyers in the latest 12 months to compare prices.")
        else:
            headline(price_note(pv, by_noun))
            show_chart(charts.price_volume_chart(pv, latest_price))
            if single_country:
                note(
                    "Within one country this compares products - cuts differ in price by "
                    "nature, so read it as the product mix, not as buyers paying more."
                )
    with right:
        if concentration.empty:
            note("No trade in the window.")
        else:
            headline(dependency_note(concentration, by_noun))
            show_chart(charts.concentration_chart(concentration))

with card("matrix"):
    matrix_measure = measure if measure in _MATRIX_MEASURES else "Value (USD)"
    ranked_by = "volume" if matrix_measure == "Volume (tons)" else "value"
    card_header(
        "Who buys what",
        f"The biggest destinations by {ranked_by}, latest 12 months, showing "
        f"{_MEASURE_NOUNS[matrix_measure]} (follows the measure picked above)",
    )
    if measure == "Destinations":
        note("Each row already is one destination, so a count doesn't apply - showing value.")
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
        **scope_args,
        rows=rows_level,
        columns=columns_level,
        top_n=top_n,
        rank_by=_MATRIX_MEASURES[matrix_measure][2],
    )
    if matrix.empty:
        note("No destination bought anything in scope in the latest 12 months.")
    else:
        _, _, _, share_column, basis = _MATRIX_MEASURES[matrix_measure]
        dominance = concentration_note(matrix, rows_noun, share_column=share_column, basis=basis)
        if dominance:
            headline(dominance)
        st.html(_matrix_html(matrix, columns_level, rows_noun, matrix_measure))
