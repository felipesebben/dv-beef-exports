"""Tests for the Explain page's charts (app/explain_charts.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from dv_beef_exports.app import explain_charts as charts
from dv_beef_exports.app.common import style_chart


def _history(fobs: list[float], tons_per_dollar: float = 0.0002) -> pd.DataFrame:
    """Oldest period first, ending Aug 2026; tiny first period not in the fit."""
    n = len(fobs)
    ends = pd.date_range(end="2026-08-01", periods=n, freq="12MS")
    in_fit = [f > 0 for f in fobs]
    in_fit[0] = False
    return pd.DataFrame(
        {
            "period_start": ends - pd.DateOffset(months=11),
            "period_end": ends,
            "periods_ago": list(range(n - 1, -1, -1)),
            "fob_usd": fobs,
            "metric_ton": [f * tons_per_dollar for f in fobs],
            "in_fit": in_fit,
            "trend_fob_usd": [max(f, 1) * 1.1 for f in fobs],
        }
    )


HISTORY = _history([42, 0, 13_000, 26_000, 52_000, 104_000])

PEERS = pd.DataFrame(
    {
        "name": [f"Country {i}" for i in range(14)] + ["Target"],
        "annual_growth_pct": [1.0 - i * 0.05 for i in range(14)] + [0.1],
        "share_pct": [0.01 * i for i in range(14)] + [0.0005],
        "opportunity_score": [1.0 - i * 0.05 for i in range(14)] + [0.09],
        "unit_price_usd_per_ton": [4000 + 100 * i for i in range(14)] + [5000],
    }
)

ROW = pd.Series(
    {
        "coverage_score": 0.5,
        "trend_r2_adj": 0.92,
        "volume_confidence": 0.59,
        "total_fob_usd": 10_300_000.0,
    }
)


def _all_charts() -> dict:
    scope = HISTORY.assign(fob_usd=HISTORY["fob_usd"] * 1000)
    return {
        "growth": charts.growth_context(HISTORY),
        "trend": charts.trend_context(HISTORY, log_scale=False),
        "trend_log": charts.trend_context(HISTORY, log_scale=True),
        "share": charts.share_context(HISTORY, scope, ("Share of exports", "Going to X")),
        "recent": charts.recent_context(HISTORY),
        "index": charts.index_breakdown(
            {"attractiveness": 0.7, "room to grow": 0.99, "commercial size": 0.5, "evidence": 0.9}
        ),
        "coverage": charts.coverage_context(HISTORY),
        "volume": charts.volume_context("Target", 10_300_000, 7_000_000),
        "confidence": charts.confidence_context(ROW),
        "size": charts.size_context(HISTORY),
        "price": charts.price_context(HISTORY, 4_015, "countries"),
        "compare": charts.comparison_chart(
            PEERS, "Target", "annual_growth_pct", "Growth per year", charts.fmt_growth, "countries"
        ),
    }


@pytest.mark.parametrize("name", list(_all_charts()))
def test_every_chart_is_valid_and_titled(name: str) -> None:
    spec = style_chart(_all_charts()[name]).to_dict()  # schema-validates

    titles = [spec.get("title")] + [sub.get("title") for sub in spec.get("vconcat", [])]
    assert any(t for t in titles), f"{name} has no title"


def test_size_context_is_two_stacked_charts_not_a_dual_axis() -> None:
    spec = charts.size_context(HISTORY).to_dict()

    assert len(spec["vconcat"]) == 2
    value_chart, tons_chart = spec["vconcat"]
    assert value_chart["encoding"]["y"]["field"] == "fob_usd"
    # the tons chart is layered with its one-container reference line
    assert tons_chart["layer"][0]["encoding"]["y"]["field"] == "metric_ton"


def test_comparison_appends_the_subject_with_its_rank_when_outside_the_top() -> None:
    chart = charts.comparison_chart(
        PEERS, "Target", "annual_growth_pct", "Growth per year", charts.fmt_growth, "countries"
    )
    data = chart.data if hasattr(chart, "data") and chart.data is not None else None
    spec = chart.to_dict()
    rows = next(iter(spec["datasets"].values())) if data is None else data.to_dict("records")

    assert len(rows) == 11  # top 10 + the subject
    subject = [r for r in rows if r["is_subject"]]
    assert len(subject) == 1 and subject[0]["axis_name"] == "#15  Target"
    assert spec["title"]["subtitle"][1] == "Target: #15 (shown last)"


def test_comparison_highlights_in_place_when_inside_the_top() -> None:
    spec = charts.comparison_chart(
        PEERS, "Country 2", "opportunity_score", "Score", charts.fmt_two, "countries"
    ).to_dict()

    rows = next(iter(spec["datasets"].values()))
    assert len(rows) == 10
    assert [r["name"] for r in rows if r["is_subject"]] == ["Country 2"]
    assert spec["title"]["subtitle"][1] == "Country 2: #3"


def test_tiny_shares_get_enough_decimals() -> None:
    assert charts._share_axis_format(0.001) == ".2%"
    assert charts._share_axis_format(0.05) == ".1%"
    assert charts._share_axis_format(0.6) == ".0%"
    assert charts.fmt_share(0.0005) == "<0.1%"


def test_contexts_without_enough_history_return_none() -> None:
    empty = _history([0, 0])
    assert charts.growth_context(empty) is None
    assert charts.price_context(empty, 4000, "countries") is None
