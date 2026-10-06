"""
Opportunity index and confidence, per
docs/decisions/0006-opportunity-index.md (which replaces 0005's
growth x (1 - share) score) and 0005 for confidence.

Both "lenses" the product needs — product fixed -> rank markets, and
country fixed -> rank products — are the same underlying query, pivoted
on which axis is fixed and which is grouped/ranked. rank_markets() and
rank_products() are thin, differently-named entry points over one shared
_score_opportunities() core, so there's exactly one query shape to trust,
not two hand-kept-in-sync copies.
"""

from __future__ import annotations

import math

import duckdb
import numpy as np
import pandas as pd

# level name -> real marts.exports column, or None for "overall" (collapse
# to a single group / don't filter – e.g. "beef overall", "Asia overall")
PRODUCT_LEVELS: dict[str, str | None] = {
    "ncm_code": "ncm_code",
    "category": "category",
    "overall": None,
}
GEO_LEVELS: dict[str, str | None] = {
    "country": "country",
    "region": "region",
    "trade_bloc": "trade_bloc",
    "overall": None,
}
_OUTPUT_COLUMNS = [
    "years_active",
    "annual_growth_pct",
    "trend_r2_adj",
    "coverage_score",
    "volume_confidence",
    "confidence",
    "share_pct",
    "recent_growth_pct",
    "recent_consistency",
    "price_trend_pct",
    "tons_per_year",
    "materiality",
    "attractiveness",
    "opportunity_score",
    "total_fob_usd",
    "total_metric_ton",
    "unit_price_usd_per_ton",
]

# The opportunity index (ADR 0006):
#   100 x attractiveness x headroom x materiality x evidence
# attractiveness = these weights over percentile ranks within the ranked set
INDEX_WEIGHTS = {"momentum": 0.30, "recent": 0.30, "size": 0.20, "price": 0.20}
# within "recent": recent growth vs how consistently it rose
RECENT_GROWTH_WEIGHT, RECENT_CONSISTENCY_WEIGHT = 0.6, 0.4
# the last N 12-month periods count as "recent"
RECENT_PERIODS = 4
# ~ one 40-ft reefer container of frozen beef: a market buying one container
# a year scores 0.5 on materiality, ten containers ~0.9
CONTAINER_TONS = 25.0

# One-line meaning of every output column, shown as the app's column-header
# tooltips. Must match the "Quick reference" table in
# docs/analysis-methodology.md word for word - a test enforces it, so the
# dashboard and the methodology doc can't drift apart.
METRIC_GLOSSARY: dict[str, str] = {
    "years_active": "12-month periods in the window with exports, after the leading trim",
    "annual_growth_pct": "compound yearly growth from the fitted trend",
    "trend_r2_adj": "how well one steady trend explains the history",
    "coverage_score": "how complete the history is within the window",
    "volume_confidence": "how large this group is vs. a typical one",
    "confidence": "all three evidence legs combined (geometric mean)",
    "share_pct": "slice of the fixed axis already held, latest 12 months",
    "recent_growth_pct": "yearly growth over the last 4 periods",
    "recent_consistency": "share of the last 3 year-on-year changes that were up",
    "price_trend_pct": "yearly change in the average price per ton",
    "tons_per_year": "average tons a year, in years with sales",
    "materiality": "commercial size: tons a year vs. one 25-ton container",
    "attractiveness": "growth, recent momentum, size and price trend, ranked",
    "opportunity_score": "0-100 index: attractiveness x headroom x materiality x evidence",
    "total_fob_usd": "total export value in the window",
    "total_metric_ton": "total export weight in the window",
    "unit_price_usd_per_ton": "realised average price, sum-then-divide",
}

# marts.exports, with each row tagged by how many trailing 12-month periods
# ago it falls: 0 = the 12 months ending at the latest month in the data,
# 1 = the 12 months before that, etc. Periods instead of calendar years so
# a partial current year is never scored as if it were a full one. Takes
# one parameter: the latest month index (year * 12 + month - 1).
_PERIODIZED_SQL = """
    SELECT *, (? - (year * 12 + month - 1)) // 12 AS periods_ago
    FROM marts.exports
"""

# Leading periods worth less than this fraction of the group's median active
# period are treated as noise and trimmed before the trend fit. In log space
# a near-zero first value dominates the slope (e.g. $42 -> $13,080 is a bigger
# step than $1M -> $100M). Leading only, so a recent collapse is never hidden;
# 1% keeps genuinely small early ramps. See the ADR 0005 amendment (2026-10-03).
_LEADING_TRIM_FRACTION = 0.01


def latest_period_end(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """(year, month) of the latest month in marts.exports - where the most
    recent trailing 12-month period, and so every score, ends.
    """
    return con.execute(
        "SELECT year, month FROM marts.exports ORDER BY year DESC, month DESC LIMIT 1"
    ).fetchone()


def latest_month_index(con: duckdb.DuckDBPyConnection) -> int:
    """year * 12 + month - 1 of the latest month in marts.exports - the
    parameter _PERIODIZED_SQL takes."""
    return con.execute("SELECT max(year * 12 + month - 1) FROM marts.exports").fetchone()[0]


def add_period_bounds(df: pd.DataFrame, latest_idx: int) -> pd.DataFrame:
    """Add period_start / period_end (first day of each 12-month period's
    first and last month) from a periods_ago column."""
    end_idx = latest_idx - 12 * df["periods_ago"]
    period_end = pd.to_datetime({"year": end_idx // 12, "month": end_idx % 12 + 1, "day": 1})
    return df.assign(period_end=period_end, period_start=period_end - pd.DateOffset(months=11))


def level_filters(
    product_level: str, product_value: str | None, geo_level: str, geo_value: str | None
) -> tuple[str, list]:
    """SQL `AND col = ?` filters (and their parameters) narrowing
    marts.exports to one product scope and one market scope. "overall"
    means no filter on that axis, and takes no value."""
    levels = ((PRODUCT_LEVELS, product_level, product_value), (GEO_LEVELS, geo_level, geo_value))
    filters, params = [], []
    for level_map, level, value in levels:
        if level not in level_map:
            raise ValueError(f"Unknown level {level!r}")
        col = level_map[level]
        if (col is None) != (value is None):
            raise ValueError(f"a value is required for {level!r}, and only for it")
        if col is not None:
            filters.append(f"AND {col} = ?")
            params.append(value)
    return " ".join(filters), params


def _trimmed_periods_ctes(group_expr: str, filter_sql: str) -> str:
    """The `windowed` and `flagged` CTEs shared by the scoring query and
    period_history(): one row per (group, 12-month period) in the window
    with positive FOB, plus `substantial_so_far` - a running count of
    periods worth >= the trim fraction of the group's median, oldest first.
    Rows still at 0 are the leading tiny periods the fit trims.

    Parameters, in order: latest month index, window_years, the filter's
    own parameters, then the trim fraction.
    """
    return f"""
        windowed AS (
            SELECT
                {group_expr}    AS group_value,
                periods_ago,
                sum(fob_usd)    AS fob_usd,
                sum(metric_ton) AS metric_ton
            FROM ({_PERIODIZED_SQL})
            WHERE periods_ago < ?
                {filter_sql}
            GROUP BY {group_expr}, periods_ago
            HAVING sum(fob_usd) > 0
        ),
        flagged AS (
            -- running count of substantial periods, oldest first: leading
            -- tiny periods are the ones still at 0
            SELECT
                *,
                count(*) FILTER (WHERE fob_usd >= ? * median_fob) OVER (
                    PARTITION BY group_value
                    ORDER BY periods_ago DESC
                    ROWS UNBOUNDED PRECEDING
                ) AS substantial_so_far
            FROM (
                SELECT *, median(fob_usd) OVER (PARTITION BY group_value) AS median_fob
                FROM windowed
            )
        )
    """


def period_history(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str,
    product_value: str | None,
    geo_level: str,
    geo_value: str | None,
    window_years: int = 10,
) -> pd.DataFrame:
    """One group's period-by-period history over the window - every
    period, including ones with no exports - alongside the fitted trend the
    score is built from. For explaining a score, not for scoring.

    Returns columns: period_start, period_end (first day of each 12-month
    period's first/last month), periods_ago, fob_usd, metric_ton (0 when
    nothing was exported), in_fit (False for empty and leading-trimmed
    periods), trend_fob_usd (the fitted exponential trend, NaN when fewer
    than two periods are in the fit). Oldest period first.
    """
    filter_sql, params = level_filters(product_level, product_value, geo_level, geo_value)
    latest_idx = latest_month_index(con)
    history_sql = f"""
        WITH {_trimmed_periods_ctes("'group'", filter_sql)},
        fit AS (
            SELECT
                regr_slope(ln(fob_usd), -periods_ago)     AS slope,
                regr_intercept(ln(fob_usd), -periods_ago) AS intercept
            FROM flagged
            WHERE substantial_so_far > 0
        )
        SELECT
            p.periods_ago,
            coalesce(f.fob_usd, 0)                    AS fob_usd,
            coalesce(f.metric_ton, 0)                 AS metric_ton,
            coalesce(f.substantial_so_far > 0, false) AS in_fit,
            exp(fit.intercept + fit.slope * -p.periods_ago) AS trend_fob_usd
        FROM range(0, ?) p(periods_ago)
        LEFT JOIN flagged f ON f.periods_ago = p.periods_ago
        CROSS JOIN fit
        ORDER BY p.periods_ago DESC
    """
    history = con.execute(
        history_sql,
        [latest_idx, window_years, *params, _LEADING_TRIM_FRACTION, window_years],
    ).df()

    history = add_period_bounds(history, latest_idx)
    return history[
        [
            "period_start",
            "period_end",
            "periods_ago",
            "fob_usd",
            "metric_ton",
            "in_fit",
            "trend_fob_usd",
        ]
    ]


def rank_markets(
    con: duckdb.DuckDBPyConnection,
    *,
    product_level: str,
    product_value: str | None,
    geo_level: str,
    window_years: int = 10,
    min_years_active: int = 4,
) -> pd.DataFrame:
    """Lens 1: "for this product, which markets look promising?"

    Fixes a product (or all products combined, if product_level is
    "overall") and ranks geo_level values (country/region/trade_bloc)
    against it.

    Args:
        con: open DuckDB connection (marts.exports must already be built).
        product_level: "ncm_code" | "category" | "overall".
        product_value: the fixed product, e.g. "02062100" or a category
            name. Must be None iff product_level == "overall".
        geo_level: "country" | "region" | "trade_bloc" — the axis being
            ranked. Can't be "overall" (nothing to rank).
        window_years: trailing window for the trend fit and confidence, in
            12-month periods ending at the latest month in the data (so a
            partial current year is never scored as a full one).
        min_years_active: groups with fewer active years than this are
            dropped entirely (adjusted R² is undefined at 2, unstable
            below ~4 — see the ADR amendment).

    Returns:
        One row per geo_level value that cleared min_years_active,
        sorted by opportunity_score descending. Columns: the geo_level
        name itself (e.g. "country"), years_active, annual_growth_pct,
        trend_r2_adj, coverage_score, volume_confidence, confidence,
        share_pct, opportunity_score, total_fob_usd, total_metric_ton,
        unit_price_usd_per_ton.
    """
    return _score_opportunities(
        con,
        fixed_level=product_level,
        fixed_value=product_value,
        fixed_levels=PRODUCT_LEVELS,
        ranked_level=geo_level,
        ranked_levels=GEO_LEVELS,
        window_years=window_years,
        min_years_active=min_years_active,
    )


def rank_products(
    con: duckdb.DuckDBPyConnection,
    *,
    geo_level: str,
    geo_value: str | None,
    product_level: str,
    window_years: int = 10,
    min_years_active: int = 4,
) -> pd.DataFrame:
    """Lens 2: "for this country, which products look promising?"

    Fixes a geography (or all geographies combined, if geo_level is
    "overall") and ranks product_level values (ncm_code/category)
    against it. See rank_markets() for the shared argument meanings.
    """
    return _score_opportunities(
        con,
        fixed_level=geo_level,
        fixed_value=geo_value,
        fixed_levels=GEO_LEVELS,
        ranked_level=product_level,
        ranked_levels=PRODUCT_LEVELS,
        window_years=window_years,
        min_years_active=min_years_active,
    )


def _score_opportunities(
    con: duckdb.DuckDBPyConnection,
    *,
    fixed_level: str,
    fixed_value: str | None,
    fixed_levels: dict[str, str | None],
    ranked_level: str,
    ranked_levels: dict[str, str | None],
    window_years: int,
    min_years_active: int,
) -> pd.DataFrame:
    if fixed_level not in fixed_levels:
        raise ValueError(f"Unknown level {fixed_level!r}")
    if ranked_level not in ranked_levels:
        raise ValueError(f"Unknown level {ranked_level!r}")
    if min_years_active < 3:
        # adjusted R2's (years_active - 2) denominator is 0 at n=2 -
        # undefined, not just unstable. See the ADR amendment for why
        # the recommended floor is 4, not 3.
        raise ValueError("min_years_active must be >= 3")

    fixed_col = fixed_levels[fixed_level]
    ranked_col = ranked_levels[ranked_level]

    if ranked_col is None:
        raise ValueError(f"{ranked_level!r} can't be the ranked axis - nothing to group by")
    if fixed_col is None and fixed_value is not None:
        raise ValueError(f"fixed_value must be None when {fixed_level!r} is 'overall'")
    if fixed_col is not None and fixed_value is None:
        raise ValueError(f"a fixed_value is required for {fixed_level!r}")

    latest_idx = latest_month_index(con)

    fixed_filter_sql = f"AND {fixed_col} = ?" if fixed_col else ""
    fixed_params = [fixed_value] if fixed_col else []

    trend_sql = f"""
        WITH {_trimmed_periods_ctes(ranked_col, fixed_filter_sql)}
        SELECT
            group_value,
            -- x = -periods_ago so time runs forward: the slope is log
            -- growth per 12-month period, oldest to newest
            regr_slope(ln(fob_usd), -periods_ago)   AS log_growth_rate,
            regr_r2(ln(fob_usd), -periods_ago)      AS trend_r2,
            count(*)                                AS years_active,
            sum(fob_usd)                            AS total_fob_usd,
            sum(metric_ton)                         AS total_metric_ton
        FROM flagged
        WHERE substantial_so_far > 0
        GROUP BY group_value
        HAVING count(*) >= ?
    """
    trend = con.execute(
        trend_sql,
        [latest_idx, window_years, *fixed_params, _LEADING_TRIM_FRACTION, min_years_active],
    ).df()

    if trend.empty:
        return pd.DataFrame(columns=[ranked_col, *_OUTPUT_COLUMNS])

    # every group's per-period series (positive periods only), for the
    # recent-momentum and price-trend components - from the same window +
    # leading-trim CTEs as the trend fit, so the two can't disagree
    series_sql = f"""
        WITH {_trimmed_periods_ctes(ranked_col, fixed_filter_sql)}
        SELECT group_value, periods_ago, fob_usd, metric_ton, substantial_so_far > 0 AS in_fit
        FROM flagged
    """
    series = con.execute(
        series_sql, [latest_idx, window_years, *fixed_params, _LEADING_TRIM_FRACTION]
    ).df()

    recent_sql = f"""
        SELECT {ranked_col} AS group_value, sum(fob_usd) AS fob_usd
        FROM ({_PERIODIZED_SQL})
        WHERE periods_ago = 0
            {fixed_filter_sql}
        GROUP BY {ranked_col}
    """
    recent = con.execute(recent_sql, [latest_idx, *fixed_params]).df()
    total_recent_fob = recent["fob_usd"].sum()

    # K (volume-confidence shrinkage constant) is the median group total
    # WITHIN THIS SELECTION - every group of the ranked axis that sold
    # anything in the window under the fixed filter, before the
    # min_years_active cut. "Big" means big among the markets (or products)
    # being compared, so nothing outside the selection moves its scores
    # (ADR 0006 amendment; ADR 0005 originally used the whole grid).
    k_sql = f"""
        WITH group_totals AS (
            SELECT {ranked_col} AS group_value, sum(fob_usd) AS total_fob_usd
            FROM ({_PERIODIZED_SQL})
            WHERE periods_ago < ?
                {fixed_filter_sql}
            GROUP BY {ranked_col}
            HAVING sum(fob_usd) > 0
        )
        SELECT median(total_fob_usd) FROM group_totals
    """
    k = con.execute(k_sql, [latest_idx, window_years, *fixed_params]).fetchone()[0]

    result = trend.merge(recent, on="group_value", how="left").rename(
        columns={"fob_usd": "recent_year_fob_usd"}
    )
    result["recent_year_fob_usd"] = result["recent_year_fob_usd"].fillna(0.0)

    result["annual_growth_pct"] = result["log_growth_rate"].apply(math.exp) - 1

    n = result["years_active"]
    r2 = result["trend_r2"].fillna(0.0)
    result["trend_r2_adj"] = (1 - (1 - r2) * (n - 1) / (n - 2)).clip(lower=0, upper=1)

    result["coverage_score"] = (n / window_years).clip(upper=1.0)
    result["volume_confidence"] = result["total_fob_usd"] / (result["total_fob_usd"] + k)
    result["confidence"] = (
        result["coverage_score"] * result["trend_r2_adj"] * result["volume_confidence"]
    ) ** (1 / 3)

    result["share_pct"] = (
        result["recent_year_fob_usd"] / total_recent_fob if total_recent_fob else 0.0
    )
    result = result.merge(_period_components(series), on="group_value", how="left")
    result["tons_per_year"] = result["total_metric_ton"] / result["years_active"]
    result["materiality"] = result["tons_per_year"] / (result["tons_per_year"] + CONTAINER_TONS)
    result["attractiveness"] = _attractiveness(result)
    result["opportunity_score"] = (
        100
        * result["attractiveness"]
        * (1 - result["share_pct"])  # headroom: the part not already Brazil's
        * result["materiality"]
        * (0.5 + 0.5 * result["confidence"])  # evidence: thin evidence halves it
    )

    # sum-then-divide at this query's own aggregation grain, over the same
    # trailing window as everything else - never an average of row-level
    # or sub-group ratios (Felipe's flagged Tableau-style gotcha).
    result["unit_price_usd_per_ton"] = result["total_fob_usd"] / result["total_metric_ton"]

    result = result.rename(columns={"group_value": ranked_col})
    return (
        result[[ranked_col, *_OUTPUT_COLUMNS]]
        .sort_values("opportunity_score", ascending=False)
        .reset_index(drop=True)
    )


def _log_linear_growth(periods_ago: pd.Series, values: pd.Series) -> float:
    """exp(slope) - 1 of ln(values) over time (-periods_ago), the same
    log-linear form as annual_growth_pct; NaN with fewer than 3 points."""
    if len(values) < 3:
        return np.nan
    slope = np.polyfit(-periods_ago.to_numpy(float), np.log(values.to_numpy(float)), 1)[0]
    return math.exp(slope) - 1


def _period_components(series: pd.DataFrame) -> pd.DataFrame:
    """Per group: recent_growth_pct, recent_consistency, price_trend_pct."""
    rows = []
    recent_range = range(RECENT_PERIODS - 1, -1, -1)  # oldest -> newest
    for group, periods in series.groupby("group_value", sort=False):
        recent = periods[periods["periods_ago"] < RECENT_PERIODS]
        # zero-filled: a period with no sales is a real "down", not missing
        recent_fob = recent.set_index("periods_ago")["fob_usd"].reindex(recent_range, fill_value=0)
        changes = [
            recent_fob[p] > recent_fob[p + 1]
            for p in range(RECENT_PERIODS - 2, -1, -1)
            if recent_fob[p + 1] > 0
        ]
        priced = periods[periods["in_fit"] & (periods["metric_ton"] > 0)]
        rows.append(
            {
                "group_value": group,
                "recent_growth_pct": _log_linear_growth(recent["periods_ago"], recent["fob_usd"]),
                "recent_consistency": float(np.mean(changes)) if changes else np.nan,
                "price_trend_pct": _log_linear_growth(
                    priced["periods_ago"], priced["fob_usd"] / priced["metric_ton"]
                ),
            }
        )
    return pd.DataFrame(
        rows, columns=["group_value", "recent_growth_pct", "recent_consistency", "price_trend_pct"]
    )


def _percentile(values: pd.Series) -> pd.Series:
    """Percentile rank within the ranked set, 0-1; missing values sit at the
    neutral middle rather than being rewarded or punished."""
    return values.rank(pct=True).fillna(0.5)


def _attractiveness(result: pd.DataFrame) -> pd.Series:
    """Weighted percentile ranks - scale-free, so products of very different
    sizes stay comparable, and no single extreme value dominates."""
    parts = {
        "momentum": _percentile(result["annual_growth_pct"]),
        "recent": RECENT_GROWTH_WEIGHT * _percentile(result["recent_growth_pct"])
        + RECENT_CONSISTENCY_WEIGHT * _percentile(result["recent_consistency"]),
        "size": _percentile(np.log10(result["total_fob_usd"] / result["years_active"])),
        "price": _percentile(result["price_trend_pct"]),
    }
    return sum(INDEX_WEIGHTS[name] * part for name, part in parts.items()) / sum(
        INDEX_WEIGHTS.values()
    )
