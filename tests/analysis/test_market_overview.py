"""Tests for the market overview metrics (analysis/market_overview.py)."""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd
import pytest

from dv_beef_exports.analysis.market_overview import market_trend, top_destinations_matrix

LIVERS, TONGUES = "02062200", "02062100"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA marts")
    connection.execute("""
        CREATE TABLE marts.exports (
            ncm_code VARCHAR, category VARCHAR, country VARCHAR, region VARCHAR,
            trade_bloc VARCHAR, year INTEGER, month INTEGER, fob_usd DOUBLE,
            metric_ton DOUBLE
        )
    """)
    yield connection
    connection.close()


def _insert(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> None:
    """month defaults to 12, so each 12-month period is a calendar year."""
    con.executemany(
        "INSERT INTO marts.exports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r.get("ncm_code", LIVERS),
                r.get("category", "offal"),
                r["country"],
                r.get("region"),
                r.get("trade_bloc"),
                r["year"],
                r.get("month", 12),
                r["fob_usd"],
                r["metric_ton"],
            )
            for r in rows
        ],
    )


def test_market_trend_sums_each_period_and_computes_yoy(con: duckdb.DuckDBPyConnection) -> None:
    _insert(
        con,
        [
            {"country": "A", "year": 2023, "fob_usd": 100, "metric_ton": 1},
            {"country": "A", "year": 2024, "fob_usd": 150, "metric_ton": 1},
            {"country": "B", "year": 2024, "fob_usd": 50, "metric_ton": 1},
        ],
    )

    trend = market_trend(con, window_years=3)

    assert list(trend["periods_ago"]) == [2, 1, 0]
    assert list(trend["fob_usd"]) == [0, 100, 200]
    assert list(trend["destinations"]) == [0, 1, 2]
    latest = trend.iloc[-1]
    assert latest["period_end"] == pd.Timestamp("2024-12-01")
    assert latest["period_start"] == pd.Timestamp("2024-01-01")
    assert latest["unit_price_usd_per_ton"] == pytest.approx(100)  # 200 / 2 t
    assert latest["fob_yoy_pct"] == pytest.approx(1.0)  # 100 -> 200
    assert latest["ton_yoy_pct"] == pytest.approx(1.0)  # 1 t -> 2 t
    assert latest["price_yoy_pct"] == pytest.approx(0.0)  # $100/t both years
    # nothing to compare against: NaN, not an infinite or zero growth rate
    assert pd.isna(trend.iloc[1]["fob_yoy_pct"])
    assert pd.isna(trend.iloc[0]["unit_price_usd_per_ton"])


def test_market_trend_oldest_period_still_gets_a_yoy(con: duckdb.DuckDBPyConnection) -> None:
    _insert(
        con,
        [
            {"country": "A", "year": 2022, "fob_usd": 100, "metric_ton": 1},
            {"country": "A", "year": 2023, "fob_usd": 120, "metric_ton": 1},
            {"country": "A", "year": 2024, "fob_usd": 180, "metric_ton": 1},
        ],
    )

    trend = market_trend(con, window_years=2)

    assert list(trend["fob_usd"]) == [120, 180]
    assert trend.iloc[0]["fob_yoy_pct"] == pytest.approx(0.2)


def test_market_trend_filters_to_the_scope(con: duckdb.DuckDBPyConnection) -> None:
    _insert(
        con,
        [
            {"country": "A", "year": 2024, "fob_usd": 100, "metric_ton": 1},
            {"country": "A", "year": 2024, "fob_usd": 900, "metric_ton": 1, "ncm_code": TONGUES},
            {"country": "B", "year": 2024, "fob_usd": 5000, "metric_ton": 1},
        ],
    )

    trend = market_trend(
        con, product_level="ncm_code", product_value=LIVERS, geo_level="country", geo_value="A"
    )

    assert trend.iloc[-1]["fob_usd"] == 100


def test_market_trend_uses_rolling_periods_not_calendar_years(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Data ending in June: the latest period is Jul-Jun, not a half year."""
    _insert(
        con,
        [
            {"country": "A", "year": 2023, "month": m, "fob_usd": 10, "metric_ton": 1}
            for m in range(1, 13)
        ]
        + [
            {"country": "A", "year": 2024, "month": m, "fob_usd": 10, "metric_ton": 1}
            for m in range(1, 7)
        ],
    )

    latest = market_trend(con, window_years=1).iloc[-1]

    assert latest["fob_usd"] == 120
    assert latest["period_start"] == pd.Timestamp("2023-07-01")
    assert latest["period_end"] == pd.Timestamp("2024-06-01")


def _matrix_rows() -> list[dict[str, Any]]:
    return [
        {"country": "Big", "year": 2024, "fob_usd": 800, "metric_ton": 1},
        {"country": "Big", "year": 2024, "fob_usd": 100, "metric_ton": 1, "ncm_code": TONGUES},
        {"country": "Big", "year": 2023, "fob_usd": 400, "metric_ton": 1},
        {"country": "Mid", "year": 2024, "fob_usd": 90, "metric_ton": 1, "ncm_code": TONGUES},
        {"country": "Small", "year": 2024, "fob_usd": 10, "metric_ton": 1},
    ]


def test_top_destinations_matrix_is_a_full_grid_of_the_top_n(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert(con, _matrix_rows())

    matrix = top_destinations_matrix(con, top_n=2)

    # Small is outside the top 2; the grid is 2 destinations x 2 products
    assert list(matrix["destination"]) == ["Big", "Big", "Mid", "Mid"]
    assert list(matrix["product"]) == [LIVERS, TONGUES, LIVERS, TONGUES]
    assert list(matrix["fob_usd"]) == [800, 100, 0, 90]
    big_livers = matrix.iloc[0]
    assert big_livers["prev_fob_usd"] == 400
    assert big_livers["fob_yoy_pct"] == pytest.approx(1.0)
    assert big_livers["share_of_destination"] == pytest.approx(800 / 900)
    # share of everything in scope - including Small, outside the top 2
    assert big_livers["share_of_total"] == pytest.approx(800 / 1000)
    assert pd.isna(matrix.iloc[1]["fob_yoy_pct"])  # Big tongues: nothing the year before


def test_top_destinations_matrix_carries_tons_and_price(con: duckdb.DuckDBPyConnection) -> None:
    _insert(
        con,
        [
            {"country": "A", "year": 2023, "fob_usd": 100, "metric_ton": 1},
            {"country": "A", "year": 2024, "fob_usd": 300, "metric_ton": 2},
            {"country": "B", "year": 2024, "fob_usd": 100, "metric_ton": 2},
        ],
    )

    cell = top_destinations_matrix(con).iloc[0]

    assert cell["destination"] == "A"
    assert cell["metric_ton"] == 2 and cell["prev_metric_ton"] == 1
    assert cell["unit_price_usd_per_ton"] == pytest.approx(150)
    assert cell["ton_yoy_pct"] == pytest.approx(1.0)
    assert cell["price_yoy_pct"] == pytest.approx(0.5)  # $100/t -> $150/t
    assert cell["ton_share_of_total"] == pytest.approx(0.5)  # 2 t of 4 t


def test_top_destinations_matrix_rank_by_tons_changes_who_is_top(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # Dear: biggest by value. Cheap: biggest by weight.
    _insert(
        con,
        [
            {"country": "Dear", "year": 2024, "fob_usd": 1000, "metric_ton": 1},
            {"country": "Cheap", "year": 2024, "fob_usd": 500, "metric_ton": 10},
        ],
    )

    by_value = top_destinations_matrix(con, top_n=1)
    by_tons = top_destinations_matrix(con, top_n=1, rank_by="metric_ton")

    assert list(by_value["destination"]) == ["Dear"]
    assert list(by_tons["destination"]) == ["Cheap"]


def test_top_destinations_matrix_by_region_drops_unassigned_countries(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert(
        con,
        [
            {"country": "A", "region": "Asia", "year": 2024, "fob_usd": 100, "metric_ton": 1},
            {"country": "B", "region": "Asia", "year": 2024, "fob_usd": 50, "metric_ton": 1},
            {"country": "C", "region": None, "year": 2024, "fob_usd": 999, "metric_ton": 1},
        ],
    )

    matrix = top_destinations_matrix(con, rows="region", columns="category")

    assert list(matrix["destination"]) == ["Asia"]
    assert list(matrix["product"]) == ["offal"]
    assert matrix.iloc[0]["fob_usd"] == 150


def test_top_destinations_matrix_validates_its_axes(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="rows"):
        top_destinations_matrix(con, rows="overall")
    with pytest.raises(ValueError, match="columns"):
        top_destinations_matrix(con, columns="country")
    with pytest.raises(ValueError, match="top_n"):
        top_destinations_matrix(con, top_n=0)
    with pytest.raises(ValueError, match="rank_by"):
        top_destinations_matrix(con, rank_by="unit_price_usd_per_ton")
