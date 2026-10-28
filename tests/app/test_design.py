"""Tests for the Sebben design system as applied to the app (app/design.py)."""

from __future__ import annotations

import base64

import altair as alt
import pandas as pd

from dv_beef_exports.app import design


def _bar() -> alt.Chart:
    data = pd.DataFrame({"name": ["A", "B"], "value": [1, 2], "kind": ["x", "y"]})
    return alt.Chart(data).mark_bar().encode(x="value:Q", y="name:N", color="kind:N")


def test_every_chart_text_is_at_least_14px_in_the_brand_sans() -> None:
    config = design.style_chart(_bar()).to_dict()["config"]

    assert config["font"] == design.FONT
    assert config["axis"]["labelFontSize"] >= 14
    assert config["axis"]["titleFontSize"] >= 14
    assert config["legend"]["labelFontSize"] >= 14
    assert config["text"]["fontSize"] >= 14
    assert config["title"]["subtitleFontSize"] >= 14
    assert config["title"]["fontSize"] == 16  # chart titles: 16px 600
    assert config["background"] == design.CREAM_CARD


def test_legends_sit_top_right_above_the_chart() -> None:
    spec = design.styled_spec(_bar())

    assert spec["config"]["legend"]["orient"] == "top"
    assert spec["config"]["legend"]["layout"] == {"top": {"anchor": "end"}}


def test_horizontal_bar_labels_read_left_to_right() -> None:
    axis = design.left_labels(150)

    assert axis["labelAlign"] == "left"
    assert axis["labelPadding"] == 150 and axis["labelLimit"] < 150


def test_time_series_put_the_value_axis_on_the_right() -> None:
    axis = design.time_axis_right(format="%").to_dict()

    assert axis["orient"] == "right" and axis["title"] is None


def test_comparison_line_carries_the_direction_in_a_triangle() -> None:
    assert design.comparison_text(0.212, "a year earlier") == "▲ +21% vs a year earlier"
    assert design.comparison_text(-0.05, "a year earlier") == "▼ -5% vs a year earlier"
    assert design.comparison_text(12, "a year earlier", unit="count") == ("▲ +12 vs a year earlier")
    assert design.comparison_text(float("nan"), "a year earlier") == (
        "No earlier period to compare"
    )


def test_sparkline_draws_this_year_over_last_years_area() -> None:
    svg = design.sparkline_svg([1, 2, 3], [2, 2, 2], "Value per month")

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 40"')
    assert 'aria-label="Value per month"' in svg
    assert f'fill="{design.GRID}"' in svg  # the year before, a grey area
    assert f'stroke="{design.BLUE}"' in svg  # this year, a blue line
    assert svg.index("<polygon") < svg.index("<polyline")  # line drawn on top


def test_sparkline_survives_missing_values() -> None:
    svg = design.sparkline_svg([float("nan"), 0.0], None, "x")

    assert "<polyline" in svg and "<polygon" not in svg


def test_kpi_cards_keep_the_number_in_ink_and_the_dates_in_the_tooltip() -> None:
    html = design.kpi_grid_html(
        [
            {
                "title": "Value, last 12 months",
                "value": "$17.0B",
                "compare": "▲ +21% vs a year earlier",
                "compare_title": "Oct '25–Sep '26 vs Oct '24–Sep '25",
                "spark": ([1, 2], [1, 1], "Value per month"),
                "detail": "FOB",
                "help": "Total FOB value",
            }
        ]
    )

    assert html.startswith('<div class="ds-kpis">')
    assert '<div class="ds-big">$17.0B</div>' in html
    assert 'title="Oct &#x27;25–Sep &#x27;26 vs Oct &#x27;24–Sep &#x27;25"' in html
    assert '<img class="ds-spark"' in html


def test_bold_markers_become_html() -> None:
    assert design._bold("China took **49%** of it") == "China took <b>49%</b> of it"


def test_kpi_sparkline_is_an_image_st_html_keeps() -> None:
    """Regression: st.html sanitises its HTML and drops inline <svg>, so the
    KPI sparklines never showed. They ship as a data-URI image instead."""
    html = design.sparkline_img([1, 2, 3], [2, 2, 2], "Value per month")

    assert html.startswith('<img class="ds-spark" src="data:image/svg+xml;base64,')
    assert "<svg" not in html
    svg = base64.b64decode(html.split("base64,")[1].split('"')[0]).decode()
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg  # required for an <img> source
    assert design.SPARKLINE_KEY in html  # the tooltip says how to read it
    assert "<svg" not in design.kpi_grid_html(
        [{"title": "t", "value": "1", "compare": "c", "spark": ([1, 2], [1, 1], "x")}]
    )


def test_sparkline_uses_its_own_range_so_small_moves_show() -> None:
    """A price moving 10% must not read as a flat line along the top."""
    svg = design.sparkline_svg([100, 110], [100, 100], "x")

    line = svg.split('<polyline points="')[1].split('"')[0]
    (_, y_start), (_, y_end) = (tuple(map(float, p.split(","))) for p in line.split())
    assert y_start - y_end > 20  # most of the 40px height, not a few pixels


def test_a_flat_sparkline_runs_through_the_middle() -> None:
    svg = design.sparkline_svg([5, 5], [5, 5], "x")

    assert '<polyline points="0.0,20.0 220.0,20.0"' in svg
