"""
Exports by Brazilian state of origin: the same trade as raw.exports, broken
out by ComexStat's "Product UF" (`details: ["state"]` - the state the goods
were produced in, not the exporter's head office). It is the one ComexStat
dimension that lines up with SIGSIF's plant list, which records each
plant's state - so "which states ship frozen beef to Mexico" narrows "which
approved plants to call" (ADR 0007).

Kept apart from raw.exports/raw.pulls on purpose: build_staging() treats
any pull logged in raw.pulls as covering its codes x months, so logging a
state-level pull there would make it look authoritative for the
per-country table it never wrote to. Same medallion shape otherwise:

- raw.exports_by_state / raw.state_pulls - append-only, everything VARCHAR;
- staging.exports_by_state - newest pull covering a (code, month) wins;
- marts.exports_by_state - drops Brazil as a destination, adds the UF code
  and metric tons.

Only the last few years are kept (STATE_FIRST_YEAR onward) - the app asks
"where does this flow ship from today", not for 1997.

    uv run python -m dv_beef_exports.ingestion.exports_by_state   # backfill
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from dv_beef_exports.ingestion.comexstat_client import fetch_exports
from dv_beef_exports.ingestion.duckdb_loader import BRAZIL_CO_PAIS, DB_PATH, get_connection
from dv_beef_exports.ingestion.ncm_codes import all_codes

STATE_FIRST_YEAR = 2023
STATE_DETAILS = ["country", "state", "ncm"]
SECONDS_BETWEEN_CALLS = 5.0

# ComexStat returns the state's name; SIGSIF records the two-letter UF.
UF_BY_STATE_NAME = {
    "Acre": "AC",
    "Alagoas": "AL",
    "Amapá": "AP",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Ceará": "CE",
    "Distrito Federal": "DF",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Maranhão": "MA",
    "Mato Grosso": "MT",
    "Mato Grosso do Sul": "MS",
    "Minas Gerais": "MG",
    "Pará": "PA",
    "Paraíba": "PB",
    "Paraná": "PR",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Rondônia": "RO",
    "Roraima": "RR",
    "Santa Catarina": "SC",
    "São Paulo": "SP",
    "Sergipe": "SE",
    "Tocantins": "TO",
}

_CREATE_RAW_SQL = """
    CREATE TABLE IF NOT EXISTS raw.exports_by_state (
        pull_id         UUID NOT NULL,
        fetched_at      TIMESTAMPTZ NOT NULL,
        co_ncm          VARCHAR NOT NULL,
        ncm             VARCHAR NOT NULL,
        country         VARCHAR NOT NULL,
        state           VARCHAR NOT NULL,
        year            VARCHAR NOT NULL,
        month_number    VARCHAR NOT NULL,
        metric_fob      VARCHAR NOT NULL,
        metric_kg       VARCHAR NOT NULL
    )
"""

# same role as raw.pulls (see duckdb_loader): what each pull COVERED, so a
# row a newer pull no longer returns drops out of staging
_CREATE_PULLS_SQL = """
    CREATE TABLE IF NOT EXISTS raw.state_pulls (
        pull_id                 UUID NOT NULL PRIMARY KEY,
        pull_seq                BIGINT NOT NULL UNIQUE,
        fetched_at              TIMESTAMPTZ NOT NULL,
        requested_ncm_codes     VARCHAR[] NOT NULL,
        requested_period_from   VARCHAR NOT NULL,
        requested_period_to     VARCHAR NOT NULL,
        row_count               INTEGER NOT NULL
    )
"""

_BUILD_STAGING_SQL = """
    CREATE OR REPLACE TABLE staging.exports_by_state AS
    SELECT
        r.co_ncm                        AS ncm_code,
        d.category                      AS category,
        r.country                       AS country,
        c.co_pais                       AS co_pais,
        r.state                         AS state,
        CAST(r.year AS INTEGER)         AS year,
        CAST(r.month_number AS INTEGER) AS month,
        CAST(r.metric_fob AS BIGINT)    AS fob_usd,
        CAST(r.metric_kg AS BIGINT)     AS kg
    FROM raw.exports_by_state r
    JOIN raw.state_pulls rp ON rp.pull_id = r.pull_id
    JOIN staging.dim_ncm d ON d.ncm_code = r.co_ncm
    LEFT JOIN staging.dim_country c ON c.name = r.country
    WHERE NOT EXISTS (
        SELECT 1
        FROM raw.state_pulls p
        WHERE p.pull_seq > rp.pull_seq
            AND list_contains(p.requested_ncm_codes, r.co_ncm)
            AND r.year || '-' || r.month_number
                BETWEEN p.requested_period_from AND p.requested_period_to
    )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY r.co_ncm, r.country, r.state, r.year, r.month_number
        ORDER BY rp.pull_seq DESC
    ) = 1
"""


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(_CREATE_RAW_SQL)
    con.execute(_CREATE_PULLS_SQL)


def ingest_by_state(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict[str, Any]],
    ncm_codes: list[str],
    period_from: str,
    period_to: str,
) -> int:
    """Append one state-level pull to raw, and log what it covered (even
    when it returned nothing). Rows as returned by fetch_exports() with
    details=STATE_DETAILS."""
    ensure_tables(con)
    pull_id = uuid.uuid4()
    fetched_at = datetime.now(UTC)
    con.execute(
        "INSERT INTO raw.state_pulls SELECT ?, coalesce(max(pull_seq), 0) + 1, ?, ?, ?, ?, ? "
        "FROM raw.state_pulls",
        [pull_id, fetched_at, ncm_codes, period_from, period_to, len(rows)],
    )
    if rows:
        con.executemany(
            "INSERT INTO raw.exports_by_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    pull_id,
                    fetched_at,
                    row["coNcm"],
                    row["ncm"],
                    row["country"],
                    row["state"],
                    row["year"],
                    row["monthNumber"],
                    row["metricFOB"],
                    row["metricKG"],
                )
                for row in rows
            ],
        )
    return len(rows)


def build_by_state(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild staging.exports_by_state and marts.exports_by_state from raw
    (full replace, like build_staging()/build_marts()). Returns the marts
    row count."""
    ensure_tables(con)
    con.execute(_BUILD_STAGING_SQL)
    uf_cases = " ".join(f"WHEN '{name}' THEN '{uf}'" for name, uf in UF_BY_STATE_NAME.items())
    con.execute(f"""
        CREATE OR REPLACE TABLE marts.exports_by_state AS
        SELECT
            ncm_code, category, country, co_pais, state,
            CASE state {uf_cases} END AS uf,
            year, month, fob_usd, kg, kg / 1000.0 AS metric_ton
        FROM staging.exports_by_state
        WHERE co_pais IS DISTINCT FROM '{BRAZIL_CO_PAIS}'
    """)
    return con.execute("SELECT count(*) FROM marts.exports_by_state").fetchone()[0]


def pull_year(con: duckdb.DuckDBPyConnection, period_from: str, period_to: str) -> int:
    """Fetch and ingest one within-a-year period (ComexStat returns nothing
    for a state-level period that crosses a year boundary)."""
    codes = all_codes()
    rows = fetch_exports(codes, period_from, period_to, details=STATE_DETAILS)
    return ingest_by_state(con, rows, codes, period_from, period_to)


def state_mismatches(
    con: duckdb.DuckDBPyConnection, years: range
) -> list[tuple[str, str, tuple[int, int], int, int]]:
    """(code, country, month, by-state total, per-country total) wherever the
    state rows don't add up to staging.exports - the state pull's
    reconciliation gate, with no extra API call."""
    rows = con.execute(
        """
        WITH s AS (
            SELECT ncm_code, country, year, month, sum(fob_usd) AS fob
            FROM staging.exports_by_state
            WHERE year BETWEEN ? AND ?
            GROUP BY ALL
        ),
        e AS (
            SELECT ncm_code, country, year, month, sum(fob_usd) AS fob
            FROM staging.exports
            WHERE year BETWEEN ? AND ?
            GROUP BY ALL
        )
        SELECT
            coalesce(s.ncm_code, e.ncm_code), coalesce(s.country, e.country),
            coalesce(s.year, e.year), coalesce(s.month, e.month),
            coalesce(s.fob, 0), coalesce(e.fob, 0)
        FROM s FULL JOIN e USING (ncm_code, country, year, month)
        WHERE coalesce(s.fob, 0) <> coalesce(e.fob, 0)
        ORDER BY ALL
        """,
        [years.start, years.stop - 1, years.start, years.stop - 1],
    ).fetchall()
    return [(code, country, (y, m), int(a), int(b)) for code, country, y, m, a, b in rows]


def run_backfill(
    db_path: Path = DB_PATH,
    first_year: int = STATE_FIRST_YEAR,
    seconds_between_calls: float = SECONDS_BETWEEN_CALLS,
) -> int:
    """Pull every year from first_year to the database's latest month."""
    con = get_connection(db_path)
    try:
        last_year, last_month = con.execute(
            "SELECT year, month FROM staging.exports ORDER BY year DESC, month DESC LIMIT 1"
        ).fetchone()
        for year in range(first_year, last_year + 1):
            to_month = last_month if year == last_year else 12
            n = pull_year(con, f"{year}-01", f"{year}-{to_month:02d}")
            print(f"{year}: {n} rows")
            time.sleep(seconds_between_calls)
        built = build_by_state(con)
        bad = state_mismatches(con, range(first_year, last_year + 1))
        print(f"marts.exports_by_state: {built} rows; {len(bad)} mismatch(es) vs staging.exports")
        for item in bad[:20]:
            print("  ", item)
        return built
    finally:
        con.close()


if __name__ == "__main__":
    print(f"Backfilling exports by state from {STATE_FIRST_YEAR} ({date.today()})")
    run_backfill()
