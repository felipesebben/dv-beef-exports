"""
Load ComexStat export rows into the tracked DuckDB file.

See docs/decisions/0003-storage-and-automation-strategy.md for why DuckDB,
and why the file itself is committed to git.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

DB_PATH = Path("data/processed/comexstat.duckdb")

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS exports (
        ncm_code    VARCHAR NOT NULL,
        ncm_name    VARCHAR NOT NULL,
        country     VARCHAR NOT NULL,
        year        INTEGER NOT NULL,
        month       INTEGER NOT NULL,
        fob_usd     BIGINT NOT NULL,
        kg          BIGINT NOT NULL,
        PRIMARY KEY (ncm_code, country, year, month)
)
"""

_DELETE_PERIOD_SQL = """
    DELETE FROM exports
    WHERE ncm_code = ANY(?)
    AND (year * 100 + month) BETWEEN ? AND ?
"""

_INSERT_SQL = """
    INSERT INTO exports (ncm_code, ncm_name, country, year, month, fob_usd, kg)
    VALUES (?, ?, ?, ?, ?, ?, ?)
"""

def get_connection(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open the tracked DuckDB file, creating its parent dir and the `exports` table if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_CREATE_TABLE_SQL)
    return con

def load_exports(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict[str, Any]],
    ncm_codes: list[str],
    period_from: str,
    period_to: str,
) -> int:
    """
    Load export rows into the `exports` table, replacing any existing rows
    for the same NCM codes and period first — safe to re-run for the same
    slice without creating duplicates.

    Assumes rows are shaped as returned by comexstat_client.fetch_exports()
    with its default details=["country", "ncm"] grouping (coNcm, ncm,
    country, year, monthNumber, metricFOB, metricKG). A different `details`
    grouping isn't supported by this loader yet.

    Args:
        con: open DuckDB connection (see get_connection()).
        rows: rows from comexstat_client.fetch_exports().
        ncm_codes: the NCM codes rows were fetched for — scopes the
            delete-before-insert to just this refresh's slice.
        period_from: "YYYY-MM", the period rows were fetched for.
        period_to: "YYYY-MM", the period rows were fetched for.

    Returns:
        Number of rows inserted.
    """
    con.execute(
        _DELETE_PERIOD_SQL,
        [ncm_codes, _year_month_to_int(period_from), _year_month_to_int(period_to)],
    )

    if not rows:
        return 0

    records = [
        (
            row["coNcm"],
            row["ncm"],
            row["country"],
            int(row["year"]),
            int(row["monthNumber"]),
            int(row["metricFOB"]),
            int(row["metricKG"]),
        )
        for row in rows
    ]
    con.executemany(_INSERT_SQL, records)
    return len(records)


def _year_month_to_int(period: str) -> int:
    """"YYYY-MM" -> YYYYMM as an int, e.g. "2024-03" -> 202403."""
    year, month = period.split("-")
    return int(year) * 100 + int(month)