"""Tests for exports by Brazilian state of origin (ingestion/exports_by_state.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dv_beef_exports.ingestion.duckdb_loader import (
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
)
from dv_beef_exports.ingestion.exports_by_state import (
    build_by_state,
    ingest_by_state,
    state_mismatches,
)

CODE = "02023000"


def row(country: str, state: str, fob: int, month: str = "01", year: str = "2026") -> dict:
    return {
        "coNcm": CODE,
        "ncm": "Frozen bovine meat, boneless",
        "country": country,
        "state": state,
        "year": year,
        "monthNumber": month,
        "metricFOB": str(fob),
        "metricKG": "2000",
    }


@pytest.fixture
def con(tmp_path: Path):
    connection = get_connection(tmp_path / "test.duckdb")
    connection.executemany(
        "INSERT INTO staging.dim_country VALUES (?, ?)", [("493", "Mexico"), ("105", "Brazil")]
    )
    yield connection
    connection.close()


def test_build_maps_states_to_uf_and_drops_brazil(con) -> None:
    ingest_by_state(
        con,
        [
            row("Mexico", "Goiás", 100),
            row("Mexico", "Não Declarada", 5),
            row("Brazil", "São Paulo", 7),  # re-imports: not a market
        ],
        [CODE],
        "2026-01",
        "2026-01",
    )

    build_by_state(con)

    rows = con.execute(
        "SELECT country, state, uf, fob_usd, metric_ton FROM marts.exports_by_state ORDER BY ALL"
    ).fetchall()
    assert rows == [("Mexico", "Goiás", "GO", 100, 2.0), ("Mexico", "Não Declarada", None, 5, 2.0)]


def test_newest_pull_covering_a_month_wins_even_when_a_row_disappears(con) -> None:
    ingest_by_state(
        con, [row("Mexico", "Goiás", 100), row("Mexico", "Pará", 30)], [CODE], "2026-01", "2026-01"
    )
    # a revision: Pará's row is gone, Goiás revised
    ingest_by_state(con, [row("Mexico", "Goiás", 120)], [CODE], "2026-01", "2026-01")

    build_by_state(con)

    assert con.execute("SELECT state, fob_usd FROM marts.exports_by_state").fetchall() == [
        ("Goiás", 120)
    ]


def test_state_mismatches_checks_states_add_up_to_the_country_rows(con) -> None:
    per_country = [
        {
            "coNcm": CODE,
            "ncm": "x",
            "country": "Mexico",
            "year": "2026",
            "monthNumber": m,
            "metricFOB": "100",
            "metricKG": "1",
        }
        for m in ("01", "02")
    ]
    ingest_raw(con, per_country, [CODE], "2026-01", "2026-02")
    build_staging(con)
    build_marts(con)
    ingest_by_state(
        con,
        [
            row("Mexico", "Goiás", 60),
            row("Mexico", "Pará", 40),  # January: 60 + 40 = 100, fine
            row("Mexico", "Goiás", 90, month="02"),  # February: 90 != 100
        ],
        [CODE],
        "2026-01",
        "2026-02",
    )
    build_by_state(con)

    assert state_mismatches(con, range(2026, 2027)) == [(CODE, "Mexico", (2026, 2), 90, 100)]
