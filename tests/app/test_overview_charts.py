"""Tests for the Market overview's analysis charts (app/overview_charts.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from dv_beef_exports.app import overview_charts as charts
from dv_beef_exports.app.common import DOWN, UP, style_chart

MONTHLY = pd.DataFrame(
    {
        "month": pd.date_range("2025-10-01", periods=12, freq="MS"),
        "fob_usd": [float(100 + i) for i in range(12)],
        "prev_fob_usd": [float(90 + i) for i in range(12)],
        "fob_yoy_pct": [0.1] * 12,
    }
)
BRIDGE = {
    "previous": 14.0e9,
    "volume_effect": 0.93e9,
    "price_effect": -0.2e9,
    "latest": 14.73e9,
    "old_price": 4933.0,
    "new_price": 4800.0,
    "previous_start": pd.Timestamp("2024-10-01"),
    "previous_end": pd.Timestamp("2025-09-01"),
    "latest_start": pd.Timestamp("2025-10-01"),
    "latest_end": pd.Timestamp("2026-09-01"),
}
MOVERS = pd.DataFrame(
    {
        "destination": ["United States", "Newland", "Algeria"],
        "fob_usd": [2.2e9, 5e6, 2.8e5],
        "prev_fob_usd": [1.7e9, 0.0, 5.9e6],
        "change": [5.25e8, 5e6, -5.66e6],
        "change_pct": [0.31, float("nan"), -0.95],
    }
)
PRICE_VOLUME = pd.DataFrame(
    {
        "destination": ["China", "United States", "Egypt"],
        "fob_usd": [8.3e9, 2.2e9, 4.4e8],
        "metric_ton": [1.4e6, 3.4e5, 1.0e5],
        "unit_price_usd_per_ton": [5965.0, 6450.0, 4434.0],
        "price_yoy_pct": [0.16, 0.10, 0.24],
        "price_vs_average": [1.06, 1.15, 0.79],
    }
)
CONCENTRATION = pd.DataFrame(
    {
        "period_start": pd.to_datetime(["2023-10-01", "2024-10-01", "2025-10-01"]),
        "period_end": pd.to_datetime(["2024-09-01", "2025-09-01", "2026-09-01"]),
        "periods_ago": [2, 1, 0],
        "top_destination": ["China", "China", "China"],
        "top1_share": [0.54, 0.56, 0.49],
        "top5_share": [0.76, 0.77, 0.72],
        "destinations": [156, 162, 174],
    }
)


def _all() -> dict:
    return {
        "monthly": charts.monthly_chart(MONTHLY),
        "bridge": charts.bridge_chart(BRIDGE),
        "movers": charts.movers_chart(MOVERS, "destination"),
        "price_volume": charts.price_volume_chart(PRICE_VOLUME, 5607.0),
        "concentration": charts.concentration_chart(CONCENTRATION),
    }


@pytest.mark.parametrize("name", list(_all()))
def test_every_overview_chart_is_valid_and_titled(name: str) -> None:
    spec = style_chart(_all()[name]).to_dict()  # schema-validates

    assert spec["title"]["text"]


def _rows(chart) -> list[dict]:
    spec = chart.to_dict()
    return [row for rows in spec["datasets"].values() for row in rows]


def test_bridge_steps_add_up_and_wear_the_colour_blind_safe_pair() -> None:
    spec = charts.bridge_chart(BRIDGE).to_dict()
    rows = next(iter(spec["datasets"].values()))

    assert [r["label"] for r in rows] == ["$14.0B", "+$930M", "-$200M", "$14.7B"]
    assert rows[2]["low"] == pytest.approx(14.93e9) and rows[2]["high"] == pytest.approx(14.73e9)
    colors = spec["layer"][0]["encoding"]["color"]["scale"]
    assert dict(zip(colors["domain"], colors["range"], strict=True))["down"] == DOWN


def test_movers_label_every_change_with_its_sign() -> None:
    rows = _rows(charts.movers_chart(MOVERS, "destination"))

    labels = {r["destination"]: r["label"] for r in rows if "label" in r}
    assert labels == {
        "United States": "+$525M (+31%)",
        "Newland": "+$5.0M",  # a new buyer: no growth rate to show
        "Algeria": "-$5.7M (-95%)",
    }
    # every label sits right of zero, never on top of the names
    assert min(r["label_at"] for r in rows if "label_at" in r) == 0


def test_gains_are_blue_and_losses_orange_never_green_or_red() -> None:
    color = charts.movers_chart(MOVERS, "destination").to_dict()["layer"][0]["encoding"]["color"]

    assert color["scale"]["range"] == [UP, DOWN]
    assert UP.lower() == "#2a78d6" and DOWN.lower() == "#eb6834"


def test_price_volume_names_only_the_biggest_buyers() -> None:
    rows = _rows(charts.price_volume_chart(PRICE_VOLUME, 5607.0, labelled=2))

    names = [r["name"] for r in rows if "name" in r]
    assert names == ["China", "United States", ""]


def test_signed_money() -> None:
    assert charts.signed_money(5.25e8) == "+$525M"
    assert charts.signed_money(-5.66e6) == "-$5.7M"
