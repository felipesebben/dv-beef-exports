"""
The Sebben design system, as used by the company's dw-unify-databases
dashboards (its `design-system` skill and `dashboards/src/style.css`),
brought to Streamlit so both products look and read the same.

- Brand colours are the chrome only (top bar, title, links, focus, buttons,
  backgrounds): cream page, cream-card surfaces, forest text accents. Orange
  and gold fail contrast on cream - decoration only (the eyebrow's rule).
- Charts keep the validated data palette (`dataviz` skill): slot 1 blue,
  slot 2 orange - also growth vs decline, colour-blind safe, never green/red.
- Type: Fraunces for the page title only, Inter Tight for everything else,
  six sizes (14, 16, 22, 32, 40), **nothing under 14px - charts included**.
- Layout: eyebrow + title + one sentence; one card per subject (title, a
  one-line subtitle, a headline sentence, then the chart); 4px spacing scale.
- Charts: unit in the title, a headline above, legends top right, category
  labels left-aligned, time series with the value axis on the right, about 4
  familiar ticks, quiet chrome.

The pure functions here (style_chart, kpi_grid_html, sparkline_svg...) build
strings/specs and are unit-tested; the st.* helpers render them.
"""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# --- brand (chrome only) -------------------------------------------------------
FOREST = "#0d4f4a"
FOREST_DEEP = "#083936"
TEAL = "#1a6b66"
CREAM = "#f4efe6"  # page
CREAM_CARD = "#faf6ef"  # cards, tables, chart surface
CREAM_WARM = "#ebe3d4"  # table header, hover row
BRAND_ORANGE = "#e89126"  # decoration only
LINE = "rgba(13,79,74,.12)"

# --- ink and chart chrome --------------------------------------------------------
INK = "#1a1a1a"  # 15.2:1 on cream
INK_SECONDARY = "#4a4a4a"
INK_MUTED = "#6b6b6b"  # 4.65:1 on cream - the floor for text
GRID = "#e4ddd0"
AXIS = "#c9bfae"
# names the charts already use
CHART_INK = INK_SECONDARY
CHART_MUTED = INK_MUTED
CHART_GRID = GRID
CHART_DOMAIN = AXIS

FONT = "'Inter Tight', -apple-system, 'Segoe UI', sans-serif"
TEXT_PX = 14  # the minimum, and every chart's text size

# --- data colours (validated palette; never brand colours) ----------------------
BLUE = "#2a78d6"  # slot 1: the subject, growth
BLUE_DARK = "#1c5cab"  # the strongest value in a blue ramp
BLUE_LIGHT = "#b7d3f6"  # the weakest still-visible blue
ORANGE = "#eb6834"  # slot 2: decline, the one weak spot
GREY = "#d3cbbd"  # peers / context, a warm grey that sits on the cream card
UP, DOWN = BLUE, ORANGE

LOGO = Path(__file__).parent / "brand" / "sebben_symbol.png"


# --- charts ----------------------------------------------------------------------


def style_chart(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    """The shared chart style: Inter Tight at 14px everywhere, the cream card
    as background, recessive chrome (muted labels, hairline grid on the value
    axis only, no ticks), about 4 familiar ticks, legends top right, a 16px
    bold title over a 14px muted subtitle. Call on the finished chart."""
    return (
        chart.configure(font=FONT, background=CREAM_CARD)
        .configure_axis(
            grid=True,
            gridColor=GRID,
            gridWidth=1,
            domainColor=AXIS,
            ticks=False,
            labelColor=INK_MUTED,
            labelFontSize=TEXT_PX,
            labelPadding=8,
            titleColor=INK_MUTED,
            titleFontSize=TEXT_PX,
            titleFontWeight="normal",
            titleAnchor="end",
            titlePadding=12,
        )
        .configure_axisQuantitative(tickCount=4)
        # categories never get gridlines
        .configure_axisBand(grid=False)
        .configure_view(strokeWidth=0)
        .configure_legend(
            orient="top",
            direction="horizontal",
            labelColor=INK_MUTED,
            labelFontSize=TEXT_PX,
            titleColor=INK_MUTED,
            titleFontSize=TEXT_PX,
            symbolSize=120,
            labelLimit=0,  # legend labels are short; never cut them
            offset=12,
        )
        .configure_text(font=FONT, fontSize=TEXT_PX, color=INK_SECONDARY)
        .configure_title(
            anchor="start",
            color=INK,
            font=FONT,
            fontSize=16,
            fontWeight=600,
            subtitleColor=INK_MUTED,
            subtitleFont=FONT,
            subtitleFontSize=TEXT_PX,
            subtitlePadding=4,
            offset=16,
        )
    )


def styled_spec(chart: alt.TopLevelMixin) -> dict:
    """The chart as a Vega-Lite spec in the shared style, with its legends
    anchored top right above the plot (design system). Vega supports that
    legend layout, but Altair's schema doesn't know it, so it's added to the
    compiled spec rather than through configure_legend()."""
    spec = style_chart(chart).to_dict()
    spec.setdefault("config", {}).setdefault("legend", {})["layout"] = {"top": {"anchor": "end"}}
    return spec


def show_chart(chart: alt.TopLevelMixin) -> None:
    """Render a chart in the shared style, full width."""
    st.vega_lite_chart(spec=styled_spec(chart), width="stretch")


def left_labels(limit: int = 170) -> dict:
    """Axis settings for horizontal bars: category labels read left to right
    from the chart's left edge, cut with an ellipsis - never right-aligned
    against the bars."""
    return {
        "labelAlign": "left",
        "labelPadding": limit,
        "labelLimit": limit - 12,
        "labelColor": INK,
        "domain": False,
    }


def time_axis_right(**kwargs) -> alt.Axis:
    """A time series' value axis on the trailing (right) side, so the plot's
    leading edge lines up with the text above it. The unit goes in the chart
    title, so no axis title."""
    return alt.Axis(orient="right", title=None, **kwargs)


# --- page chrome -----------------------------------------------------------------

_APP_CSS = f"""
<style>
@import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400&display=swap");
:root {{
  --brand-forest: {FOREST}; --brand-forest-deep: {FOREST_DEEP}; --brand-teal: {TEAL};
  --brand-cream: {CREAM}; --brand-cream-card: {CREAM_CARD}; --brand-cream-warm: {CREAM_WARM};
  --brand-orange: {BRAND_ORANGE}; --brand-line: {LINE};
  --ink-primary: {INK}; --ink-secondary: {INK_SECONDARY}; --ink-muted: {INK_MUTED};
  --serif: "Fraunces", Georgia, serif;
  --sans: {FONT};
}}

/* content max 1280px, 48px between the top bar and the page */
.block-container {{ max-width: 1280px; padding-top: 112px; padding-bottom: 64px; }}

/* page title: Fraunces 40/48 forest (32/40 on phones) - the only serif */
h1 {{ font-family: var(--serif) !important; font-weight: 400 !important;
  font-size: 40px !important; line-height: 48px !important; color: var(--brand-forest) !important;
  letter-spacing: 0 !important; padding: 0 0 8px !important; }}
@media (max-width: 639px) {{ h1 {{ font-size: 32px !important; line-height: 40px !important; }} }}

/* the eyebrow: one per page, forest caps after a 24px orange rule (decoration) */
.ds-eyebrow {{ display: flex; align-items: center; gap: 12px; margin: 0 0 8px;
  font: 600 14px/20px var(--sans); letter-spacing: .12em; text-transform: uppercase;
  color: var(--brand-forest); }}
.ds-eyebrow::before {{ content: ""; width: 24px; height: 2px; background: var(--brand-orange); }}
.ds-lede {{ font: 400 16px/24px var(--sans); color: var(--ink-secondary); margin: 0;
  max-width: 70ch; }}

/* one card per subject: cream-card surface, hairline, 8px, 24px padding */
div[class*="st-key-card_"] {{ background: var(--brand-cream-card);
  border: 1px solid var(--brand-line) !important; border-radius: 8px; padding: 24px !important; }}
.ds-card-title {{ font: 600 22px/28px var(--sans); color: var(--ink-primary); margin: 0; }}
.ds-subtitle {{ font: 400 14px/20px var(--sans); color: var(--ink-muted); margin: 4px 0 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.ds-headline {{ font: 600 16px/24px var(--sans); color: var(--ink-primary); margin: 0; }}
.ds-note {{ font: 400 14px/20px var(--sans); color: var(--ink-muted); margin: 0; }}

/* KPI cards: 4 per row, 2 on tablets, 1 on phones */
.ds-kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }}
@media (max-width: 1023px) {{ .ds-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
@media (max-width: 639px) {{ .ds-kpis {{ grid-template-columns: 1fr; }} }}
.ds-kpi {{ background: var(--brand-cream-card); border: 1px solid var(--brand-line);
  border-radius: 8px; padding: 16px; display: flex; flex-direction: column; gap: 4px; }}
.ds-kpi h3 {{ font: 600 16px/24px var(--sans) !important; color: var(--ink-primary);
  margin: 0 !important; padding: 0 !important; }}
.ds-big {{ font: 600 32px/40px var(--sans); color: var(--ink-primary); }}
.ds-kpi .ds-compare {{ font: 400 14px/20px var(--sans); color: var(--ink-primary); }}
.ds-kpi .ds-detail {{ font: 400 14px/20px var(--sans); color: var(--ink-muted); }}
.ds-kpi .ds-spark {{ display: block; margin: 8px 0 4px; width: 100%; height: 40px; }}

/* tables: the header in warm cream (the theme), text 14px */
[data-testid="stDataFrame"] {{ font-size: 14px; }}

/* buttons are pills (theme); sentence case, 14px 600 */
.stButton button, .stDownloadButton button {{ font: 600 14px/20px var(--sans); }}
:focus-visible {{ outline: 2px solid var(--brand-forest) !important; outline-offset: 2px; }}

/* the footer: data source and where to report a wrong number */
.ds-footer {{ font: 400 14px/20px var(--sans); color: var(--ink-muted);
  border-top: 1px solid var(--brand-line); padding-top: 16px; margin-top: 64px; }}
.ds-footer a {{ color: var(--brand-forest); }}
</style>
"""


def apply_style() -> None:
    """The app-wide stylesheet and the logo - call once per run, from main.py."""
    st.html(_APP_CSS)
    st.logo(str(LOGO), size="large")


def page_header(eyebrow: str, title: str, lede: str) -> None:
    """Every page starts the same: an eyebrow (area · data date), the title
    in Fraunces, and one sentence on what the page answers."""
    st.html(f'<p class="ds-eyebrow">{escape(eyebrow)}</p>')
    st.title(title)
    st.html(f'<p class="ds-lede">{escape(lede)}</p>')


def card(key: str):
    """One card per subject - use as `with card("prices"):`."""
    return st.container(border=True, key=f"card_{key}")


def card_header(title: str, subtitle: str | None = None) -> None:
    """A card's h2 and its one-line, regular, muted subtitle (cut with an
    ellipsis; the full text is in the tooltip)."""
    html = f'<h2 class="ds-card-title">{escape(title)}</h2>'
    if subtitle:
        html += f'<p class="ds-subtitle" title="{escape(subtitle)}">{escape(subtitle)}</p>'
    st.html(html)


def headline(text: str) -> None:
    """A chart's main message in one sentence, with real values, above it."""
    st.html(f'<p class="ds-headline">{_bold(escape(text))}</p>')


def note(text: str) -> None:
    st.html(f'<p class="ds-note">{_bold(escape(text))}</p>')


def footer(text_html: str) -> None:
    st.html(f'<div class="ds-footer">{text_html}</div>')


def _bold(text: str) -> str:
    """**x** -> <b>x</b>, so wording functions can keep their markdown emphasis."""
    parts = text.split("**")
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))


# --- KPI cards ---------------------------------------------------------------------


def sparkline_svg(current: list[float], previous: list[float] | None, label: str) -> str:
    """The KPI sparkline: the last 12 months as a blue line with a dot per
    month (the latest larger) over a grey area for the same months a year
    earlier, 220 x 40, no axes. Both share one
    scale spanning their own range (a dynamic range, as the design system
    allows for lines), so a price that moves 10% doesn't read as flat."""
    width, height, pad = 220, 40, 4
    side = 5  # room for the first and last dots, which would be cut in half at the edge
    series = [v for v in (*current, *(previous or [])) if v is not None and not pd.isna(v)]
    low, high = (min(series), max(series)) if series else (0.0, 1.0)
    span = (high - low) or (abs(high) or 1.0)
    # the lowest value sits a little above the bottom, so the area still shows
    floor = low - span * 0.15

    def y(value: float) -> float:
        if high == low:  # nothing moved: a flat line through the middle
            return height / 2
        value = low if pd.isna(value) else value
        return round(height - pad - (value - floor) / (high - floor) * (height - 2 * pad), 1)

    def points(values: list[float]) -> list[tuple[float, float]]:
        n = max(len(values) - 1, 1)
        return [(round(side + i * (width - 2 * side) / n, 1), y(v)) for i, v in enumerate(values)]

    parts = []
    if previous:
        pts = points(previous)
        area = " ".join(f"{x},{y}" for x, y in pts)
        first, last = pts[0][0], pts[-1][0]
        parts.append(f'<polygon points="{first},{height} {area} {last},{height}" fill="{GRID}" />')
    if current:
        pts = points(current)
        line = " ".join(f"{x},{y}" for x, y in pts)
        # the image stretches to the card's width: non-scaling strokes keep the
        # line 2px and the dots round (a dot is a zero-length round-capped stroke,
        # which - unlike a <circle> - doesn't squash into an oval)
        parts.append(
            f'<polyline points="{line}" fill="none" stroke="{BLUE}" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />'
        )
        for i, (x, y_) in enumerate(pts):
            size = 7 if i == len(pts) - 1 else 4  # the latest month stands out
            parts.append(
                f'<path class="dot" d="M{x},{y_} l0,0" stroke="{BLUE}" stroke-width="{size}" '
                'stroke-linecap="round" vector-effect="non-scaling-stroke" />'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" aria-label="{escape(label)}">'
        f"{''.join(parts)}</svg>"
    )


SPARKLINE_KEY = (
    "Blue line: the last 12 months, one dot per month (the latest is larger). "
    "Grey area: the same months a year earlier."
)


def sparkline_img(current: list[float], previous: list[float] | None, label: str) -> str:
    """The sparkline as an <img> with the SVG inline as a data URI: st.html
    sanitises the page's HTML and drops inline <svg>, but keeps images. The
    tooltip says how to read it; the alt text says what it shows."""
    svg = sparkline_svg(current, previous, label)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (
        f'<img class="ds-spark" src="data:image/svg+xml;base64,{data}" '
        f'alt="{escape(label)}" title="{escape(SPARKLINE_KEY)}" />'
    )


def comparison_text(change: float | None, basis: str, unit: str = "pct") -> str:
    """'▲ +21% vs a year earlier' - in ink; the triangle carries the direction."""
    if change is None or pd.isna(change):
        return "No earlier period to compare"
    arrow = "▲" if change > 0 else ("▼" if change < 0 else "■")
    amount = f"{change:+.0%}" if unit == "pct" else f"{change:+,.0f}"
    return f"{arrow} {amount} vs {basis}"


def kpi_grid_html(cards: list[dict]) -> str:
    """KPI cards: h3 title, the number in ink (never coloured), a short
    comparison line (exact periods in its tooltip), a sparkline, and one muted
    line of detail. Each card: title, value, compare, compare_title, spark
    (current, previous, label), detail."""
    html = []
    for c in cards:
        current, previous, spark_label = c["spark"]
        html.append(
            '<div class="ds-kpi">'
            f'<h3 title="{escape(c.get("help", ""))}">{escape(c["title"])}</h3>'
            f'<div class="ds-big">{escape(c["value"])}</div>'
            f'<div class="ds-compare" title="{escape(c.get("compare_title", ""))}">'
            f"{escape(c['compare'])}</div>"
            f"{sparkline_img(current, previous, spark_label)}"
            f'<div class="ds-detail">{escape(c.get("detail", ""))}</div>'
            "</div>"
        )
    return f'<div class="ds-kpis">{"".join(html)}</div>'
