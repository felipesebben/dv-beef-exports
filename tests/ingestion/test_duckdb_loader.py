"""Tests for the DuckDB medallion loader (raw / staging / marts)."""

from pathlib import Path

import duckdb

from dv_beef_exports.ingestion.duckdb_loader import (
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
)

CHINA_FEB_2024 = {
    "coNcm": "02023000",
    "ncm": "Frozen bovine meat, boneless",
    "country": "China",
    "year": "2024",
    "monthNumber": "02",
    "metricFOB": "428582214",
    "metricKG": "96055461",
}


def _connect(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    return get_connection(tmp_path / "test.duckdb")


def test_get_connection_creates_schemas_and_raw_table(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    con = get_connection(db_path)

    assert db_path.exists()
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (0,)
    con.close()


def test_get_connection_seeds_dim_ncm_from_ncm_codes(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    count = con.execute("SELECT count(*) FROM staging.dim_ncm").fetchone()[0]
    boneless = con.execute(
        "SELECT description_en, category FROM staging.dim_ncm WHERE ncm_code = '02023000'"
    ).fetchone()

    assert count == 11
    assert boneless == ("Boneless beef, frozen", "frozen")
    con.close()


def test_ingest_raw_appends_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    assert inserted == 1
    row = con.execute(
        "SELECT co_ncm, country, year, month_number, metric_fob, metric_kg FROM raw.exports"
    ).fetchone()
    assert row == ("02023000", "China", "2024", "02", "428582214", "96055461")
    con.close()


def test_ingest_raw_records_what_was_requested(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    ingest_raw(
        con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03", details=["country", "ncm"]
    )

    row = con.execute(
        "SELECT requested_ncm_codes, requested_period_from, requested_period_to, "
        "requested_details FROM raw.exports"
    ).fetchone()
    assert row == (["02023000"], "2024-01", "2024-03", ["country", "ncm"])
    con.close()


def test_ingest_raw_does_not_overwrite_previous_pulls(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    # append-only: re-pulling the same period adds a second raw row, it
    # doesn't replace the first (see docs/decisions/0004)
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (2,)
    con.close()


def test_ingest_raw_handles_empty_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = ingest_raw(con, [], ["02023000"], "2024-01", "2024-03")

    assert inserted == 0
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (0,)
    con.close()


def test_build_staging_dedupes_to_the_most_recent_pull(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    # two conflicting pulls for the same key, inserted directly so fetched_at
    # ordering is controlled rather than relying on real clock timing
    con.execute(
        """
        INSERT INTO raw.exports VALUES
        ('11111111-1111-1111-1111-111111111111', '2024-04-01 00:00:00+00',
         ['02023000'], '2024-02', '2024-02', ['country', 'ncm'],
         '02023000', 'Frozen bovine meat, boneless', 'China',
         '2024', '02', '100000000', '20000000'),
        ('22222222-2222-2222-2222-222222222222', '2024-05-01 00:00:00+00',
         ['02023000'], '2024-02', '2024-02', ['country', 'ncm'],
         '02023000', 'Frozen bovine meat, boneless', 'China',
         '2024', '02', '428582214', '96055461')
        """
    )

    build_staging(con)

    row = con.execute(
        "SELECT fob_usd, kg FROM staging.exports WHERE ncm_code = '02023000' AND country = 'China'"
    ).fetchone()
    assert row == (428582214, 96055461)  # the later (2024-05) pull wins
    con.close()


def test_build_staging_types_and_joins_dim_ncm(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    build_staging(con)

    row = con.execute(
        "SELECT ncm_code, ncm_name, category, country, year, month, fob_usd, kg "
        "FROM staging.exports"
    ).fetchone()
    assert row == (
        "02023000",
        "Boneless beef, frozen",
        "frozen",
        "China",
        2024,
        2,
        428582214,
        96055461,
    )
    con.close()


def test_build_marts_converts_kg_to_metric_ton(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    build_staging(con)

    build_marts(con)

    row = con.execute("SELECT kg, metric_ton FROM marts.exports").fetchone()
    assert row == (96055461, 96055461 / 1000.0)
    con.close()
