"""
Load ComexStat export rows into the tracked DuckDB file.

Three schemas inside one file — raw / staging / marts — per
docs/decisions/0004-medallion-data-layering.md:
- raw: append-only, API responses close to as-returned, minimal casting.
- staging: typed, deduped (latest pull per key wins), joined to the NCM
  reference table.
- marts: business judgment — currently just kg -> metric ton. category
  stays a plain column, not rolled up across categories (open question,
  see the ADR).

See docs/decisions/0003-storage-and-automation-strategy.md for why
DuckDB, and why the file itself is committed to git.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dv_beef_exports.ingestion.ncm_codes import BEEF_NCM_CODES

DB_PATH = Path("data/processed/comexstat.duckdb")

_CREATE_RAW_EXPORTS_SQL = """
    CREATE TABLE IF NOT EXISTS raw.exports (
        pull_id                 UUID NOT NULL,
        fetched_at              TIMESTAMPTZ NOT NULL,
        requested_ncm_codes     VARCHAR[] NOT NULL,
        requested_period_from   VARCHAR NOT NULL,
        requested_period_to     VARCHAR NOT NULL,
        requested_details       VARCHAR[] NOT NULL,
        co_ncm                  VARCHAR NOT NULL,
        ncm                     VARCHAR NOT NULL,
        country                 VARCHAR NOT NULL,
        year                    VARCHAR NOT NULL,
        month_number            VARCHAR NOT NULL,
        metric_fob              VARCHAR NOT NULL,
        metric_kg               VARCHAR NOT NULL
        -- no PRIMARY KEY: append-only, duplicates across pulls expected.
    )
"""

_INSERT_RAW_SQL = """
    INSERT INTO raw.exports (
        pull_id, fetched_at, requested_ncm_codes, requested_period_from,
        requested_period_to, requested_details, co_ncm, ncm, country,
        year, month_number, metric_fob, metric_kg
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_CREATE_DIM_NCM_SQL = """
    CREATE OR REPLACE TABLE staging.dim_ncm (
        ncm_code       VARCHAR NOT NULL PRIMARY KEY,
        description_pt VARCHAR NOT NULL,
        description_en VARCHAR NOT NULL,
        category       VARCHAR NOT NULL
    )
"""

_INSERT_DIM_NCM_SQL = """
    INSERT INTO staging.dim_ncm (ncm_code, description_pt, description_en, category)
    VALUES (?, ?, ?, ?)
"""

_BUILD_STAGING_SQL = """
    CREATE OR REPLACE TABLE staging.exports AS
    SELECT
        r.co_ncm                        AS ncm_code,
        d.description_en                AS ncm_name,
        d.category                      AS category,
        r.country                       AS country,
        CAST(r.year AS INTEGER)         AS year,
        CAST(r.month_number AS INTEGER) AS month,
        CAST(r.metric_fob AS BIGINT)    AS fob_usd,
        CAST(r.metric_kg AS BIGINT)     AS kg,
        r.pull_id                       AS source_pull_id
    FROM raw.exports r
    JOIN staging.dim_ncm d ON d.ncm_code = r.co_ncm
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY r.co_ncm, r.country, r.year, r.month_number
        ORDER BY r.fetched_at DESC
    ) = 1
"""

_BUILD_MARTS_SQL = """
    CREATE OR REPLACE TABLE marts.exports AS
    SELECT
        ncm_code,
        ncm_name,
        category,
        country,
        year,
        month,
        fob_usd,
        kg,
        kg / 1000.0 AS metric_ton
    FROM staging.exports
"""


def get_connection(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open the tracked DuckDB file, creating the raw/staging/marts schemas,
    `raw.exports`, and `staging.dim` if needed. `staging.dim_ncm` is
    reseeded from ncm_codes.py on every call (cheap – 11 rows– and keeps
    it always in sync with the source instead of needing a separate
    migration step whenever the reference list changes).

    `staging.exports` and `marts.exports` are NOT created here – they only
    exist once build_staging()/build_marts() have run at least once. This
    is deliberate: querying them before a build has ever run should fail
    loudly, not silently return zero rows.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    con.execute("CREATE SCHEMA IF NOT EXISTS marts")
    con.execute(_CREATE_RAW_EXPORTS_SQL)
    _seed_dim_ncm(con)
    return con


def _seed_dim_ncm(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_DIM_NCM_SQL)
    records = [
        (ncm.code, ncm.description_pt, ncm.description_en, ncm.category) for ncm in BEEF_NCM_CODES
    ]
    con.executemany(_INSERT_DIM_NCM_SQL, records)


def ingest_raw(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict[str, Any]],
    ncm_codes: list[str],
    period_from: str,
    period_to: str,
    details: list[str] | None = None,
) -> int:
    """
    Append fetched rows to raw.exports. Never deletes or overwrites
    existing rows, even for a period already stored — every call gets its
    own pull_id + fetched_at, shared by all rows it inserts, so
    build_staging() can later pick the most recent pull per key. Re-running
    this for the same NCM codes/period is expected to add duplicate raw
    rows, not replace anything (see docs/decisions/0004).

    Assumes rows are shaped as returned by comexstat_client.fetch_exports()
    with its default details=["country", "ncm"] grouping (coNcm, ncm,
    country, year, monthNumber, metricFOB, metricKG). A different `details`
    grouping isn't supported by this loader yet.

    Args:
        con: open DuckDB connection (see get_connection()).
        rows: rows from comexstat_client.fetch_exports().
        ncm_codes: the NCM codes rows were fetched for.
        period_from: "YYYY-MM", the period rows were fetched for.
        period_to: "YYYY-MM", the period rows were fetched for.
        details: the `details` grouping rows were fetched with — must match
            what was actually passed to fetch_exports() (including its own
            default) so raw's provenance is accurate.

    Returns:
        Number of rows inserted.
    """
    if details is None:
        details = ["country", "ncm"]

    if not rows:
        return 0

    pull_id = uuid.uuid4()
    fetched_at = datetime.now(UTC)

    records = [
        (
            pull_id,
            fetched_at,
            ncm_codes,
            period_from,
            period_to,
            details,
            row["coNcm"],
            row["ncm"],
            row["country"],
            row["year"],
            row["monthNumber"],
            row["metricFOB"],
            row["metricKG"],
        )
        for row in rows
    ]
    con.executemany(_INSERT_RAW_SQL, records)
    return len(records)


def build_staging(con: duckdb.DuckDBPyConnection) -> int:
    """
    Rebuild staging.exports from raw.exports — a full replace, not
    incremental (raw is the only append-only layer; the data volume here
    is small enough that rebuilding from scratch is simpler than
    incremental upsert logic, see docs/decisions/0004). Dedups to the most
    recent raw pull per (ncm_code, country, year, month), and joins
    staging.dim_ncm for the NCM name/category.

    Returns the row count of the rebuilt table.
    """
    con.execute(_BUILD_STAGING_SQL)
    return con.execute("SELECT count(*) FROM staging.exports").fetchone()[0]


def build_marts(con: duckdb.DuckDBPyConnection) -> int:
    """
    Rebuild marts.exports from staging.exports — adds the kg -> metric ton
    conversion. `category` stays a plain column rather than being rolled up
    across categories, so cross-category weight comparability stays an
    open, query-time question (see docs/decisions/0004) rather than baked
    into the schema.

    Returns the row count of the rebuilt table.
    """
    con.execute(_BUILD_MARTS_SQL)
    return con.execute("SELECT count(*) FROM marts.exports").fetchone()[0]
