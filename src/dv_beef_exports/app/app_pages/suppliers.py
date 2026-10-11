"""
Find suppliers: for one market and one product, which Brazilian states
already ship it there (ComexStat by state of production) and which plants
MAPA has approved to export it there (SIGSIF) - analysis/suppliers.py,
ADR 0007. The natural next step after the rankings: "we know the market and
the product - who do we buy from?"

Has its own pickers (the sidebar's ranking query doesn't apply); the
Explain page's "Find suppliers" button pre-fills them.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dv_beef_exports.analysis import suppliers
from dv_beef_exports.app.common import (
    SUPPLIERS_MARKET_KEY,
    SUPPLIERS_PRODUCT_KEY,
    connection,
    geo_options,
    product_phrase,
    product_scope_options,
    style_chart,
)
from dv_beef_exports.app.explain_charts import origin_states_chart
from dv_beef_exports.app.explanations import supplier_summary

# plants on at least this many of MAPA's lists count as proven exporters
# when the market keeps no list of its own
PROVEN_EXPORTER_LISTS = 5

_STATUS_LABELS = {"active": "Approved", "suspended": "Suspended", "expired": "Expired"}


def _md(text: str) -> str:
    """Escape $ - st.markdown renders text between two $ signs as LaTeX."""
    return text.replace("$", "\\$")


def _plants_table(plants: pd.DataFrame, listed: bool) -> pd.DataFrame:
    table = pd.DataFrame(
        {
            # what a trader needs first on a phone: who, where, how to reach them
            "Plant": plants["name"],
            "City": plants["municipality"].str.title(),
            "State": plants["uf"],
            "State's share": plants["state_share"],
            "Search": plants["search_url"].replace("", None),
            "Map": plants["map_url"].replace("", None),
            "Type": plants["plant_type"],
            "On MAPA lists": plants["markets"],
            "Approved for": plants["approved_for"],
            "Status": plants["status"].map(_STATUS_LABELS),
            "Approved on": pd.to_datetime(plants["approved_on"]),
            "Valid until": pd.to_datetime(plants["valid_until"]),
            "SIF": plants["sif"],
            "CNPJ": plants["cnpj"],
        }
    )
    if not listed:
        # their approvals are for other markets - dates and status would mislead
        table = table.drop(columns=["Approved on", "Valid until", "Status"])
    return table


st.title("Find suppliers")
st.caption(
    "You have a market and a product: which Brazilian states ship it there today, and which "
    "plants MAPA has approved to export it there."
)

con = connection()
if not suppliers.has_supplier_data(con):
    st.info(
        "Supplier data isn't in this database yet - it arrives with the next data refresh.",
        icon=":material/hourglass_empty:",
    )
    st.stop()

countries = geo_options(con, "country")
product_options = product_scope_options(con)
with st.container(horizontal=True):
    market = st.selectbox(
        "Market",
        countries,
        index=countries.index("Mexico") if "Mexico" in countries else 0,
        key=SUPPLIERS_MARKET_KEY,
        persist_state="session",
    )
    product_level, product_value, _ = st.selectbox(
        "Product",
        product_options,
        format_func=lambda option: option[2],
        key=SUPPLIERS_PRODUCT_KEY,
        persist_state="session",
    )

product = product_phrase(product_level, product_value)
categories = suppliers.categories_for(product_level, product_value)
listing = suppliers.market_listing(con, market)
origin = suppliers.origin_states(
    con, country=market, product_level=product_level, product_value=product_value
)
if listing["listed"]:
    plants = suppliers.approved_plants(con, country=market, categories=categories, origin=origin)
else:
    plants = suppliers.experienced_exporters(
        con, categories=categories, origin=origin, min_markets=PROVEN_EXPORTER_LISTS
    )

with st.container(border=True):
    st.markdown(_md(supplier_summary(market, product, listing, origin, plants)))

if not origin.empty:
    st.altair_chart(style_chart(origin_states_chart(origin, product, market)), width="stretch")
    st.caption(
        "ComexStat records the state where the goods were produced. It has no company "
        "detail, so a plant's state is a lead, not proof the plant ships this flow."
    )

st.subheader(
    "Approved plants" if listing["listed"] else "Proven exporters",
    help="Plants are listed by MAPA's SIF number. 'On MAPA lists' counts the markets whose "
    "list carries the plant for this kind of product (a bloc's list counts once) - a sign "
    "of export experience.",
)
if plants.empty:
    st.info("No plants match.", icon=":material/search_off:")
else:
    show_all = listing["listed"] and st.toggle(
        "Include suspended and expired approvals", value=False, key="suppliers_show_all"
    )
    shown = plants if show_all or not listing["listed"] else plants[plants["status"] == "active"]
    st.dataframe(
        _plants_table(shown, listing["listed"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Plant": st.column_config.TextColumn(pinned=True),
            "State's share": st.column_config.ProgressColumn(
                "State's share",
                help=f"Share of the {product} Brazil shipped to {market} in the last 12 months "
                "that came from the plant's state.",
                format="percent",
                min_value=0.0,
                max_value=1.0,
            ),
            "On MAPA lists": st.column_config.NumberColumn(format="%d"),
            "Approved on": st.column_config.DateColumn(format="DD MMM YYYY"),
            "Valid until": st.column_config.DateColumn(
                format="DD MMM YYYY", help="None: the approval has no end date."
            ),
            "Search": st.column_config.LinkColumn(
                "Search", display_text="Web search", help="Find the company's site and contacts"
            ),
            "Map": st.column_config.LinkColumn(
                "Map", display_text="Map", help="The registered address on a map"
            ),
        },
    )

changes = suppliers.recent_changes(con, country=market, categories=categories)
if not changes.empty:
    with st.expander(
        f"Changes in the latest MAPA update ({len(changes)})", icon=":material/update:"
    ):
        st.dataframe(
            changes.rename(
                columns={
                    "sif": "SIF",
                    "plant_name": "Plant",
                    "product": "Product",
                    "change": "Change",
                    "seen_on": "Seen on",
                }
            ),
            hide_index=True,
            width="stretch",
        )

with st.expander("About this data", icon=":material/info:"):
    as_of = f"{listing['as_of']:%d %b %Y}" if listing["as_of"] else "unknown"
    st.markdown(
        f"""
- **Approvals and plants**: MAPA's SIGSIF open data ("Estabelecimentos Nacionais
  Habilitados" and "Estabelecimentos Registrados no SIF", dados.agricultura.gov.br,
  licence CC-BY), checked weekly; this copy is from **{as_of}**. Each change is kept, so
  approvals and suspensions can be followed over time from now on.
- **Products**: MAPA approves *kinds* of product (raw, cured, heat-treated), not tariff
  codes. Raw ("em natureza") covers frozen cuts and offal alike, so a plant approved for
  raw beef shows for both.
- **Markets without a list** (Libya, Ghana, Guyana...) don't approve plants one by one: a
  SIF plant can usually ship with the right certificate. Check the market's requirements
  with MAPA before quoting.
- **States**: ComexStat exports by state of production, latest 12 months.
- Contact details are not copied: use the search and map links.
"""
    )
