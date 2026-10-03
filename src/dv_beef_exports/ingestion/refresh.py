"""
Routine monthly refresh: keep the tracked DuckDB file current without
re-running the full backfill. Built to run unattended from GitHub Actions
(.github/workflows/refresh.yml), but works the same locally:

    uv run python -m dv_beef_exports.ingestion.refresh            # only if new data
    uv run python -m dv_beef_exports.ingestion.refresh --force    # re-pull regardless

Per docs/decisions/0003 and 0004 (both amended 2026-10-03):

1. Ask ComexStat for its newest published month (GET /general/dates/updated)
   and stop if the database already has it - unless forced.
2. Re-pull a trailing window - the previous calendar year through the newest
   month - not just the new month. ComexStat revises published months (seen
   live: June 2026 lost a row three months later), and build_staging()
   treats the newest pull covering a month as authoritative, so re-pulling
   the window is what lets revisions and removals land.
3. Rebuild staging and marts, and refresh the country/bloc dimensions (new
   country names would otherwise lose their codes).
4. Gate the result before anyone trusts it:
   - reconciliation: for every (NCM code, month) in the window, the sum of
     our per-country rows must equal ComexStat's own per-code total, fetched
     by a second, coarser query. A mismatch means a truncated or partial
     pull - the refresh FAILS.
   - plausibility: a newly added month whose total is far outside the
     previous 12 months' range is flagged for review (a warning, not a
     failure - beef exports genuinely swing).
   - revisions: every already-published month whose total changed is listed.

The outcome is written as a markdown summary (the refresh PR's body).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from dv_beef_exports.ingestion.comexstat_client import fetch_exports, fetch_latest_period
from dv_beef_exports.ingestion.duckdb_loader import (
    DB_PATH,
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
    refresh_dim_country,
)
from dv_beef_exports.ingestion.ncm_codes import all_codes

# re-pull this many calendar years before the newest month's year
REVISION_WINDOW_YEARS = 1
SECONDS_BETWEEN_CALLS = 5.0
# a new month outside [low, high] x the previous 12 months' median is flagged
PLAUSIBLE_RATIO = (0.4, 2.5)

Month = tuple[int, int]


@dataclass
class RefreshResult:
    """What a refresh did. `changed` drives whether a PR is opened."""

    latest_published: Month
    published_on: str
    latest_before: Month | None
    ran: bool = False
    new_months: list[Month] = field(default_factory=list)
    revisions: list[tuple[Month, float, float]] = field(default_factory=list)
    mismatches: list[tuple[str, Month, int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches

    @property
    def changed(self) -> bool:
        return self.ran and self.passed and bool(self.new_months or self.revisions)


def run_refresh(
    db_path: Path = DB_PATH,
    force: bool = False,
    revision_window_years: int = REVISION_WINDOW_YEARS,
    seconds_between_calls: float = SECONDS_BETWEEN_CALLS,
) -> RefreshResult:
    """Run one refresh against db_path - see the module docstring."""
    year, month, published_on = fetch_latest_period()
    latest_published = (year, month)

    con = get_connection(db_path)
    try:
        latest_before = _latest_stored_month(con)
        result = RefreshResult(latest_published, published_on, latest_before)
        if not force and latest_before is not None and latest_before >= latest_published:
            return result

        result.ran = True
        totals_before = _monthly_totals(con)
        codes = all_codes()
        window_years = range(year - revision_window_years, year + 1)

        for window_year in window_years:
            period_from, period_to = _year_period(window_year, latest_published)
            rows = fetch_exports(codes, period_from, period_to)
            ingest_raw(con, rows, codes, period_from, period_to)
            time.sleep(seconds_between_calls)

        refresh_dim_country(con)
        build_staging(con)
        build_marts(con)

        for window_year in window_years:
            period_from, period_to = _year_period(window_year, latest_published)
            time.sleep(seconds_between_calls)
            official = fetch_exports(codes, period_from, period_to, details=["ncm"])
            result.mismatches += _reconcile(con, official)

        totals_after = _monthly_totals(con)
        result.new_months = sorted(set(totals_after) - set(totals_before))
        result.revisions = [
            (m, totals_before[m], totals_after.get(m, 0.0))
            for m in sorted(totals_before)
            if m in _months_in(window_years) and totals_after.get(m, 0.0) != totals_before[m]
        ]
        result.warnings = _plausibility_warnings(totals_after, result.new_months)
        return result
    finally:
        con.close()


def _year_period(year: int, latest: Month) -> tuple[str, str]:
    """A whole calendar year, cut off at the newest published month."""
    last_month = latest[1] if year == latest[0] else 12
    return f"{year}-01", f"{year}-{last_month:02d}"


def _months_in(years: range) -> set[Month]:
    return {(y, m) for y in years for m in range(1, 13)}


def _latest_stored_month(con: duckdb.DuckDBPyConnection) -> Month | None:
    row = con.execute(
        "SELECT max(CAST(year AS INTEGER) * 100 + CAST(month_number AS INTEGER)) FROM raw.exports"
    ).fetchone()
    return None if row[0] is None else divmod(row[0], 100)


def _monthly_totals(con: duckdb.DuckDBPyConnection) -> dict[Month, float]:
    """Total FOB per month in staging - {} before the first build."""
    try:
        rows = con.execute(
            "SELECT year, month, sum(fob_usd) FROM staging.exports GROUP BY year, month"
        ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {(y, m): float(total) for y, m, total in rows}


def _reconcile(
    con: duckdb.DuckDBPyConnection, official: list[dict]
) -> list[tuple[str, Month, int, int]]:
    """(code, month, ours, official) for every per-code monthly total in
    `official` that our per-country staging rows don't add up to."""
    ours = dict(
        ((code, (y, m)), int(total))
        for code, y, m, total in con.execute(
            "SELECT ncm_code, year, month, sum(fob_usd) FROM staging.exports "
            "GROUP BY ncm_code, year, month"
        ).fetchall()
    )
    mismatches = []
    for row in official:
        key = (row["coNcm"], (int(row["year"]), int(row["monthNumber"])))
        expected = int(row["metricFOB"])
        if ours.get(key, 0) != expected:
            mismatches.append((key[0], key[1], ours.get(key, 0), expected))
    return mismatches


def _plausibility_warnings(totals: dict[Month, float], new_months: list[Month]) -> list[str]:
    warnings = []
    ordered = sorted(totals)
    for month in new_months:
        previous = [totals[m] for m in ordered if m < month][-12:]
        if len(previous) < 6:
            continue
        median = sorted(previous)[len(previous) // 2]
        ratio = totals[month] / median if median else float("inf")
        low, high = PLAUSIBLE_RATIO
        if not low <= ratio <= high:
            warnings.append(
                f"{_label(month)} total is {ratio:.2f}x the previous 12 months' median "
                f"(${totals[month]:,.0f} vs ${median:,.0f}) - check before merging."
            )
    return warnings


def _label(month: Month) -> str:
    return f"{month[0]}-{month[1]:02d}"


def _signed_money(amount: float) -> str:
    return f"{'+' if amount >= 0 else '-'}${abs(amount):,.0f}"


def _change_pct(before: float, after: float) -> str:
    """Enough decimals that a small revision doesn't print as 0.000%."""
    if not before:
        return "new"
    change = (after - before) / before
    return f"{change:+.4%}" if abs(change) >= 0.0001 else f"{change:+.2e}"


def summary_markdown(result: RefreshResult) -> str:
    """The refresh PR's body."""
    published = f"{_label(result.latest_published)} (published {result.published_on})"
    if not result.ran:
        return f"No refresh needed: the database already has ComexStat's latest month, {published}."
    before = _label(result.latest_before) if result.latest_before else "nothing"
    lines = [
        f"Monthly ComexStat refresh. Latest published month: **{published}**; "
        f"database previously ended at {before}.",
        "",
        "## Quality gate",
    ]
    if result.passed:
        lines.append(
            "- :white_check_mark: **Reconciliation passed**: for every product code and month "
            "re-pulled, our per-country rows add up exactly to ComexStat's own per-code totals."
        )
    else:
        lines.append(
            f"- :x: **Reconciliation FAILED** for {len(result.mismatches)} code-month(s) - "
            "likely a truncated or partial pull. Do not merge:"
        )
        lines += [
            f"  - {code} {_label(m)}: ours ${ours:,} vs ComexStat ${official:,}"
            for code, m, ours, official in result.mismatches[:20]
        ]
    lines += [f"- :warning: {w}" for w in result.warnings] or [
        "- :white_check_mark: New months are within the usual range."
    ]
    lines += ["", "## New months"]
    lines += [f"- {_label(m)}" for m in result.new_months] or ["- none"]
    lines += ["", "## Revisions to already-published months"]
    if result.revisions:
        lines += ["| Month | Before | After | Change |", "|---|---|---|---|"]
        lines += [
            f"| {_label(m)} | ${before:,.0f} | ${after:,.0f} | "
            f"{_signed_money(after - before)} ({_change_pct(before, after)}) |"
            for m, before, after in result.revisions
        ]
    else:
        lines.append("- none")
    lines += [
        "",
        "Review the numbers before merging (ADR 0003: manual review for the first few cycles).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--force", action="store_true", help="re-pull even if nothing new")
    parser.add_argument("--summary-path", type=Path, help="write the markdown summary here")
    args = parser.parse_args(argv)

    # module globals looked up at call time (not run_refresh's defaults,
    # which are bound at definition) so tests can point them elsewhere
    result = run_refresh(DB_PATH, force=args.force, seconds_between_calls=SECONDS_BETWEEN_CALLS)
    summary = summary_markdown(result)
    print(summary)
    if args.summary_path:
        args.summary_path.write_text(summary, encoding="utf-8")
    # for GitHub Actions: whether there's anything to open a PR for
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"changed={'true' if result.changed else 'false'}\n")
            out.write(f"latest={_label(result.latest_published)}\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
