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


def monthly_comparison(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
) -> pd.DataFrame:
    """The latest 12 months, month by month, next to the same month a year
    earlier - seasonality and momentum that 12-month totals smooth away.

    Returns 12 rows, oldest first: month (first day of the month), fob_usd,
    metric_ton, destinations (countries buying that month), and the same
    three a year earlier (prev_fob_usd, prev_metric_ton, prev_destinations),
    fob_yoy_pct (NaN when that month had nothing).
    """
    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    latest_idx = latest_month_index(con)
    monthly = con.execute(
        f"""
        SELECT year * 12 + month - 1 AS idx, sum(fob_usd) AS fob_usd,
               sum(metric_ton) AS metric_ton,
               count(DISTINCT country) FILTER (WHERE fob_usd > 0) AS destinations
        FROM marts.exports
        WHERE year * 12 + month - 1 > ? - 24
            {filter_sql}
        GROUP BY idx
        """,
        [latest_idx, *params],
    ).df()
    by_idx = monthly.set_index("idx")

    def value(idx: int, column: str) -> float:
        return float(by_idx.at[idx, column]) if idx in by_idx.index else 0.0

    df = pd.DataFrame(
        {
            "month": [
                pd.Timestamp(year=idx // 12, month=idx % 12 + 1, day=1)
                for idx in range(latest_idx - 11, latest_idx + 1)
            ],
            "fob_usd": [value(i, "fob_usd") for i in range(latest_idx - 11, latest_idx + 1)],
            "metric_ton": [value(i, "metric_ton") for i in range(latest_idx - 11, latest_idx + 1)],
            "prev_fob_usd": [
                value(i - 12, "fob_usd") for i in range(latest_idx - 11, latest_idx + 1)
            ],
            "prev_metric_ton": [
                value(i - 12, "metric_ton") for i in range(latest_idx - 11, latest_idx + 1)
            ],
            "destinations": [
                value(i, "destinations") for i in range(latest_idx - 11, latest_idx + 1)
            ],
            "prev_destinations": [
                value(i - 12, "destinations") for i in range(latest_idx - 11, latest_idx + 1)
            ],
        }
    )
    df["fob_yoy_pct"] = _growth(df["fob_usd"], df["prev_fob_usd"])
    return df


def change_bridge(trend: pd.DataFrame) -> dict | None:
    """How the latest 12 months' value moved from the 12 before, split into
    a volume effect (the change in tons, at the old price) and a price
    effect (the change in $/t, on the new tons) - the two add up exactly to
    the change. `trend` is market_trend()'s output. None when the previous
    period shipped no tons (no old price to hold constant).

    Returns {previous, volume_effect, price_effect, latest, old_price,
    new_price, previous_start, previous_end, latest_start, latest_end}.
    """
    if len(trend) < 2:
        return None
    old, new = trend.iloc[-2], trend.iloc[-1]
    if not old["metric_ton"] > 0:
        return None
    old_price = old["fob_usd"] / old["metric_ton"]
    new_price = new["fob_usd"] / new["metric_ton"] if new["metric_ton"] > 0 else old_price
    volume_effect = (new["metric_ton"] - old["metric_ton"]) * old_price
    return {
        "previous": float(old["fob_usd"]),
        "volume_effect": float(volume_effect),
        "price_effect": float(new["fob_usd"] - old["fob_usd"] - volume_effect),
        "latest": float(new["fob_usd"]),
        "old_price": float(old_price),
        "new_price": float(new_price),
        "previous_start": old["period_start"],
        "previous_end": old["period_end"],
        "latest_start": new["period_start"],
        "latest_end": new["period_end"],
    }


# what a row of the movers / price maps can be: a destination level, or a
# product level (for a single-country scope, where "which country" is moot)
_GROUP_COLUMNS = {
    **{level: col for level, col in GEO_LEVELS.items() if col},
    **{level: col for level, col in PRODUCT_LEVELS.items() if col},
}


def _check_rows(rows: str) -> None:
    if rows not in _GROUP_COLUMNS:
        raise ValueError(f"rows must be one of {sorted(_GROUP_COLUMNS)}, not {rows!r}")


def _destination_periods(
    con: duckdb.DuckDBPyConnection,
    rows: str,
    product_level: str,
    product_value: str | None,
    geo_level: str,
    geo_value: str | None,
) -> pd.DataFrame:
    """Value and tons per destination (or product - see _GROUP_COLUMNS),
    latest 12 months and the 12 before."""
    _check_rows(rows)
    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    return con.execute(
        f"""
        SELECT
            {_GROUP_COLUMNS[rows]}                                       AS destination,
            coalesce(sum(fob_usd) FILTER (WHERE periods_ago = 0), 0)     AS fob_usd,
            coalesce(sum(fob_usd) FILTER (WHERE periods_ago = 1), 0)     AS prev_fob_usd,
            coalesce(sum(metric_ton) FILTER (WHERE periods_ago = 0), 0)  AS metric_ton,
            coalesce(sum(metric_ton) FILTER (WHERE periods_ago = 1), 0)  AS prev_metric_ton
        FROM ({_PERIODIZED_SQL})
        WHERE periods_ago <= 1 AND {_GROUP_COLUMNS[rows]} IS NOT NULL
            {filter_sql}
        GROUP BY destination
        """,
        [latest_month_index(con), *params],
    ).df()


def destination_movers(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
    rows: str = "country",
    top_n: int = 6,
) -> pd.DataFrame:
    """Which destinations drove the change: the top_n biggest gains and the
    top_n biggest losses in value, latest 12 months vs the 12 before.

    Returns destination, fob_usd, prev_fob_usd, change (USD), change_pct
    (NaN for a new buyer); gains first (largest first), then losses
    (largest first).
    """
    df = _destination_periods(con, rows, product_level, product_value, geo_level, geo_value)
    df["change"] = df["fob_usd"] - df["prev_fob_usd"]
    df["change_pct"] = _growth(df["fob_usd"], df["prev_fob_usd"])
    gains = df[df["change"] > 0].nlargest(top_n, "change")
    losses = df[df["change"] < 0].nsmallest(top_n, "change")
    return pd.concat([gains, losses]).reset_index(drop=True)[
        ["destination", "fob_usd", "prev_fob_usd", "change", "change_pct"]
    ]


def price_volume_map(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
    rows: str = "country",
    top_n: int = 20,
) -> pd.DataFrame:
    """Premium vs bulk buyers: the top_n destinations by value over the
    latest 12 months, with their volume and average price.

    Returns destination, fob_usd, metric_ton, unit_price_usd_per_ton,
    price_yoy_pct, price_vs_average (their $/t over the whole scope's $/t:
    1.2 = pays 20% above average), largest first.
    """
    df = _destination_periods(con, rows, product_level, product_value, geo_level, geo_value)
    total_tons = df["metric_ton"].sum()
    average = df["fob_usd"].sum() / total_tons if total_tons > 0 else float("nan")
    df = df[(df["fob_usd"] > 0) & (df["metric_ton"] > 0)].nlargest(top_n, "fob_usd")
    price = df["fob_usd"] / df["metric_ton"]
    prev_price = df["prev_fob_usd"] / df["prev_metric_ton"].where(df["prev_metric_ton"] > 0)
    return df.assign(
        unit_price_usd_per_ton=price,
        price_yoy_pct=_growth(price, prev_price),
        price_vs_average=price / average,
    ).reset_index(drop=True)[
        [
            "destination",
            "fob_usd",
            "metric_ton",
            "unit_price_usd_per_ton",
            "price_yoy_pct",
            "price_vs_average",
        ]
    ]


def concentration_trend(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str = "overall",
    product_value: str | None = None,
    geo_level: str = "overall",
    geo_value: str | None = None,
    rows: str = "country",
    window_years: int = 10,
) -> pd.DataFrame:
    """How dependent the trade is on its biggest buyers, period by period:
    the top buyer's and the top 5's share of value.

    Returns one row per 12-month period with any trade, oldest first:
    period_start, period_end, periods_ago, top_destination, top1_share,
    top5_share, destinations (buyers in that period).
    """
    _check_rows(rows)
    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    latest_idx = latest_month_index(con)
    df = con.execute(
        f"""
        WITH per_destination AS (
            SELECT periods_ago, {_GROUP_COLUMNS[rows]} AS destination, sum(fob_usd) AS fob_usd
            FROM ({_PERIODIZED_SQL})
            WHERE periods_ago < ? AND {_GROUP_COLUMNS[rows]} IS NOT NULL
                {filter_sql}
            GROUP BY ALL
            HAVING sum(fob_usd) > 0
        ),
        ranked AS (
            SELECT *,
                row_number() OVER (PARTITION BY periods_ago ORDER BY fob_usd DESC, destination)
                    AS rnk,
                sum(fob_usd) OVER (PARTITION BY periods_ago) AS period_total
            FROM per_destination
        )
        SELECT
            periods_ago,
            any_value(destination) FILTER (WHERE rnk = 1)                  AS top_destination,
            sum(fob_usd) FILTER (WHERE rnk = 1) / any_value(period_total)  AS top1_share,
            sum(fob_usd) FILTER (WHERE rnk <= 5) / any_value(period_total) AS top5_share,
            count(*)                                                       AS destinations
        FROM ranked
        GROUP BY periods_ago
        ORDER BY periods_ago DESC
        """,
        [latest_idx, window_years, *params],
    ).df()
    return add_period_bounds(df, latest_idx)[
        [
            "period_start",
            "period_end",
            "periods_ago",
            "top_destination",
            "top1_share",
            "top5_share",
            "destinations",
        ]
    ]
