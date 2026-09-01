"""Tests for the DuckDB export loader."""

from pathlib import Path

from dv_beef_exports.ingestion.duckdb_loader import get_connection, load_exports

CHINA_FEB_2024 = {
    "coNcm": "02023000",
    "ncm": "Frozen bovine meat, boneless",
    "country": "China",
    "year": "2024",
    "monthNumber": "02",
    "metricFOB": "428582214",
    "metricKG": "96055461",
}


def _connect(tmp_path: Path):
    return get_connection(tmp_path / "test.duckdb")


def test_get_connection_creates_file_and_table(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    con = get_connection(db_path)

    assert db_path.exists()
    assert con.execute("SELECT count(*) FROM exports").fetchone() == (0,)
    con.close()


def test_load_exports_inserts_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = load_exports(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    assert inserted == 1
    row = con.execute(
        "SELECT ncm_code, country, year, month, fob_usd, kg FROM exports"
    ).fetchone()
    assert row == ("02023000", "China", 2024, 2, 428582214, 96055461)
    con.close()


def test_load_exports_is_idempotent_for_the_same_period(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    load_exports(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    inserted_again = load_exports(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    assert inserted_again == 1
    assert con.execute("SELECT count(*) FROM exports").fetchone() == (1,)
    con.close()


def test_load_exports_only_replaces_rows_in_scope(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    other_ncm_row = {**CHINA_FEB_2024, "coNcm": "02062100", "ncm": "Tongues, frozen"}
    later_month_row = {**CHINA_FEB_2024, "monthNumber": "06"}

    load_exports(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    load_exports(con, [other_ncm_row], ["02062100"], "2024-01", "2024-03")
    load_exports(con, [later_month_row], ["02023000"], "2024-06", "2024-06")

    # a fresh load scoped to just the original NCM/period should leave the
    # other rows (different NCM, different month) untouched
    load_exports(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    assert con.execute("SELECT count(*) FROM exports").fetchone() == (3,)
    con.close()


def test_load_exports_handles_empty_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = load_exports(con, [], ["02023000"], "2024-01", "2024-03")

    assert inserted == 0
    con.close()
