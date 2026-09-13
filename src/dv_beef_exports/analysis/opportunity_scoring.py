"""
Opportunity scoring and confidence, per
docs/decisions/0005-opportunity-scoring-methodology.md (amended
2026-09-12 for adjusted R²).

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
    "opportunity_score",
    "total_fob_usd",
    "total_kg",
    "unit_price_usd_per_ton",
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
        window_years: trailing window for the trend fit and confidence.
        min_years_active: groups with fewer active years than this are
            dropped entirely (adjusted R² is undefined at 2, unstable
            below ~4 — see the ADR amendment).

    Returns:
        One row per geo_level value that cleared min_years_active,
        sorted by opportunity_score descending. Columns: the geo_level
        name itself (e.g. "country"), years_active, annual_growth_pct,
        trend_r2_adj, coverage_score, volume_confidence, confidence,
        share_pct, opportunity_score, total_fob_usd, total_kg,
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

    max_year = con.execute("SELECT max(year) FROM marts.exports").fetchone()[0]
    min_year = max_year - window_years + 1

    fixed_filter_sql = f"AND {fixed_col} = ?" if fixed_col else ""
    fixed_params = [fixed_value] if fixed_col else []

    trend_sql = f"""
        WITH windowed AS (
            SELECT
                {ranked_col} AS group_value,
                year,
                sum(fob_usd) AS fob_usd,
                sum(kg)      AS kg
            FROM marts.exports
            WHERE year BETWEEN ? AND ?
                {fixed_filter_sql}
            GROUP BY {ranked_col}, year
            HAVING sum(fob_usd) > 0
        )
        SELECT
            group_value,
            regr_slope(ln(fob_usd), year) AS log_growth_rate,
            regr_r2(ln(fob_usd), year)    AS trend_r2,
            count(*)                      AS years_active,
            sum(fob_usd)                  AS total_fob_usd,
            sum(kg)                       AS total_kg
        FROM windowed
        GROUP BY group_value
        HAVING count(*) >= ?
    """
    trend = con.execute(trend_sql, [min_year, max_year, *fixed_params, min_years_active]).df()

    if trend.empty:
        return pd.DataFrame(columns=[ranked_col, *_OUTPUT_COLUMNS])

    recent_sql = f"""
        SELECT {ranked_col} AS group_value, sum(fob_usd) AS fob_usd
        FROM marts.exports
        WHERE year = ?
            {fixed_filter_sql}
        GROUP BY {ranked_col}
    """
    recent = con.execute(recent_sql, [max_year, *fixed_params]).df()
    total_recent_fob = recent["fob_usd"].sum()

    # K (volume-confidence shrinkage constant) is the median group total
    # across the FULL (product_col, geo_col) grid, unfiltered by whatever
    # is fixed here - ADR 0005 defines it per (product_level, geo_level)
    # pair, not scoped to one product/country.
    k_group_cols = [c for c in (fixed_col, ranked_col) if c is not None]
    k_cols_sql = ", ".join(k_group_cols)
    k_sql = f"""
        WITH group_totals AS (
            SELECT {k_cols_sql}, sum(fob_usd) AS total_fob_usd
            FROM marts.exports
            WHERE year BETWEEN ? AND ?
            GROUP BY {k_cols_sql}
            HAVING sum(fob_usd) > 0
        )
        SELECT median(total_fob_usd) FROM group_totals
    """
    k = con.execute(k_sql, [min_year, max_year]).fetchone()[0]

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
    result["opportunity_score"] = result["annual_growth_pct"] * (1 - result["share_pct"])

    # sum-then-divide at this query's own aggregation grain, over the same
    # trailing window as everything else - never an average of row-level
    # or sub-group ratios (Felipe's flagged Tableau-style gotcha).
    result["unit_price_usd_per_ton"] = result["total_fob_usd"] / (result["total_kg"] / 1000.0)

    result = result.rename(columns={"group_value": ranked_col})
    return (
        result[[ranked_col, *_OUTPUT_COLUMNS]]
        .sort_values("opportunity_score", ascending=False)
        .reset_index(drop=True)
    )
