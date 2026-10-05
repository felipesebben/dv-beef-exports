"""Tests for opportunity scoring and confidence (docs/decisions/0005)."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from dv_beef_exports.analysis.opportunity_scoring import (
    _OUTPUT_COLUMNS,
    GEO_LEVELS,
    METRIC_GLOSSARY,
    PRODUCT_LEVELS,
    latest_period_end,
    period_history,
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
            month      INTEGER,
            fob_usd    DOUBLE,
            metric_ton DOUBLE
        )
    """)
    yield connection
    connection.close()


def _insert(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> None:
    # month defaults to 12: with every row in December, each trailing
    # 12-month period lines up exactly with a calendar year, so yearly
    # fixtures behave as if periods were plain calendar years.
    con.executemany(
        """
        INSERT INTO marts.exports
            (ncm_code, category, country, region, trade_bloc, year, month, fob_usd, metric_ton)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["ncm_code"],
                r.get("category"),
                r["country"],
                r.get("region"),
                r.get("trade_bloc"),
                r["year"],
                r.get("month", 12),
                r["fob_usd"],
                r.get("metric_ton", r["fob_usd"] * 0.002),
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
    reimplementing OLS in the test. metric_ton defaults (in _insert) to
    0.002x fob for every row, which makes unit_price_usd_per_ton exactly 500
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


def test_share_pct_is_each_groups_slice_of_the_latest_period(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rows_p = _exp_series(2020, 5, final_value=300, rate=0.2, ncm_code=PRODUCT_A, country="P")
    rows_q = _exp_series(2020, 5, final_value=700, rate=0.2, ncm_code=PRODUCT_A, country="Q")
    _insert(con, rows_p + rows_q)

    by_country = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert by_country.loc["P", "share_pct"] == pytest.approx(0.3)
    assert by_country.loc["Q", "share_pct"] == pytest.approx(0.7)


def _market(country: str, values: list[float], tons_per_dollar: float = 0.01, **extra) -> list:
    return [
        {
            "year": 2017 + i,
            "fob_usd": v,
            "metric_ton": v * tons_per_dollar,
            "ncm_code": PRODUCT_A,
            "country": country,
            **extra,
        }
        for i, v in enumerate(values)
        if v > 0
    ]


STEADY = [1_000, 1_200, 1_440, 1_728, 2_074, 2_488, 2_986, 3_583, 4_300, 5_160]


def test_index_is_the_product_of_its_four_factors(con: duckdb.DuckDBPyConnection) -> None:
    _insert(con, _market("A", STEADY) + _market("B", [v * 2 for v in STEADY[::-1]]))

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    )

    expected = (
        100
        * result["attractiveness"]
        * (1 - result["share_pct"])
        * result["materiality"]
        * (0.5 + 0.5 * result["confidence"])
    )
    assert result["opportunity_score"].tolist() == pytest.approx(expected.tolist())
    assert result["opportunity_score"].between(0, 100).all()
    assert result["opportunity_score"].is_monotonic_decreasing


def test_materiality_measures_tons_a_year_against_one_container(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # 10 periods, 25 t a year exactly -> half-way; 1 t a year -> barely material
    _insert(
        con,
        _market("Container", [25_000] * 10, tons_per_dollar=0.001)
        + _market("Tiny", [25_000] * 10, tons_per_dollar=0.00004),
    )

    by_country = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert by_country.loc["Container", "tons_per_year"] == pytest.approx(25)
    assert by_country.loc["Container", "materiality"] == pytest.approx(0.5)
    assert by_country.loc["Tiny", "materiality"] == pytest.approx(1 / 26)


def test_a_tiny_fast_grower_ranks_below_a_material_steady_one(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The failure the index exists to fix: the old score ranked Vanuatu
    (3 t a year, explosive growth) above real markets."""
    explosive = [10 * 3**i for i in range(10)]
    _insert(
        con,
        _market("Tiny", explosive, tons_per_dollar=0.000001)
        + _market("Real", STEADY, tons_per_dollar=0.1),
    )

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert result.loc["Tiny", "annual_growth_pct"] > result.loc["Real", "annual_growth_pct"]
    assert result.loc["Real", "opportunity_score"] > result.loc["Tiny", "opportunity_score"]


def test_a_saturated_market_is_discounted_by_its_share(con: duckdb.DuckDBPyConnection) -> None:
    # identical shapes; "Saturated" is 9x bigger, so it holds 90% of the latest period
    _insert(
        con,
        _market("Open", STEADY, tons_per_dollar=1.0)
        + _market("Saturated", [v * 9 for v in STEADY], tons_per_dollar=1.0),
    )

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert result.loc["Saturated", "share_pct"] == pytest.approx(0.9)
    assert result.loc["Saturated", "attractiveness"] > result.loc["Open", "attractiveness"]
    assert result.loc["Open", "opportunity_score"] > result.loc["Saturated", "opportunity_score"]


def test_recent_components_reward_recent_constant_growth(con: duckdb.DuckDBPyConnection) -> None:
    flat_then_up = [1_000] * 6 + [1_000, 1_500, 2_250, 3_375]
    up_then_flat = [100 * 1.5**i for i in range(6)] + [759] * 4
    _insert(con, _market("Rising", flat_then_up) + _market("Stalled", up_then_flat))

    by_country = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    rising, stalled = by_country.loc["Rising"], by_country.loc["Stalled"]
    assert rising["recent_growth_pct"] == pytest.approx(0.5)  # x1.5 a year, last 4 periods
    assert rising["recent_consistency"] == pytest.approx(1.0)  # 3 of 3 changes up
    assert stalled["recent_growth_pct"] == pytest.approx(0.0, abs=1e-9)
    assert stalled["recent_consistency"] == pytest.approx(0.0)


def test_price_trend_is_the_yearly_change_in_price_per_ton(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # value steady, tons falling 20% a year -> $/t rising 25% a year
    rows = [
        {
            "year": 2017 + i,
            "fob_usd": 1_000.0,
            "metric_ton": 10 * 0.8**i,
            "ncm_code": PRODUCT_A,
            "country": "Pricier",
        }
        for i in range(10)
    ]
    _insert(con, rows)

    row = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]

    assert row["price_trend_pct"] == pytest.approx(0.25)


def test_components_needing_three_points_are_missing_not_zero(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # sales in only 2 of the last 4 periods -> no recent growth fit
    _insert(con, _market("Gappy", [1_000] * 6 + [0, 0, 1_200, 1_400]))

    row = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]

    assert pd.isna(row["recent_growth_pct"])
    # last 4 periods: 0, 0, 1,200, 1,400 - changes from zero are skipped (no
    # base to grow from), leaving one change, and it was up
    assert row["recent_consistency"] == pytest.approx(1.0)


def test_unit_price_is_sum_then_divide_not_row_average(con: duckdb.DuckDBPyConnection) -> None:
    # metric_ton = 0.002x fob for every row (the _insert default) -> unit
    # price is exactly 500 regardless of totals.
    uniform_rows = _exp_series(
        2020, 4, final_value=1000, rate=0.1, ncm_code=PRODUCT_A, country="Uniformland"
    )
    _insert(con, uniform_rows)

    # give tons a wildly different ratio to fob each year - if the module
    # ever averaged row-level (fob/ton) ratios instead of summing both
    # first, this would produce a different, wrong number.
    heterogeneous_rows = _exp_series(
        2020, 4, final_value=1000, rate=0.1, ncm_code=PRODUCT_A, country="Wobbleland"
    )
    for i, row in enumerate(heterogeneous_rows):
        row["metric_ton"] = row["fob_usd"] * (0.001 if i % 2 == 0 else 0.05)
    _insert(con, heterogeneous_rows)

    result = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).set_index("country")

    assert result.loc["Uniformland", "unit_price_usd_per_ton"] == pytest.approx(500.0)

    expected_total_fob = sum(r["fob_usd"] for r in heterogeneous_rows)
    expected_total_ton = sum(r["metric_ton"] for r in heterogeneous_rows)
    expected_unit_price = expected_total_fob / expected_total_ton
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


def _yearly(start_year: int, values: list[float], **fields: Any) -> list[dict[str, Any]]:
    return [{"year": start_year + i, "fob_usd": v, **fields} for i, v in enumerate(values)]


def test_tiny_leading_period_is_trimmed_before_the_fit(con: duckdb.DuckDBPyConnection) -> None:
    """The Bahrain case: a $42 first period, then steady doubling. Left in,
    the $42 -> $13,000 log jump dominates the slope; trimmed, the fit sees
    the clean doubling only."""
    _insert(
        con,
        _yearly(
            2020, [42, 13_000, 26_000, 52_000, 104_000], ncm_code=PRODUCT_A, country="Tinyland"
        ),
    )

    row = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]

    assert row["years_active"] == 4
    assert row["annual_growth_pct"] == pytest.approx(1.0)
    assert row["trend_r2_adj"] == pytest.approx(1.0)
    assert row["total_fob_usd"] == pytest.approx(13_000 + 26_000 + 52_000 + 104_000)


def test_tiny_trailing_period_is_kept_so_a_collapse_still_shows(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Trimming is leading-only: a market that collapses to almost nothing
    in its latest period must still read as shrinking, not have the
    collapse trimmed away."""
    _insert(
        con,
        _yearly(2020, [1_000, 2_000, 4_000, 8_000, 10], ncm_code=PRODUCT_A, country="Fadeland"),
    )

    row = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]

    assert row["years_active"] == 5
    assert row["annual_growth_pct"] < 0


def test_small_but_not_tiny_leading_period_is_kept(con: duckdb.DuckDBPyConnection) -> None:
    """The Singapore case: a first period at ~4% of the median is a real,
    small start, not noise - it stays in the fit."""
    _insert(
        con,
        _yearly(
            2020, [500, 13_000, 26_000, 52_000, 104_000], ncm_code=PRODUCT_A, country="Smalland"
        ),
    )

    row = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]

    assert row["years_active"] == 5


def test_period_history_fills_gaps_flags_trimmed_periods_and_matches_the_score(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # $42 leading period (trimmed), then clean doubling with a gap year
    _insert(
        con,
        _yearly(2019, [42, 13_000, 26_000, 0, 104_000, 208_000], ncm_code=PRODUCT_A, country="Gap"),
    )

    history = period_history(
        con,
        product_level="ncm_code",
        product_value=PRODUCT_A,
        geo_level="country",
        geo_value="Gap",
        window_years=8,
    )

    assert list(history["periods_ago"]) == [7, 6, 5, 4, 3, 2, 1, 0]
    assert list(history["fob_usd"]) == [0, 0, 42, 13_000, 26_000, 0, 104_000, 208_000]
    assert list(history["in_fit"]) == [False, False, False, True, True, False, True, True]
    assert history["period_end"].iloc[-1] == pd.Timestamp("2024-12-01")
    assert history["period_start"].iloc[-1] == pd.Timestamp("2024-01-01")

    # the fitted trend's yearly ratio is the score's own growth rate
    score = rank_markets(
        con, product_level="ncm_code", product_value=PRODUCT_A, geo_level="country"
    ).iloc[0]
    trend = history["trend_fob_usd"]
    assert trend.iloc[-1] / trend.iloc[-2] - 1 == pytest.approx(score["annual_growth_pct"])
    assert trend.iloc[-1] / trend.iloc[-2] == pytest.approx(2.0)


def test_period_history_validates_levels(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="Unknown level"):
        period_history(
            con, product_level="bogus", product_value=None, geo_level="overall", geo_value=None
        )
    with pytest.raises(ValueError, match="value is required"):
        period_history(
            con, product_level="ncm_code", product_value=None, geo_level="overall", geo_value=None
        )


def _monthly(
    first: tuple[int, int], last: tuple[int, int], fob_usd: float, **fields: Any
) -> list[dict[str, Any]]:
    """One row per month from first to last (inclusive), each (year, month)."""
    start = first[0] * 12 + first[1] - 1
    end = last[0] * 12 + last[1] - 1
    return [
        {"year": idx // 12, "month": idx % 12 + 1, "fob_usd": fob_usd, **fields}
        for idx in range(start, end + 1)
    ]


def test_latest_period_end_is_latest_year_and_month(con: duckdb.DuckDBPyConnection) -> None:
    _insert(con, _monthly((2023, 1), (2024, 6), 100, ncm_code=PRODUCT_A, country="Wineland"))

    assert latest_period_end(con) == (2024, 6)


def test_partial_latest_year_is_not_scored_as_a_full_year(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Flat monthly buying that ends mid-year. Scored on calendar years,
    the half-year 2024 would read as a 50% collapse; scored on trailing
    12-month periods (Jul-Jun), every period is a full, identical year,
    so growth is exactly zero.
    """
    _insert(con, _monthly((2020, 1), (2024, 6), 1000, ncm_code=PRODUCT_A, country="Flatland"))

    result = rank_markets(
        con,
        product_level="ncm_code",
        product_value=PRODUCT_A,
        geo_level="country",
        window_years=4,
    )

    row = result.iloc[0]
    assert row["years_active"] == 4
    assert row["annual_growth_pct"] == pytest.approx(0.0)
    # 4 full 12-month periods (Jul 2020 - Jun 2024), nothing from Jan-Jun 2020
    assert row["total_fob_usd"] == pytest.approx(48 * 1000)


def test_share_pct_uses_latest_twelve_months_not_calendar_year(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A seasonal buyer that only buys in October has zero share of a
    Jan-Jun partial year, but a real share of the latest 12 months."""
    steady = _monthly((2020, 1), (2024, 6), 1000, ncm_code=PRODUCT_A, country="Steadyland")
    autumn = [
        {"year": year, "month": 10, "fob_usd": 6000, "ncm_code": PRODUCT_A, "country": "Autumnland"}
        for year in range(2020, 2024)
    ]
    _insert(con, steady + autumn)

    result = rank_markets(
        con,
        product_level="ncm_code",
        product_value=PRODUCT_A,
        geo_level="country",
        window_years=4,
    ).set_index("country")

    # latest period Jul 2023 - Jun 2024: Autumnland 6,000 (Oct 2023) vs.
    # Steadyland 12 x 1,000
    assert result.loc["Autumnland", "share_pct"] == pytest.approx(6000 / 18000)
    assert result.loc["Steadyland", "share_pct"] == pytest.approx(12000 / 18000)
    assert result.loc["Autumnland", "years_active"] == 4


METHODOLOGY_DOC = Path(__file__).parents[2] / "docs" / "analysis-methodology.md"


def test_metric_glossary_covers_every_output_column() -> None:
    assert list(METRIC_GLOSSARY) == _OUTPUT_COLUMNS


def test_metric_glossary_matches_methodology_doc_quick_reference() -> None:
    """The app's column tooltips and the doc's quick-reference table must say
    the same thing - edit both together, or this fails."""
    doc = METHODOLOGY_DOC.read_text(encoding="utf-8")
    section = doc.split("## Quick reference", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(\w+)` \| (.+?) \|", section, flags=re.MULTILINE)

    assert dict(rows) == METRIC_GLOSSARY
