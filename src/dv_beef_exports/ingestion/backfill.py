"""
One-time full historical backfill: all tracked NCM codes, 1997-present.

Run directly:
    uv run python -m dv_beef_exports.ingestion.backfill

Chunked by year (not one call for the whole range) — a smaller, safer
per-request payload against an API that's already confirmed to soft
rate-limit (see comexstat_client.py), and easier to retry a single year
if something goes wrong partway through. A pause between years is just
good etiquette against a free, unauthenticated, public API — and, since
a real run can still exhaust even a patient client-level retry on a
sustained rate limit, a year that fails outright gets one more try after
a longer pause before finally being skipped (not fatal either way — the
rest of the range keeps going, and ingest_raw() only appends, so
re-running any year, or the whole range, is always safe).
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import duckdb

from dv_beef_exports.ingestion.comexstat_client import ComexStatTransientError, fetch_exports
from dv_beef_exports.ingestion.duckdb_loader import (
    DB_PATH,
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
)
from dv_beef_exports.ingestion.ncm_codes import all_codes

FIRST_YEAR = 1997
SECONDS_BETWEEN_YEARS = 5.0
RETRY_PASS_PAUSE_SECONDS = 30.0


def run_full_backfill(
    db_path: Path = DB_PATH,
    first_year: int = FIRST_YEAR,
    last_year: int | None = None,
    years: list[int] | None = None,
    seconds_between_years: float = SECONDS_BETWEEN_YEARS,
) -> list[int]:
    """
    Pull every tracked NCM code's history into raw.exports, one API call
    per year — then rebuild staging.exports and marts.exports once at the
    end (not per-year: a full rebuild is cheap, see docs/decisions/0004).

    A year whose fetch fails outright (the client's own retry/backoff
    exhausted — e.g. a sustained rate limit) gets one more try after a
    longer pause; if it still fails, it's skipped, not fatal, and the
    rest of the range keeps going. Returns the years still failing after
    that second pass, if any.

    Args:
        db_path: the DuckDB file to load into.
        first_year: first year to pull, if `years` isn't given
            (ComexStat's data starts 1997).
        last_year: last year to pull, if `years` isn't given. Defaults to
            the current year — asking for months not yet published is
            expected to just return fewer rows, not error.
        years: explicit years to pull, e.g. to retry ones a previous run
            skipped (`run_full_backfill(years=[2000, 2017])`). Overrides
            first_year/last_year when given.
        seconds_between_years: pause between year-chunks, on top of the
            client's own retry/backoff.
    """
    if years is None:
        if last_year is None:
            last_year = date.today().year
        years = list(range(first_year, last_year + 1))

    con = get_connection(db_path)
    try:
        ncm_codes = all_codes()
        failed_years = _pull_years(con, ncm_codes, years, seconds_between_years)

        if failed_years:
            print(f"Retrying {len(failed_years)} year(s) after a longer pause: {failed_years}")
            time.sleep(RETRY_PASS_PAUSE_SECONDS)
            failed_years = _pull_years(
                con, ncm_codes, failed_years, seconds_between_years, retry_pass=True
            )
        staging_count = build_staging(con)
        marts_count = build_marts(con)
        print(f"staging.exports: {staging_count} rows, marts.exports: {marts_count} rows")
        if failed_years:
            print(f"Still failing after retry (re-run these individually): {failed_years}")
    finally:
        con.close()
    return failed_years


def _pull_years(
    con: duckdb.DuckDBPyConnection,
    ncm_codes: list[str],
    years: list[int],
    seconds_between_years: float,
    retry_pass: bool = False,
) -> list[int]:
    failed_years = []
    for year in years:
        period_from = f"{year}-01"
        period_to = f"{year}-12"
        try:
            rows = fetch_exports(ncm_codes, period_from, period_to)
        except ComexStatTransientError as exc:
            label = "SKIPPED (retry pass)" if retry_pass else "SKIPPED, will retry once"
            print(f"{year}: {label} after client retries exhausted ({exc})")
            failed_years.append(year)
            time.sleep(seconds_between_years)
            continue
        inserted = ingest_raw(con, rows, ncm_codes, period_from, period_to)
        print(f"{year}: {inserted} rows ingested")
        time.sleep(seconds_between_years)
    return failed_years


if __name__ == "__main__":
    run_full_backfill()
