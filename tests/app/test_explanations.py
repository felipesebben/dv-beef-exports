"""Tests for the plain-language explanations (app/explanations.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from dv_beef_exports.app.explanations import (
    Selection,
    bottom_line,
    concentration_note,
    explain_all,
    explain_confidence,
    explain_growth,
    explain_share,
    explain_unit_price,
    growth_drivers,
    implied_typical_size,
    money,
    overview_summary,
    plural,
    share_text,
    stalled_since,
    tons,
)

SEL = Selection(
    mode="markets",
    fixed_label="frozen livers",
    subject_label="Singapore",
    ranked_noun="country",
    rank=1,
    n_ranked=52,
    window_years=10,
    window_label="Sep 2016 - Aug 2026",
)


def _row(**overrides) -> pd.Series:
    """Singapore x frozen livers, as the real DB scores it."""
    base = {
        "country": "Singapore",
        "years_active": 8,
        "annual_growth_pct": 1.516455,
        "trend_r2_adj": 0.9151,
        "coverage_score": 0.8,
        "volume_confidence": 0.855518,
        "confidence": 0.855584,
        "share_pct": 0.002146,
        "opportunity_score": 1.513201,
        "total_fob_usd": 308_611.0,
        "total_metric_ton": 182.222,
        "unit_price_usd_per_ton": 1693.6,
    }
    return pd.Series({**base, **overrides})


def _history(fobs: list[float]) -> pd.DataFrame:
    """Oldest period first, ending Aug 2026; every positive period in the fit."""
    ends = pd.date_range(end="2026-08-01", periods=len(fobs), freq="12MS")
    return pd.DataFrame(
        {
            "period_end": ends,
            "fob_usd": fobs,
            "in_fit": [f > 0 for f in fobs],
        }
    )


GROWING = _history([524, 565, 752, 9_083, 19_599, 62_338, 110_513, 105_237])
STALLED = _history([112, 300, 450, 623, 0, 0])


def test_money_formats_by_magnitude() -> None:
    assert money(524) == "$524"
    assert money(9_083) == "$9,083"
    assert money(27_306) == "$27.3k"
    assert money(308_611) == "$309k"
    assert money(83_991_851) == "$84.0M"
    assert money(17.5e9) == "$17.5B"


def test_plural() -> None:
    assert plural("country") == "countries"
    assert plural("product category") == "product categories"
    assert plural("region") == "regions"


def test_share_text_never_shows_a_misleading_zero() -> None:
    assert share_text(0.00009) == "under 0.1%"
    assert share_text(0.002146) == "0.2%"
    assert share_text(0.6129) == "61%"


def test_implied_typical_size_inverts_volume_confidence() -> None:
    total, k = 308_611.0, 52_119.0
    assert implied_typical_size(total, total / (total + k)) == pytest.approx(k)


def test_stalled_since_flags_an_empty_latest_period() -> None:
    assert stalled_since(GROWING) is None
    assert stalled_since(STALLED) == STALLED["period_end"].iloc[3]


def test_explain_all_covers_each_metric_once() -> None:
    explanations = explain_all(_row(), SEL, GROWING, pd.DataFrame([_row()]))

    assert [e.key for e in explanations] == [
        "annual_growth_pct",
        "trend_r2_adj",
        "share_pct",
        "opportunity_score",
        "coverage_score",
        "volume_confidence",
        "confidence",
        "total_fob_usd",
        "unit_price_usd_per_ton",
    ]
    assert all(e.meaning and e.for_you and e.action for e in explanations)


def test_growth_uses_the_real_first_and_last_periods() -> None:
    text = explain_growth(_row(), SEL, GROWING).for_you

    assert "Brazil's exports of frozen livers to Singapore" in text
    assert "+152% a year" in text
    assert "$524 in the 12 months to Aug 2019" in text


def test_growth_warns_when_nothing_sold_in_the_latest_period() -> None:
    action = explain_growth(_row(), SEL, STALLED).action

    assert "Nothing was sold in the latest 12 months" in action


def test_share_wording_follows_the_lens() -> None:
    markets = explain_share(_row(), SEL).for_you
    products_sel = Selection(
        "products", "Vietnam", "frozen livers", "product", 1, 9, 10, "Sep 2016 - Aug 2026"
    )
    products = explain_share(_row(), products_sel).for_you

    assert "Singapore took **0.2%** of Brazil's total exports of frozen livers" in markets
    assert "frozen livers made up **0.2%** of everything Brazil sold to Vietnam" in products


def test_confidence_only_names_a_weak_leg_when_one_is_weak() -> None:
    strong = explain_confidence(_row(), SEL)
    weak = explain_confidence(_row(confidence=0.52, coverage_score=0.4), SEL)

    assert "nothing is weak" in strong.for_you
    assert "weakest: history" in weak.for_you
    assert "history (how many years it bought)" in weak.action


def test_unit_price_compares_with_the_ranked_peers() -> None:
    peers = pd.DataFrame({"unit_price_usd_per_ton": [1000.0, 2000.0, 3000.0]})

    cheap = explain_unit_price(_row(unit_price_usd_per_ton=1500.0), SEL, peers)
    dear = explain_unit_price(_row(unit_price_usd_per_ton=2600.0), SEL, peers)

    assert "25% below" in cheap.for_you and "52 countries" in cheap.for_you
    assert "30% above" in dear.for_you


@pytest.mark.parametrize(
    ("overrides", "history", "expected"),
    [
        ({}, GROWING, "**Strong lead**"),
        ({"share_pct": 0.61}, GROWING, "**Established position**"),
        ({"confidence": 0.55, "trend_r2_adj": 0.3}, GROWING, "**Promising but unproven**"),
        ({"confidence": 0.3}, GROWING, "**Hypothesis only**"),
        ({"annual_growth_pct": -0.2}, GROWING, "Not a growth lead right now"),
        ({}, STALLED, "**Check before acting**"),
    ],
)
def test_bottom_line_verdicts(overrides: dict, history: pd.DataFrame, expected: str) -> None:
    assert expected in bottom_line(_row(**overrides), SEL, history)


def test_bottom_line_flags_small_scale() -> None:
    small = bottom_line(_row(), SEL, GROWING)
    big = bottom_line(_row(total_fob_usd=84e6), SEL, GROWING)

    assert "Mind the scale: about $38.6k a year" in small
    assert "Mind the scale" not in big


def test_tons_formats_large_volumes_in_millions() -> None:
    assert tons(4.258) == "4.3 t"
    assert tons(182.2) == "182 t"
    assert tons(3_151_011) == "3.15 million t"


@pytest.mark.parametrize(
    ("value", "volume", "price", "expected"),
    [
        (0.309, 0.135, 0.153, "half more beef shipped, half higher prices"),
        (0.50, 0.05, 0.43, "mostly **higher prices**"),
        (0.50, 0.43, 0.05, "mostly **more beef shipped**"),
        (0.02, -0.09, 0.12, "the growth is **all price**"),
        (0.10, 0.20, -0.08, "the growth is **all volume**"),
        (-0.20, -0.10, -0.11, "**both** less beef and lower prices"),
        (-0.05, -0.15, 0.12, "mainly **less beef shipped**"),
        (-0.05, 0.10, -0.14, "mainly **lower prices**"),
    ],
)
def test_growth_drivers(value: float, volume: float, price: float, expected: str) -> None:
    assert expected in growth_drivers(value, volume, price)


def _trend(**latest: float) -> pd.DataFrame:
    row = {
        "period_end": pd.Timestamp("2026-08-01"),
        "fob_usd": 17.54e9,
        "metric_ton": 3_151_011,
        "destinations": 173,
        "fob_yoy_pct": 0.309,
        "ton_yoy_pct": 0.135,
        "price_yoy_pct": 0.153,
        **latest,
    }
    return pd.DataFrame([row])


def test_overview_summary_headline_and_drivers() -> None:
    text = overview_summary(_trend(), "Brazil's exports of beef to all markets")

    assert "In the 12 months to Aug 2026, Brazil's exports of beef to all markets" in text
    assert "**$17.5B** (3.15 million t) across **173** destination countries" in text
    assert "Value +31%" in text


def test_overview_summary_edge_cases() -> None:
    nothing = overview_summary(_trend(fob_usd=0), "x")
    no_prior = overview_summary(_trend(fob_yoy_pct=float("nan")), "x")
    single = overview_summary(_trend(destinations=1), "x")

    assert "recorded no sales" in nothing
    assert "no comparison" in no_prior
    assert "destination country." in single


def test_concentration_note_only_when_one_destination_dominates() -> None:
    dominated = pd.DataFrame(
        {"destination": ["China", "China", "US"], "share_of_total": [0.50, 0.03, 0.10]}
    )
    spread = pd.DataFrame({"destination": ["A", "B"], "share_of_total": [0.2, 0.15]})

    assert "**China alone took 53%**" in concentration_note(dominated, "country")
    assert concentration_note(spread, "country") is None
