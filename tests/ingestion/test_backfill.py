"""Tests for the full historical backfill script."""

from datetime import date
from pathlib import Path

from dv_beef_exports.ingestion import backfill as backfill_module
from dv_beef_exports.ingestion.backfill import run_full_backfill
from dv_beef_exports.ingestion.comexstat_client import ComexStatTransientError
from dv_beef_exports.ingestion.duckdb_loader import get_connection


def _fake_row(period_from: str) -> dict:
    return {
        "coNcm": "02023000",
        "ncm": "Frozen bovine meat, boneless",
        "country": "China",
        "year": period_from.split("-")[0],
        "monthNumber": "01",
        "metricFOB": "1000",
        "metricKG": "500",
    }


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(backfill_module.time, "sleep", lambda seconds: None)


def test_run_full_backfill_makes_one_call_per_year(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.duckdb"
    calls: list[tuple[str, str]] = []

    def fake_fetch_exports(ncm_codes, period_from, period_to, **kwargs):
        calls.append((period_from, period_to))
        return [_fake_row(period_from)]

    monkeypatch.setattr(backfill_module, "fetch_exports", fake_fetch_exports)
    _no_sleep(monkeypatch)

    run_full_backfill(db_path=db_path, first_year=2023, last_year=2024)

    assert calls == [("2023-01", "2023-12"), ("2024-01", "2024-12")]


def test_run_full_backfill_ingests_and_builds_staging_and_marts(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "test.duckdb"

    def fake_fetch_exports(ncm_codes, period_from, period_to, **kwargs):
        return [_fake_row(period_from)]

    monkeypatch.setattr(backfill_module, "fetch_exports", fake_fetch_exports)
    _no_sleep(monkeypatch)

    run_full_backfill(db_path=db_path, first_year=2023, last_year=2024)

    con = get_connection(db_path)
    raw_count = con.execute("SELECT count(*) FROM raw.exports").fetchone()[0]
    staging_count = con.execute("SELECT count(*) FROM staging.exports").fetchone()[0]
    marts_count = con.execute("SELECT count(*) FROM marts.exports").fetchone()[0]
    con.close()

    assert raw_count == 2  # one row ingested per year, two years pulled
    assert staging_count == 2  # different years -> different keys, no dedup collision
    assert marts_count == 2


def test_run_full_backfill_uses_current_year_by_default(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.duckdb"
    calls: list[tuple[str, str]] = []

    def fake_fetch_exports(ncm_codes, period_from, period_to, **kwargs):
        calls.append((period_from, period_to))
        return []

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 3)

    monkeypatch.setattr(backfill_module, "fetch_exports", fake_fetch_exports)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(backfill_module, "date", _FixedDate)

    run_full_backfill(db_path=db_path, first_year=2025)

    assert calls == [("2025-01", "2025-12"), ("2026-01", "2026-12")]


def test_run_full_backfill_accepts_explicit_years(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.duckdb"
    calls: list[tuple[str, str]] = []

    def fake_fetch_exports(ncm_codes, period_from, period_to, **kwargs):
        calls.append((period_from, period_to))
        return [_fake_row(period_from)]

    monkeypatch.setattr(backfill_module, "fetch_exports", fake_fetch_exports)
    _no_sleep(monkeypatch)

    # first_year/last_year are ignored when years is given
    run_full_backfill(db_path=db_path, first_year=1997, years=[2000, 2017])

    assert calls == [("2000-01", "2000-12"), ("2017-01", "2017-12")]


def test_run_full_backfill_retries_a_failed_year_once_then_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "test.duckdb"
    call_count = {"2000": 0}

    def fake_fetch_exports(ncm_codes, period_from, period_to, **kwargs):
        if period_from == "2000-01":
            call_count["2000"] += 1
            if call_count["2000"] == 1:
                raise ComexStatTransientError("simulated rate limit")
        return [_fake_row(period_from)]

    monkeypatch.setattr(backfill_module, "fetch_exports", fake_fetch_exports)
    _no_sleep(monkeypatch)

    failed_years = run_full_backfill(db_path=db_path, years=[2000])

    assert call_count["2000"] == 2  # first pass failed, retry pass succeeded
    assert failed_years == []  # recovered -> not reported as still-failing

    con = get_connection(db_path)
    raw_count = con.execute("SELECT count(*) FROM raw.exports").fetchone()[0]
    con.close()
    assert raw_count == 1  # the successful retry-pass call, ingested once


def test_run_full_backfill_reports_years_still_failing_after_retry_pass(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "test.duckdb"

    def always_fails(ncm_codes, period_from, period_to, **kwargs):
        raise ComexStatTransientError("simulated sustained rate limit")

    monkeypatch.setattr(backfill_module, "fetch_exports", always_fails)
    _no_sleep(monkeypatch)

    failed_years = run_full_backfill(db_path=db_path, years=[2000, 2017])

    assert failed_years == [2000, 2017]
