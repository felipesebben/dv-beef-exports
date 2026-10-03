"""Tests for the monthly refresh (ingestion/refresh.py), against a fake ComexStat."""

from __future__ import annotations

from pathlib import Path

import pytest

from dv_beef_exports.ingestion import refresh
from dv_beef_exports.ingestion.duckdb_loader import (
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
)
from dv_beef_exports.ingestion.refresh import (
    _year_period,
    main,
    run_refresh,
    summary_markdown,
)

CODE = "02023000"


class FakeComexStat:
    """A tiny ComexStat: rows[(year, month)] = [(country, fob), ...]."""

    def __init__(self, latest: tuple[int, int], rows: dict) -> None:
        self.latest = latest
        self.rows = rows
        self.total_override: dict = {}  # (year, month) -> fob, to fake a mismatch

    def latest_period(self) -> tuple[int, int, str]:
        return (*self.latest, "2026-10-06")

    def fetch_exports(self, codes, period_from, period_to, details=None, month_detail=True):
        months = [m for m in self.rows if f"{m[0]}-{m[1]:02d}" >= period_from]
        months = [m for m in months if f"{m[0]}-{m[1]:02d}" <= period_to]
        if details == ["ncm"]:
            return [
                {
                    "coNcm": CODE,
                    "year": str(y),
                    "monthNumber": f"{m:02d}",
                    "metricFOB": str(
                        self.total_override.get((y, m), sum(f for _, f in self.rows[(y, m)]))
                    ),
                }
                for y, m in months
            ]
        return [
            {
                "coNcm": CODE,
                "ncm": "Frozen bovine meat, boneless",
                "country": country,
                "year": str(y),
                "monthNumber": f"{m:02d}",
                "metricFOB": str(fob),
                "metricKG": "1000",
            }
            for y, m in months
            for country, fob in self.rows[(y, m)]
        ]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "test.duckdb"


def _install(monkeypatch, fake: FakeComexStat) -> None:
    monkeypatch.setattr(refresh, "fetch_latest_period", fake.latest_period)
    monkeypatch.setattr(refresh, "fetch_exports", fake.fetch_exports)
    monkeypatch.setattr(refresh, "refresh_dim_country", lambda con: None)
    monkeypatch.setattr(refresh, "all_codes", lambda: [CODE])


def _seed(db: Path, fake: FakeComexStat, period_from: str, period_to: str) -> None:
    """The database as it stood before this refresh: an earlier pull."""
    con = get_connection(db)
    ingest_raw(
        con, fake.fetch_exports([CODE], period_from, period_to), [CODE], period_from, period_to
    )
    build_staging(con)
    build_marts(con)
    con.close()


def _history(last_month: int) -> dict:
    """Steady 2025 and 2026 history: China $100 + Chile $50 every month."""
    months = [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, last_month + 1)]
    return {month: [("China", 100), ("Chile", 50)] for month in months}


def test_year_period_cuts_the_latest_year_at_the_published_month() -> None:
    assert _year_period(2025, (2026, 8)) == ("2025-01", "2025-12")
    assert _year_period(2026, (2026, 8)) == ("2026-01", "2026-08")


def test_does_nothing_when_the_database_already_has_the_latest_month(db, monkeypatch) -> None:
    fake = FakeComexStat((2026, 8), _history(8))
    _seed(db, fake, "2025-01", "2026-08")
    _install(monkeypatch, fake)

    result = run_refresh(db, seconds_between_calls=0)

    assert not result.ran and not result.changed
    assert "No refresh needed" in summary_markdown(result)


def test_adds_the_new_month_and_applies_a_revision(db, monkeypatch) -> None:
    fake = FakeComexStat((2026, 8), _history(8))
    _seed(db, fake, "2025-01", "2026-08")
    # ComexStat publishes September, and revises June: Chile's row is removed
    fake.latest = (2026, 9)
    fake.rows[(2026, 9)] = [("China", 120), ("Chile", 40)]
    fake.rows[(2026, 6)] = [("China", 100)]
    _install(monkeypatch, fake)

    result = run_refresh(db, seconds_between_calls=0)

    assert result.ran and result.passed and result.changed
    assert result.latest_before == (2026, 8)
    assert result.new_months == [(2026, 9)]
    assert result.revisions == [((2026, 6), 150.0, 100.0)]
    con = get_connection(db)
    june = con.execute(
        "SELECT country FROM staging.exports WHERE year = 2026 AND month = 6"
    ).fetchall()
    con.close()
    assert june == [("China",)]
    summary = summary_markdown(result)
    assert "Reconciliation passed" in summary
    assert "| 2026-06 | $150 | $100 | -$50 (-33.3333%) |" in summary


def test_a_reconciliation_mismatch_fails_the_refresh(db, monkeypatch) -> None:
    fake = FakeComexStat((2026, 9), _history(9))
    fake.total_override[(2026, 9)] = 999  # official total disagrees with our rows
    _install(monkeypatch, fake)

    result = run_refresh(db, seconds_between_calls=0)

    assert not result.passed and not result.changed
    assert result.mismatches == [(CODE, (2026, 9), 150, 999)]
    assert "Reconciliation FAILED" in summary_markdown(result)


def test_an_implausible_new_month_is_flagged_not_failed(db, monkeypatch) -> None:
    fake = FakeComexStat((2026, 8), _history(8))
    _seed(db, fake, "2025-01", "2026-08")
    fake.latest = (2026, 9)
    fake.rows[(2026, 9)] = [("China", 5000)]  # 33x the usual month
    _install(monkeypatch, fake)

    result = run_refresh(db, seconds_between_calls=0)

    assert result.passed and result.changed
    assert len(result.warnings) == 1 and "2026-09" in result.warnings[0]


def test_main_writes_github_outputs_and_exits_nonzero_on_failure(
    db, monkeypatch, tmp_path: Path
) -> None:
    fake = FakeComexStat((2026, 9), _history(9))
    _install(monkeypatch, fake)
    monkeypatch.setattr(refresh, "DB_PATH", db)
    monkeypatch.setattr(refresh, "SECONDS_BETWEEN_CALLS", 0)
    outputs = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    summary_path = tmp_path / "summary.md"

    assert main(["--summary-path", str(summary_path)]) == 0
    assert "changed=true" in outputs.read_text() and "latest=2026-09" in outputs.read_text()
    assert "Monthly ComexStat refresh" in summary_path.read_text(encoding="utf-8")

    fake.latest = (2026, 10)
    fake.rows[(2026, 10)] = [("China", 100)]
    fake.total_override[(2026, 10)] = 1
    assert main([]) == 1
