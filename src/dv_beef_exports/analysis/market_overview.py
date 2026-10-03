"""
Market overview: what Brazil sells, how it's trending, and who buys what -
the descriptive half of Phase 2 (docs/ROADMAP.md: "market overview" and
"top products x top destinations").

Built on the same trailing 12-month periods as opportunity scoring (see
_PERIODIZED_SQL), so a figure on the overview page always agrees with the
same figure behind a score, and a partial calendar year never reads as a
drop. Purely descriptive: no trend fitting, no leading-period trim.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from dv_beef_exports.analysis.opportunity_scoring import (
    _PERIODIZED_SQL,
    GEO_LEVELS,
    PRODUCT_LEVELS,
    add_period_bounds,
    latest_month_index,
    level_filters,
)


def _growth(current: pd.Series, previous: pd.Series) -> pd.Series:
    """current / previous - 1, NaN where there was nothing to grow from."""
    return (current / previous.where(previous > 0)) - 1


def market_trend(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
    window_years: int = 10,
) -> pd.DataFrame:
    """Brazil's exports in scope, one row per trailing 12-month period.

    Returns columns (oldest period first, every period in the window, zeros
    where nothing was exported): period_start, period_end, periods_ago,
    fob_usd, metric_ton, unit_price_usd_per_ton (sum-then-divide; NaN with
    no tons), destinations (countries with any exports), and
    fob_yoy_pct / ton_yoy_pct / price_yoy_pct (change vs the previous 12
    months; NaN when the previous period had nothing).
    """
    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    latest_idx = latest_month_index(con)
    # one extra period beyond the window, so the oldest shown period has a YoY
    n_periods = window_years + 1
    trend_sql = f"""
        WITH periods AS (
            SELECT
                periods_ago,
                sum(fob_usd)                                         AS fob_usd,
                sum(metric_ton)                                      AS metric_ton,
                count(DISTINCT country) FILTER (WHERE fob_usd > 0)   AS destinations
            FROM ({_PERIODIZED_SQL})
            WHERE periods_ago < ?
                {filter_sql}
            GROUP BY periods_ago
        )
        SELECT
            p.periods_ago,
            coalesce(t.fob_usd, 0)      AS fob_usd,
            coalesce(t.metric_ton, 0)   AS metric_ton,
            coalesce(t.destinations, 0) AS destinations
        FROM range(0, ?) p(periods_ago)
        LEFT JOIN periods t ON t.periods_ago = p.periods_ago
        ORDER BY p.periods_ago DESC
    """
    trend = con.execute(trend_sql, [latest_idx, n_periods, *params, n_periods]).df()

    trend["unit_price_usd_per_ton"] = trend["fob_usd"] / trend["metric_ton"].where(
        trend["metric_ton"] > 0
    )
    trend["fob_yoy_pct"] = _growth(trend["fob_usd"], trend["fob_usd"].shift(1))
    trend["ton_yoy_pct"] = _growth(trend["metric_ton"], trend["metric_ton"].shift(1))
    trend["price_yoy_pct"] = _growth(
        trend["unit_price_usd_per_ton"], trend["unit_price_usd_per_ton"].shift(1)
    )
    trend = add_period_bounds(trend.iloc[1:].reset_index(drop=True), latest_idx)
    return trend[
        [
            "period_start",
            "period_end",
            "periods_ago",
            "fob_usd",
            "metric_ton",
            "unit_price_usd_per_ton",
            "destinations",
            "fob_yoy_pct",
            "ton_yoy_pct",
            "price_yoy_pct",
        ]
    ]


def top_destinations_matrix(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
    rows: str = "country",
    columns: str = "ncm_code",
    top_n: int = 10,
    rank_by: str = "fob_usd",
) -> pd.DataFrame:
    """Who buys what, over the latest 12 months: the top_n destinations
    (within the scope) x every product they bought.

    rows: "country" | "region" | "trade_bloc" - what a destination is.
        Countries outside every region/bloc are left out, not grouped.
    columns: "ncm_code" | "category" - what a product is.
    rank_by: "fob_usd" | "metric_ton" - what makes a destination "top", and
        how rows and columns are ordered (largest first).

    Returns one row per (destination, product) cell - a full grid, zeros
    included - with columns: destination, product; fob_usd and metric_ton
    for the latest 12 months and prev_fob_usd / prev_metric_ton for the 12
    before; unit_price_usd_per_ton (sum-then-divide, NaN with no tons);
    fob_yoy_pct / ton_yoy_pct / price_yoy_pct (NaN when the previous period
    had nothing); and, for both value and tons, the cell's share of its
    destination's total (share_of_destination, ton_share_of_destination)
    and of everything in scope across all destinations, not just the top_n
    (share_of_total, ton_share_of_total).
    """
    if rows not in GEO_LEVELS or GEO_LEVELS[rows] is None:
        raise ValueError(f"rows must be one of country/region/trade_bloc, not {rows!r}")
    if columns not in PRODUCT_LEVELS or PRODUCT_LEVELS[columns] is None:
        raise ValueError(f"columns must be ncm_code or category, not {columns!r}")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if rank_by not in ("fob_usd", "metric_ton"):
        raise ValueError(f"rank_by must be fob_usd or metric_ton, not {rank_by!r}")

    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    latest_idx = latest_month_index(con)
    matrix_sql = f"""
        WITH filtered AS (
            SELECT
                {GEO_LEVELS[rows]}         AS destination,
                {PRODUCT_LEVELS[columns]}  AS product,
                periods_ago,
                fob_usd,
                metric_ton
            FROM ({_PERIODIZED_SQL})
            WHERE periods_ago <= 1
                {filter_sql}
        ),
        top_destinations AS (
            SELECT
                destination,
                sum(fob_usd)    AS destination_fob_usd,
                sum(metric_ton) AS destination_metric_ton
            FROM filtered
            WHERE periods_ago = 0 AND destination IS NOT NULL
            GROUP BY destination
            HAVING sum({rank_by}) > 0
            ORDER BY sum({rank_by}) DESC, destination
            LIMIT ?
        ),
        cells AS (
            SELECT
                destination,
                product,
                coalesce(sum(fob_usd) FILTER (WHERE periods_ago = 0), 0)    AS fob_usd,
                coalesce(sum(fob_usd) FILTER (WHERE periods_ago = 1), 0)    AS prev_fob_usd,
                coalesce(sum(metric_ton) FILTER (WHERE periods_ago = 0), 0) AS metric_ton,
                coalesce(sum(metric_ton) FILTER (WHERE periods_ago = 1), 0) AS prev_metric_ton
            FROM filtered
            GROUP BY destination, product
        ),
        products AS (
            SELECT product, sum({rank_by}) AS product_total
            FROM cells
            WHERE destination IN (SELECT destination FROM top_destinations)
            GROUP BY product
            HAVING sum({rank_by}) > 0
        )
        SELECT
            d.destination,
            p.product,
            coalesce(c.fob_usd, 0)         AS fob_usd,
            coalesce(c.prev_fob_usd, 0)    AS prev_fob_usd,
            coalesce(c.metric_ton, 0)      AS metric_ton,
            coalesce(c.prev_metric_ton, 0) AS prev_metric_ton,
            d.destination_fob_usd,
            d.destination_metric_ton,
            (SELECT sum(fob_usd) FROM filtered WHERE periods_ago = 0)    AS total_fob_usd,
            (SELECT sum(metric_ton) FROM filtered WHERE periods_ago = 0) AS total_metric_ton
        FROM top_destinations d
        CROSS JOIN products p
        LEFT JOIN cells c ON c.destination = d.destination AND c.product = p.product
        ORDER BY
            d.destination_{rank_by} DESC, d.destination, p.product_total DESC, p.product
    """
    matrix = con.execute(matrix_sql, [latest_idx, *params, top_n]).df()

    price = matrix["fob_usd"] / matrix["metric_ton"].where(matrix["metric_ton"] > 0)
    prev_price = matrix["prev_fob_usd"] / matrix["prev_metric_ton"].where(
        matrix["prev_metric_ton"] > 0
    )
    destination_tons = matrix["destination_metric_ton"].where(matrix["destination_metric_ton"] > 0)
    matrix["unit_price_usd_per_ton"] = price
    matrix["fob_yoy_pct"] = _growth(matrix["fob_usd"], matrix["prev_fob_usd"])
    matrix["ton_yoy_pct"] = _growth(matrix["metric_ton"], matrix["prev_metric_ton"])
    matrix["price_yoy_pct"] = _growth(price, prev_price)
    matrix["share_of_destination"] = matrix["fob_usd"] / matrix["destination_fob_usd"]
    matrix["share_of_total"] = matrix["fob_usd"] / matrix["total_fob_usd"]
    matrix["ton_share_of_destination"] = matrix["metric_ton"] / destination_tons
    matrix["ton_share_of_total"] = matrix["metric_ton"] / matrix["total_metric_ton"]
    return matrix[
        [
            "destination",
            "product",
            "fob_usd",
            "prev_fob_usd",
            "metric_ton",
            "prev_metric_ton",
            "unit_price_usd_per_ton",
            "fob_yoy_pct",
            "ton_yoy_pct",
            "price_yoy_pct",
            "share_of_destination",
            "share_of_total",
            "ton_share_of_destination",
            "ton_share_of_total",
        ]
    ]
