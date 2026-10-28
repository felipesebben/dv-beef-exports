"""Tests for the plain-language explanations (app/explanations.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from dv_beef_exports.app.explanations import (
    Selection,
    bottom_line,
    bridge_note,
    concentration_note,
    dependency_note,
    explain_all,
    explain_confidence,
    explain_growth,
    explain_recent,
    explain_score,
    explain_share,
    explain_size,
    explain_unit_price,
    growth_drivers,
    implied_typical_size,
    money,
    monthly_note,
    movers_note,
    overview_summary,
    plural,
    price_note,
    ranking_note,
    share_text,
    stalled_since,
    supplier_summary,
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
        "recent_growth_pct": 0.75326,
        "recent_consistency": 0.666667,
        "price_trend_pct": -0.062873,
        "tons_per_year": 22.77775,
        "materiality": 0.476744,
        "attractiveness": 0.709729,
        "opportunity_score": 31.325281,
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
        "recent_growth_pct",
        "trend_r2_adj",
        "share_pct",
        "total_fob_usd",
        "unit_price_usd_per_ton",
        "opportunity_score",
        "coverage_score",
        "volume_confidence",
        "confidence",
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


def test_score_explains_the_index_through_its_four_factors() -> None:
    explanation = explain_score(_row(), SEL)

    assert explanation.value == "31 / 100"
    assert "attractiveness **0.71**" in explanation.for_you
    assert "commercial size **0.48**" in explanation.for_you
    # commercial size (0.48) is Singapore's weakest factor
    assert "what holds it back most is commercial size" in explanation.action


def test_score_does_not_invent_a_weakness_when_every_factor_is_strong() -> None:
    strong = explain_score(
        _row(attractiveness=0.95, materiality=1.0, share_pct=0.03, confidence=0.86), SEL
    )

    assert "every factor is strong" in strong.action


def test_recent_momentum_wording() -> None:
    accelerating = explain_recent(_row(), SEL)
    falling = explain_recent(_row(recent_growth_pct=-0.2, recent_consistency=0.0), SEL)
    patchy = explain_recent(_row(recent_growth_pct=float("nan")), SEL)

    assert accelerating.value == "+75%/yr"
    assert "67% of the last year-on-year changes were up" in accelerating.for_you
    assert "flat or falling" in falling.action
    assert patchy.value == "n/a"


def test_size_speaks_in_containers() -> None:
    small = explain_size(_row(), SEL)  # 22.8 t a year
    big = explain_size(_row(tons_per_year=500.0), SEL)

    assert "under one 25-ton container a year" in small.for_you
    assert "Less than one container a year" in small.action
    assert "about **20 containers a year**" in big.for_you


def test_price_reads_level_and_trend() -> None:
    peers = pd.DataFrame({"unit_price_usd_per_ton": [1000.0, 2000.0, 3000.0]})

    falling = explain_unit_price(_row(), SEL, peers)
    rising = explain_unit_price(_row(price_trend_pct=0.10), SEL, peers)

    assert "about **-6% a year**" in falling.for_you
    assert "Falling prices" in falling.action
    assert "Rising prices" in rising.action


_ORIGIN = pd.DataFrame({"uf": ["GO", "RO"], "state": ["Goiás", "Rondônia"], "share": [0.38, 0.21]})


def _plants(*rows: tuple[str, str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["uf", "status"])


def test_supplier_summary_for_a_listed_market() -> None:
    plants = _plants(("GO", "active"), ("GO", "active"), ("RO", "active"), ("MT", "suspended"))
    listing = {"listed": True, "via_bloc": None}

    text = supplier_summary("Mexico", "boneless beef", listing, _ORIGIN, plants)

    assert "**3 plants are approved** to ship boneless beef to Mexico" in text
    assert "**Goiás supplied 38%**" in text and "then Rondônia (21%)" in text
    assert text.endswith("Start with the 2 in Goiás.")


def test_supplier_summary_names_the_bloc_list() -> None:
    listing = {"listed": True, "via_bloc": "European Union (EU)"}

    text = supplier_summary("Germany", "beef", listing, _ORIGIN, _plants(("MT", "active")))

    assert "**1 plant is approved**" in text and "(through the European Union (EU) list)" in text


def test_supplier_summary_when_nothing_is_approved() -> None:
    listing = {"listed": True, "via_bloc": None}

    text = supplier_summary("China", "cured beef", listing, _ORIGIN, _plants(("GO", "expired")))

    assert "**No plant is currently approved to ship cured beef to China**" in text
    assert "Start with" not in text


def test_supplier_summary_for_a_market_without_a_list() -> None:
    listing = {"listed": False, "via_bloc": None}

    text = supplier_summary(
        "Libya", "frozen livers", listing, pd.DataFrame(), _plants(("RO", "active"))
    )

    assert "**Libya doesn't approve Brazilian plants one by one**" in text
    assert "doesn't mean it's closed" in text
    assert "Brazil shipped no frozen livers to Libya in the last 12 months." in text


def _bridge(volume: float, price: float) -> dict:
    return {"previous": 14e9, "volume_effect": volume, "price_effect": price}


def test_bridge_note_names_what_drove_the_change() -> None:
    assert bridge_note(_bridge(0.93e9, 2.04e9)) == (
        "Value rose $3.0B: more volume added $930M and higher prices added $2.0B - prices drove it."
    )
    assert bridge_note(_bridge(-0.9e9, 0.1e9)).endswith("volume drove it.")
    assert "less volume took off $900M" in bridge_note(_bridge(-0.9e9, 0.1e9))
    assert bridge_note(_bridge(0.5e9, 0.4e9)).endswith("volume and price both played a part.")


def _concentration(*rows: tuple[str, float, float]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["top_destination", "top1_share", "top5_share"])


def test_dependency_note_compares_with_a_year_earlier() -> None:
    text = dependency_note(
        _concentration(("China", 0.56, 0.77), ("China", 0.49, 0.72)), "destination"
    )

    assert text.startswith("China took **49%** of the value in the latest 12 months, down from 56%")
    assert "the top 5 destinations took 72%" in text
    assert text.endswith("Heavy dependence on one buyer is a risk to plan around.")


def test_dependency_note_flags_a_new_top_buyer_and_a_spread_trade() -> None:
    text = dependency_note(
        _concentration(("Chile", 0.30, 0.6), ("Japan", 0.25, 0.6)), "destination"
    )

    assert "taking over from Chile" in text
    assert text.endswith("No single buyer dominates.")


def test_monthly_note_compares_the_last_three_months() -> None:
    monthly = pd.DataFrame(
        {
            "month": pd.date_range("2025-10-01", periods=12, freq="MS"),
            "fob_usd": [10.0] * 9 + [9.0, 8.0, 7.0],
            "prev_fob_usd": [10.0] * 12,
        }
    )

    assert monthly_note(monthly) == (
        "Jul–Sep 2026: $24, 20% below the same three months a year earlier."
    )


def test_movers_note_names_the_biggest_gain_and_loss() -> None:
    movers = pd.DataFrame(
        {"destination": ["United States", "Algeria"], "change": [5.25e8, -5.66e6]}
    )

    assert movers_note(movers, "destination") == (
        "United States gained the most (+$525M); Algeria lost the most (-$5.7M)."
    )
    assert movers_note(movers.iloc[:0], "destination") == "No destination changed."


def test_price_note_reads_the_biggest_buyer_against_the_average() -> None:
    pv = pd.DataFrame(
        {
            "destination": ["China", "United States", "Netherlands"],
            "unit_price_usd_per_ton": [5964.0, 6450.0, 8859.0],
            "price_vs_average": [1.064, 1.15, 1.58],
        }
    )

    assert price_note(pv, "destination") == (
        "China pays $5,964/t, 6% above the average; among the 10 biggest destinations, "
        "Netherlands pays the most ($8,859/t)."
    )


def test_ranking_note_names_the_leader_and_the_evidence() -> None:
    result = pd.DataFrame(
        {
            "name": ["Mexico", "Philippines", "Guyana"],
            "opportunity_score": [86.0, 73.0, 12.0],
            "confidence": [0.87, 0.97, 0.6],
        }
    )

    assert ranking_note(result) == (
        "Mexico leads with 86, ahead of Philippines (73); 2 of the top 3 are well evidenced "
        "(confidence 70%+)."
    )
