"""
Where to source a product for a market: which Brazilian states already ship
it there (ComexStat by state of origin), and which plants MAPA has approved
to export it there (SIGSIF) - ADR 0007.

The two sources meet on the plant's state, never on the plant itself:
ComexStat has no company dimension. So "this plant is in Mato Grosso, which
ships 40% of Brazil's boneless beef to Mexico" is a lead, not a fact about
that plant's sales.

A market missing from SIGSIF altogether is NOT a closed market: SIGSIF only
lists the countries that approve Brazilian plants one by one. Elsewhere any
SIF plant can usually ship against the country's certificate, so the page
falls back to experienced beef exporters (plants on many markets' lists).
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import duckdb
import pandas as pd

from dv_beef_exports.ingestion.ncm_codes import BEEF_NCM_CODES
from dv_beef_exports.ingestion.sigsif import PRODUCT_CATEGORY_RULES

CATEGORIES = sorted({ncm.category for ncm in BEEF_NCM_CODES})
# MAPA's product lines in a trader's words - first match wins (the raw
# Portuguese name stays in `products`)
PRODUCT_KINDS: list[tuple[str, str]] = [
    (r"N[ÃA]O SUBMETIDOS A TRATAMENTO", "Not heat-treated products"),
    (r"ESTERILIZA", "Canned"),
    (r"COZID|COC[ÇC][ÃA]O", "Cooked"),
    (r"TRATAMENTO T[ÉE]RMICO", "Heat-treated"),
    (r"A BASE DE CARNE", "Meat products"),
    (r"INIBIDORES|SALGAD", "Cured / salted"),
    (r"MI[ÚU]DOS|EST[ÔO]MAGO", "Offal"),
    (r"HILTON", "Hilton quota beef"),
    (r"EM NATUREZA", "Raw beef & offal"),
    (r"IN NATURA|CONGELADA", "Raw beef"),
]
ORIGIN_MONTHS = 12
_STATUS_ORDER = {"active": 0, "suspended": 1, "expired": 2}


def categories_for(product_level: str, product_value: str | None) -> list[str]:
    """Our product categories a product selection falls under - SIGSIF
    approvals map to categories, never to single NCM codes (ADR 0007)."""
    if product_level == "overall":
        return CATEGORIES
    if product_level == "category":
        return [product_value]
    if product_level == "ncm_code":
        return [ncm.category for ncm in BEEF_NCM_CODES if ncm.code == product_value]
    raise ValueError(f"unknown product_level {product_level!r}")


def has_supplier_data(con: duckdb.DuckDBPyConnection) -> bool:
    """Whether the SIGSIF and by-state tables exist in this database."""
    tables = {
        f"{schema}.{name}"
        for schema, name in con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables()"
        ).fetchall()
    }
    return {"staging.sigsif_approvals", "marts.exports_by_state"} <= tables


def snapshot_date(con: duckdb.DuckDBPyConnection):
    """The date of the SIGSIF snapshot the staging tables were built from."""
    return con.execute("SELECT max(as_of) FROM staging.sigsif_approvals").fetchone()[0]


def origin_states(
    con: duckdb.DuckDBPyConnection,
    *,
    country: str,
    product_level: str = "overall",
    product_value: str | None = None,
    months: int = ORIGIN_MONTHS,
) -> pd.DataFrame:
    """Which Brazilian states this flow ships from, over the latest `months`.

    Returns uf, state, fob_usd, metric_ton, share (of the flow's value),
    period_start, period_end - largest first; empty if nothing shipped.
    """
    if product_level == "ncm_code":
        product_sql, params = "AND ncm_code = ?", [product_value]
    elif product_level == "category":
        product_sql, params = "AND category = ?", [product_value]
    else:
        product_sql, params = "", []
    df = con.execute(
        f"""
        WITH bounds AS (
            SELECT max(year * 12 + month - 1) AS last_idx FROM marts.exports_by_state
        )
        SELECT
            coalesce(uf, '-')                               AS uf,
            state,
            sum(fob_usd)                                    AS fob_usd,
            sum(metric_ton)                                 AS metric_ton,
            make_date(((any_value(last_idx) - ? + 1) // 12)::INT,
                      ((any_value(last_idx) - ? + 1) % 12 + 1)::INT, 1) AS period_start,
            make_date((any_value(last_idx) // 12)::INT,
                      (any_value(last_idx) % 12 + 1)::INT, 1)            AS period_end
        FROM marts.exports_by_state, bounds
        WHERE year * 12 + month - 1 > last_idx - ?
            AND country = ?
            {product_sql}
        GROUP BY ALL
        HAVING sum(fob_usd) > 0
        ORDER BY fob_usd DESC
        """,
        [months, months, months, country, *params],
    ).df()
    total = df["fob_usd"].sum()
    df["share"] = df["fob_usd"] / total if total else 0.0
    return df


def market_listing(con: duckdb.DuckDBPyConnection, country: str) -> dict:
    """Whether MAPA keeps a per-plant export list for this market, and how
    current the SIGSIF snapshot is: {listed, via_bloc, as_of}."""
    row = con.execute(
        """
        SELECT count(*) > 0, any_value(via_bloc),
               (SELECT max(as_of) FROM staging.sigsif_approvals)
        FROM staging.sigsif_approvals WHERE country = ?
        """,
        [country],
    ).fetchone()
    return {"listed": bool(row[0]), "via_bloc": row[1], "as_of": row[2]}


def _plants_sql(market_filter: str) -> str:
    return f"""
        WITH matching AS (
            SELECT a.*
            FROM staging.sigsif_approvals a
            WHERE len(list_intersect(a.categories, ?)) > 0
                {market_filter}
        ),
        per_plant AS (
            SELECT
                sif,
                string_agg(DISTINCT product, '; ' ORDER BY product)       AS products,
                min(CASE status WHEN 'active' THEN 0 WHEN 'suspended' THEN 1 ELSE 2 END)
                                                                          AS status_rank,
                max(approved_on)                                          AS approved_on,
                max(valid_until)                                          AS valid_until,
                any_value(uf)                                             AS approval_uf,
                any_value(municipality)                                   AS approval_city,
                any_value(plant_name)                                     AS approval_name
            FROM matching
            GROUP BY sif
        ),
        reach AS (
            -- MAPA's lists, not countries: the EU's one list must not count 27+ times
            SELECT sif, count(DISTINCT sigsif_country) AS markets
            FROM staging.sigsif_approvals
            WHERE status = 'active' AND len(list_intersect(categories, ?)) > 0
            GROUP BY sif
        ),
        seen AS (
            SELECT sif, min(first_seen) AS first_seen
            FROM staging.sigsif_approval_spans
            WHERE in_latest
            GROUP BY sif
        )
        SELECT
            pp.sif,
            coalesce(p.trade_name, p.legal_name, pp.approval_name)       AS name,
            p.legal_name,
            p.cnpj,
            p.plant_type,
            coalesce(p.municipality, pp.approval_city)                    AS municipality,
            coalesce(p.uf, pp.approval_uf)                                AS uf,
            p.street, p.district, p.postcode,
            pp.products,
            CASE pp.status_rank WHEN 0 THEN 'active' WHEN 1 THEN 'suspended'
                ELSE 'expired' END                                       AS status,
            pp.approved_on,
            pp.valid_until,
            coalesce(r.markets, 0)                                        AS markets,
            s.first_seen
        FROM per_plant pp
        LEFT JOIN staging.sigsif_plants p ON p.sif = pp.sif
        LEFT JOIN reach r ON r.sif = pp.sif
        LEFT JOIN seen s ON s.sif = pp.sif
    """


def approved_plants(
    con: duckdb.DuckDBPyConnection,
    *,
    country: str,
    categories: list[str],
    origin: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Plants on MAPA's list for this market, for any of these categories.

    One row per plant: sif, name, legal_name, cnpj, plant_type,
    municipality, uf, street/district/postcode, products (the approved
    product lines that match, as MAPA names them) and approved_for (the
    same in plain English), status (best of its approvals: active >
    suspended > expired), approved_on, valid_until, markets (how many
    of MAPA's market lists carry it for these categories - export
    experience; a bloc's list counts once),
    first_seen (when our snapshots first saw it), state_share (share of
    this flow shipped from the plant's state - 0 when none), search_url,
    map_url. Active first, then by state_share, then by markets.
    """
    df = con.execute(_plants_sql("AND a.country = ?"), [categories, country, categories]).df()
    return _finish(df, origin)


def experienced_exporters(
    con: duckdb.DuckDBPyConnection,
    *,
    categories: list[str],
    origin: pd.DataFrame | None = None,
    min_markets: int = 1,
) -> pd.DataFrame:
    """For a market with no per-plant list: plants approved for these
    categories by at least `min_markets` listing countries - proven
    exporters, located where the flow ships from first. Same columns as
    approved_plants()."""
    df = con.execute(_plants_sql("AND a.status = 'active'"), [categories, categories]).df()
    return _finish(df[df["markets"] >= min_markets].reset_index(drop=True), origin)


def product_kind(product: str) -> str:
    """A MAPA product line in plain English ("Raw beef & offal")."""
    for pattern, label in PRODUCT_KINDS:
        if re.search(pattern, product.upper()):
            return label
    return product.capitalize()


def _kinds(products: str) -> str:
    return ", ".join(dict.fromkeys(product_kind(p) for p in products.split("; ")))


def _finish(df: pd.DataFrame, origin: pd.DataFrame | None) -> pd.DataFrame:
    shares = (
        {}
        if origin is None or origin.empty
        else dict(zip(origin["uf"], origin["share"], strict=True))
    )
    df["state_share"] = df["uf"].map(shares).fillna(0.0)
    df["approved_for"] = [_kinds(p) for p in df["products"]]
    df["search_url"] = [
        search_url(n, legal, m, uf)
        for n, legal, m, uf in zip(
            df["name"], df["legal_name"], df["municipality"], df["uf"], strict=True
        )
    ]
    df["map_url"] = [
        map_url(street, district, m, uf, cep)
        for street, district, m, uf, cep in zip(
            df["street"], df["district"], df["municipality"], df["uf"], df["postcode"], strict=True
        )
    ]
    df["_status"] = df["status"].map(_STATUS_ORDER)
    df = df.sort_values(
        ["_status", "state_share", "markets", "name"], ascending=[True, False, False, True]
    )
    return df.drop(columns="_status").reset_index(drop=True)


def _clean(*parts: object) -> list[str]:
    return [str(p).strip() for p in parts if isinstance(p, str) and p.strip()]


def search_url(
    name: str | None, legal_name: str | None, municipality: str | None, uf: str | None
) -> str:
    """A web search for the company in its town - the quickest way to its
    website and contacts, which the open data deliberately doesn't carry."""
    company = legal_name if isinstance(legal_name, str) and legal_name.strip() else name
    query = " ".join([f'"{company}"', *_clean(municipality, uf)]) if company else ""
    return f"https://www.google.com/search?q={quote_plus(query)}" if query else ""


def map_url(
    street: str | None,
    district: str | None,
    municipality: str | None,
    uf: str | None,
    postcode: str | None,
) -> str:
    """The plant's registered address on a map (empty if there's none)."""
    parts = _clean(street, district, municipality, uf, postcode)
    if not _clean(street):
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(', '.join(parts))}"


def recent_changes(
    con: duckdb.DuckDBPyConnection, *, country: str, categories: list[str]
) -> pd.DataFrame:
    """Approvals for this market that appeared or disappeared in the latest
    SIGSIF snapshot vs the one before (empty until there are two):
    sif, plant_name, product, change ('added' / 'removed'), seen_on."""
    snapshots = con.execute(
        "SELECT fetched_at::DATE FROM raw.sigsif_snapshots ORDER BY snapshot_seq DESC LIMIT 2"
    ).fetchall()
    if len(snapshots) < 2:
        return pd.DataFrame(columns=["sif", "plant_name", "product", "change", "seen_on"])
    latest, previous = snapshots[0][0], snapshots[1][0]
    return (
        con.execute(
            """
        WITH m AS (SELECT DISTINCT sigsif_country FROM staging.sigsif_approvals WHERE country = ?),
        spans AS (
            SELECT s.*, a.plant_name
            FROM staging.sigsif_approval_spans s
            JOIN m USING (sigsif_country)
            LEFT JOIN (SELECT DISTINCT sif, plant_name FROM staging.sigsif_approvals) a USING (sif)
        )
        SELECT sif, plant_name, product,
               CASE WHEN in_latest THEN 'added' ELSE 'removed' END AS change,
               CASE WHEN in_latest THEN first_seen ELSE last_seen END AS seen_on
        FROM spans
        WHERE (in_latest AND first_seen = ? AND first_seen > (SELECT min(first_seen) FROM spans))
            OR (NOT in_latest AND last_seen = ?)
        ORDER BY change, plant_name
        """,
            [country, latest, previous],
        )
        .df()
        .pipe(lambda df: df[[_matches(p, categories) for p in df["product"]]] if len(df) else df)
    )


def _matches(product: str, categories: list[str]) -> bool:
    """Whether a SIGSIF product line maps to any of these categories."""
    for pattern, mapped in PRODUCT_CATEGORY_RULES:
        if re.search(pattern, product.upper()):
            return bool(set(mapped) & set(categories))
    return False
