"""Tests for opportunity scoring and confidence (docs/decisions/0005)."""

from __future__ import annotations

import math
from typing import Any

import duckdb
import pytest

from dv_beef_exports.analysis.opportunity_scoring import (
    GEO_LEVELS,
    PRODUCT_LEVELS,
    rank_markets,
    rank_products,
)

PRODUCT_A = "10000001"
PRODUCT_B = "10000002"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA IF NOT EXISTS marts")
    connection.execute("""
        CREATE TABLE marts.exports (
            ncm_code   VARCHAR,
            category   VARCHAR,
            country    VARCHAR,
            region     VARCHAR,
            trade_bloc VARCHAR,
            year       INTEGER,
            fob_usd    DOUBLE,
            kg         DOUBLE
        )
    """)
    yield connection
    connection.close()


def _insert(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> None:
    con.executemany(
        """
        INSERT INTO marts.exports
            (ncm_code, category, country, region, trade_bloc, year, fob_usd, kg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["ncm_code"],
                r.get("category"),
                r["country"],
                r.get("region"),
                r.get("trade_bloc"),
                r["year"],
                r["fob_usd"],
                r.get("kg", r["fob_usd"] * 2.0),
            )
            for r in rows
        ],
    )


def _exp_series(
    start_year: int, n_years: int, final_value: float, rate: float, **fields: Any
) -> list[dict[str, Any]]:
    """n_years of exact exponential growth ending at final_value in the
    last year -> a perfect log-linear fit (regr_r2 == 1.0 exactly), so
    the expected slope/growth/r2 are known ahead of time without
    reimplementing OLS in the test. kg defaults (in _insert) to 2x fob
    for every row, which makes unit_price_usd_per_ton exactly 500
    regardless of totals - handy for asserting the sum-then-divide math
    independently of the growth shape.
    """
    base = final_value / math.exp(rate * (n_years - 1))
    return [
        {"year": start_year + i, "fob_usd": base * math.exp(rate * i), **fields}
        for i in range(n_years)
    ]


def test_level_mappings_cover_the_documented_levels() -> None:
    assert PRODUCT_LEVELS == {"ncm_code": "ncm_code", "category": "category", "overall": None}
    assert GEO_LEVELS == {
        "country": "country",
        "region": "region",
        "trade_bloc": "trade_bloc",
        "overall": None,
    }


def test_excludes_groups_below_min_years_active(con: duckdb.DuckDBPyConnection) -> None:
    rows = _exp_series(2023, 3, final_value=300, rate=0.5, ncm_code=PRODUCT_A, country="Shortland")
    _insert(con, rows)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )
    assert result.empty

    result_lower_floor = rank_markets(
        con,
        product_level="ncm_code",
        product_value=PRODUCT_A,
        geo_level="country",
        min_years_active=3,
    )
    assert list(result_lower_floor["country"]) == ["Shortland"]
    assert result_lower_floor.loc[0, "years_active"] == 3


def test_rank_markets_filters_by_fixed_product(con: duckdb.DuckDBPyConnection) -> None:
    country = "Wineland"
    rows_a = _exp_series(2020, 4, final_value=1000, rate=0.2, ncm_code=PRODUCT_A, country=country)
    rows_b = _exp_series(
        2020, 4, final_value=999_999, rate=0.2, ncm_code=PRODUCT_B, country=country
    )
    _insert(con, rows_a + rows_b)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )

    assert len(result) == 1
    expected_total = sum(r["fob_usd"] for r in rows_a)
    assert result.loc[0, "total_fob_usd"] == pytest.approx(expected_total)


def test_rank_markets_geo_level_region_groups_correctly(con: duckdb.DuckDBPyConnection) -> None:
    rows_m = _exp_series(
        2020, 4, final_value=500, rate=0.1, ncm_code=PRODUCT_A, country="M", region="TestRegion"
    )
    rows_n = _exp_series(
        2020, 4, final_value=500, rate=0.1, ncm_code=PRODUCT_A, country="N", region="TestRegion"
    )
    _insert(con, rows_m + rows_n)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="region"
    )

    assert list(result["region"]) == ["TestRegion"]
    expected_total = sum(r["fob_usd"] for r in rows_m + rows_n)
    assert result.loc[0, "total_fob_usd"] == pytest.approx(expected_total)


def test_rank_markets_geo_level_trade_bloc_does_not_raise(con: duckdb.DuckDBPyConnection) -> None:
    rows = _exp_series(
        2020, 4, final_value=500, rate=0.1, ncm_code=PRODUCT_A, country="M", trade_bloc="TestBloc"
    )
    _insert(con, rows)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="trade_bloc"
    )

    assert list(result["trade_bloc"]) == ["TestBloc"]


def test_rank_products_filters_by_fixed_geo(con: duckdb.DuckDBPyConnection) -> None:
    country = "Wineland"
    other_country = "Cheeseland"
    rows_target = _exp_series(
        2020, 4, final_value=800, rate=0.15, ncm_code=PRODUCT_A, country=country
    )
    rows_other = _exp_series(
        2020, 4, final_value=999_999, rate=0.15, ncm_code=PRODUCT_A, country=other_country
    )
    _insert(con, rows_target + rows_other)

    result = rank_products(con, geo_level="country", geo_value=country, product_level="ncm_code")

    assert len(result) == 1
    expected_total = sum(r["fob_usd"] for r in rows_target)
    assert result.loc[0, "total_fob_usd"] == pytest.approx(expected_total)


def test_product_level_overall_combines_all_products(con: duckdb.DuckDBPyConnection) -> None:
    country = "Wineland"
    rows_a = _exp_series(2020, 4, final_value=100, rate=0.1, ncm_code=PRODUCT_A, country=country)
    rows_b = _exp_series(2020, 4, final_value=200, rate=0.1, ncm_code=PRODUCT_B, country=country)
    _insert(con, rows_a + rows_b)

    result = rank_markets(con, product_level="overall", product_value=None, geo_level="country")

    assert len(result) == 1
    expected_total = sum(r["fob_usd"] for r in rows_a + rows_b)
    assert result.loc[0, "total_fob_usd"] == pytest.approx(expected_total)


def test_unknown_level_raises(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="Unknown level"):
        rank_markets(con, product_level="bogus", product_value="x", geo_level="country")

    with pytest.raises(ValueError, match="Unknown level"):
        rank_markets(con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="bogus")


def test_overall_as_ranked_axis_raises(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="ranked axis"):
        rank_markets(con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="overall")

    with pytest.raises(ValueError, match="ranked axis"):
        rank_products(con, geo_level="country", geo_value="Wineland", product_level="overall")


def test_fixed_value_required_unless_overall(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="fixed_value"):
        rank_markets(
            con, product_level="overall", product_value="should-be-none", geo_level="country"
        )

    with pytest.raises(ValueError, match="required"):
        rank_markets(con, product_level="ncm_code", product_value=None, geo_level="country")


def test_min_years_active_below_three_raises(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="min_years_active"):
        rank_markets(
            con,
            product_level="ncm_code",
            product_value=PRODUCT_A,
            geo_level="country",
            min_years_active=2,
        )


def test_perfect_exponential_trend_gives_full_r2_and_expected_growth(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rate = 0.3
    rows = _exp_series(
        2020, 6, final_value=10_000, rate=rate, ncm_code=PRODUCT_A, country="Wineland"
    )
    _insert(con, rows)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )

    row = result.iloc[0]
    assert row["years_active"] == 6
    assert row["trend_r2_adj"] == pytest.approx(1.0)
    assert row["annual_growth_pct"] == pytest.approx(math.exp(rate) - 1)


def test_share_pct_and_opportunity_score_ranking(con: duckdb.DuckDBPyConnection) -> None:
    rate = 0.2
    # same growth rate for both -> opportunity_score ordering is driven
    # purely by share_pct (the lower-share country should score higher).
    rows_p = _exp_series(2020, 5, final_value=300, rate=rate, ncm_code=PRODUCT_A, country="P")
    rows_q = _exp_series(2020, 5, final_value=700, rate=rate, ncm_code=PRODUCT_A, country="Q")
    _insert(con, rows_p + rows_q)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )

    by_country = result.set_index("country")
    assert by_country.loc["P", "share_pct"] == pytest.approx(0.3)
    assert by_country.loc["Q", "share_pct"] == pytest.approx(0.7)
    growth = math.exp(rate) - 1
    assert by_country.loc["P", "opportunity_score"] == pytest.approx(growth * 0.7)
    assert by_country.loc["Q", "opportunity_score"] == pytest.approx(growth * 0.3)
    # sorted descending by opportunity_score -> P (lower share) comes first
    assert list(result["country"]) == ["P", "Q"]


def test_unit_price_is_sum_then_divide_not_row_average(con: duckdb.DuckDBPyConnection) -> None:
    # kg = 2x fob for every row (the _insert default) -> unit price is
    # exactly 500 regardless of totals.
    uniform_rows = _exp_series(
        2020, 4, final_value=1000, rate=0.1, ncm_code=PRODUCT_A, country="Uniformland"
    )
    _insert(con, uniform_rows)

    # give kg a wildly different ratio to fob each year - if the module
    # ever averaged row-level (fob/kg) ratios instead of summing both
    # first, this would produce a different, wrong number.
    heterogeneous_rows = _exp_series(
        2020, 4, final_value=1000, rate=0.1, ncm_code=PRODUCT_A, country="Wobbleland"
    )
    for i, row in enumerate(heterogeneous_rows):
        row["kg"] = row["fob_usd"] * (1.0 if i % 2 == 0 else 50.0)
    _insert(con, heterogeneous_rows)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert result.loc["Uniformland", "unit_price_usd_per_ton"] == pytest.approx(500.0)

    expected_total_fob = sum(r["fob_usd"] for r in heterogeneous_rows)
    expected_total_kg = sum(r["kg"] for r in heterogeneous_rows)
    expected_unit_price = expected_total_fob / (expected_total_kg / 1000.0)
    assert result.loc["Wobbleland", "unit_price_usd_per_ton"] == pytest.approx(expected_unit_price)


def test_volume_confidence_uses_full_grid_not_just_fixed_product(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """K (the volume-confidence shrinkage constant) is the median group
    total across the FULL (product, geo) grid - adding a large, unrelated
    product/country pair should shift a group's volume_confidence even
    though that pair never appears in this query's own filtered results.
    """
    target_rows = _exp_series(
        2020, 4, final_value=1000, rate=0.1, ncm_code=PRODUCT_A, country="Wineland"
    )
    _insert(con, target_rows)

    result_before = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )
    volume_confidence_before = result_before.loc[0, "volume_confidence"]

    huge_rows = _exp_series(
        2020, 4, final_value=50_000_000, rate=0.1, ncm_code=PRODUCT_B, country="Bigland"
    )
    _insert(con, huge_rows)

    result_after = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )
    volume_confidence_after = result_after.loc[0, "volume_confidence"]

    assert volume_confidence_after < volume_confidence_before
