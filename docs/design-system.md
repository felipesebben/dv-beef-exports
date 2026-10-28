# Design system

The app follows the **Sebben design system**, shared with the company's other product
(the `dw-unify-databases` dashboards). The source of truth is that repo's
`design-system` skill (`.claude/skills/design-system/`, with its
`references/practices.md`) and `dashboards/src/style.css`. Read them before designing a
new page. This page records how the rules land in Streamlit here. Adopted 2026-10-28.

Two deliberate differences from the other product:

- **English copy.** The rules apply; the words stay English. Money is `$1.2M`, months
  are `Sep 2026`.
- **Streamlit, not Observable.** The same anatomy is built from `st.html` (cards, KPI
  cards, headlines) plus the theme and a small stylesheet.

## Where things live

| Rule | Where |
|---|---|
| Fonts, colours, radius, table header | `.streamlit/config.toml` (Inter Tight, cream, forest, pill buttons) |
| Tokens, chart style, stylesheet, components | `src/dv_beef_exports/app/design.py` |
| Logo | `src/dv_beef_exports/app/brand/sebben_symbol.png` (`st.logo`, 32px) |

## The rules, as applied

- **Brand is the chrome only:**
  - cream page `#f4efe6`, cards `#faf6ef`, warm header and hover `#ebe3d4`;
  - forest `#0d4f4a` for the title, links, focus and active controls;
  - the orange rule before the eyebrow is decoration only (it fails contrast).
- **Data keeps the validated palette:**
  - slot 1 blue `#2a78d6` and slot 2 orange `#eb6834`, also used for growth vs decline
    (colour-blind safe, never green/red);
  - context is a warm grey that sits on the cream card.
- **Type:**
  - Fraunces for the page title only;
  - Inter Tight everywhere else, charts included;
  - six sizes (14, 16, 22, 32, 40); **nothing under 14px**.
- **Every page:**
  - an eyebrow that carries the data date ("MARKET OVERVIEW · DATA TO SEP 2026");
  - the title, and one sentence saying what the page answers;
  - then filters, KPIs, cards, lists (`page_header()`).
- **One card per subject** (`card()` + `card_header()`):
  - a 22px title and a one-line muted subtitle, cut with "…";
  - a **headline** sentence with real values above each chart (`headline()`);
  - then the chart, with its own 16px title that carries **the unit**
    ("Value per month (US$)").
- **KPI cards** (`kpi_grid_html()`):
  - the number in ink, never coloured;
  - a short "▲ +21% vs a year earlier" line, with the exact periods compared in its
    tooltip;
  - a sparkline of the last 12 months (blue line) over the same months a year earlier
    (grey area);
  - one muted line of detail;
  - 4 per row, 2 on tablets, 1 on phones.
- **Charts:** always render through `show_chart()`.
  - It applies `style_chart()`, with recessive chrome and about 4 familiar ticks.
  - Legends sit **top right above the plot**. That Vega layout is added to the
    compiled spec, because Altair's schema doesn't know it.
  - Horizontal bar labels are **left-aligned** from the card's edge (`left_labels()`).
  - Time series put the value axis **on the right** (`time_axis_right()`).
  - Bars start at zero; one highlight, the rest muted.
- **Tables:** 40px rows (`row_height=40`), warm header, numbers in tabular figures.
- **Footer:** the sources, and where to report a wrong number.

## Checklist for a new page

- [ ] Eyebrow with the data date, the title, and one sentence.
- [ ] Filters in one row above what they affect; KPIs with context.
- [ ] One card per subject: title, one-line subtitle, headline, then the chart.
- [ ] Chart titles carry the unit; legends top right; nothing under 14px.
- [ ] Blue/orange for up/down, never green/red; one highlight, the rest muted.
- [ ] Rendered and looked at on desktop and phone.
