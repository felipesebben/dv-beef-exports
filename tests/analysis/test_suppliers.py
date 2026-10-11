"""Tests for where-to-source queries (analysis/suppliers.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dv_beef_exports.analysis import suppliers
from dv_beef_exports.ingestion import sigsif
from dv_beef_exports.ingestion.duckdb_loader import get_connection
from dv_beef_exports.ingestion.exports_by_state import build_by_state, ingest_by_state
from tests.ingestion.test_sigsif import CURED, RAW, approval, registry

BONELESS, LIVERS = "02023000", "02062200"


def by_state(
    country: str, state: str, fob: int, code: str = BONELESS, month: int = 9, year: int = 2026
) -> dict:
    return {
        "coNcm": code,
        "ncm": "x",
        "country": country,
        "state": state,
        "year": str(year),
        "monthNumber": f"{month:02d}",
        "metricFOB": str(fob),
        "metricKG": "1000",
    }


def plant_approval(country: str, sif: str, uf: str, product: str = RAW, **dates: str) -> dict:
    return {**approval(country, sif, product, **dates), "UF": uf}


@pytest.fixture
def con(tmp_path: Path):
    connection = get_connection(tmp_path / "test.duckdb")
    connection.executemany(
        "INSERT INTO staging.dim_country VALUES (?, ?)",
        [("493", "Mexico"), ("023", "Germany"), ("275", "France"), ("438", "Libya")],
    )
    connection.execute("INSERT INTO staging.dim_economic_bloc VALUES ('22', 'European Union (EU)')")
    connection.executemany(
        "INSERT INTO staging.bridge_country_bloc VALUES (?, '22')", [("023",), ("275",)]
    )
    sigsif.refresh_country_names_pt(connection, [{"id": "493", "text": "México"}])

    approvals = [
        plant_approval("MÉXICO", "10", "GO"),
        plant_approval("MÉXICO", "20", "MT"),
        plant_approval("MÉXICO", "30", "PA", suspended="2026-05-01 00:00:00"),
        plant_approval("MÉXICO", "40", "GO", CURED),  # salted: not a boneless supplier
        plant_approval("UNIÃO EUROPEIA (UE)", "20", "MT"),  # one list, two countries
        plant_approval("CHILE", "20", "MT"),
    ]
    plants = sigsif.beef_plants(
        [registry("10"), registry("20"), registry("30"), registry("40")],
        {"10", "20", "30", "40"},
    )
    sigsif.ingest_snapshot(connection, approvals, plants, datetime(2026, 10, 1, tzinfo=UTC))
    sigsif.build_staging(connection)

    ingest_by_state(
        connection,
        [
            by_state("Mexico", "Goiás", 300),
            by_state("Mexico", "Mato Grosso", 100),
            by_state("Mexico", "Mato Grosso", 999, month=1),  # Jan 2026: inside the window
            by_state("Mexico", "Pará", 50, code=LIVERS),
        ],
        [BONELESS, LIVERS],
        "2026-01",
        "2026-09",
    )
    ingest_by_state(
        connection, [by_state("Mexico", "Goiás", 777, year=2024)], [BONELESS], "2024-09", "2024-09"
    )
    build_by_state(connection)
    yield connection
    connection.close()


def test_categories_for_each_product_level() -> None:
    assert suppliers.categories_for("ncm_code", LIVERS) == ["offal"]
    assert suppliers.categories_for("category", "processed") == ["processed"]
    assert suppliers.categories_for("overall", None) == [
        "frozen",
        "offal",
        "processed",
        "salted_dried",
    ]


def test_origin_states_shares_the_latest_twelve_months(con) -> None:
    states = suppliers.origin_states(
        con, country="Mexico", product_level="ncm_code", product_value=BONELESS
    )

    # Sep 2024 is outside the 12 months to Sep 2026; livers aren't boneless
    assert list(states["uf"]) == ["MT", "GO"]
    assert list(states["fob_usd"]) == [1099, 300]
    assert states["share"].sum() == pytest.approx(1.0)
    assert str(states["period_start"].iloc[0])[:10] == "2025-10-01"


def test_origin_states_is_empty_for_a_flow_that_never_shipped(con) -> None:
    assert suppliers.origin_states(con, country="Libya").empty


def test_market_listing_tells_listed_from_unlisted_markets(con) -> None:
    assert suppliers.market_listing(con, "Mexico")["listed"]
    assert suppliers.market_listing(con, "Germany")["via_bloc"] == "European Union (EU)"
    # Libya keeps no per-plant list: not listed, which is NOT "closed"
    assert not suppliers.market_listing(con, "Libya")["listed"]


def test_approved_plants_for_a_market_and_product(con) -> None:
    origin = suppliers.origin_states(
        con, country="Mexico", product_level="ncm_code", product_value=BONELESS
    )

    plants = suppliers.approved_plants(con, country="Mexico", categories=["frozen"], origin=origin)

    # active first, by how much of the flow its state ships; salted-only 40 excluded
    assert list(plants["sif"]) == ["20", "10", "30"]
    assert list(plants["status"]) == ["active", "active", "suspended"]
    assert plants.loc[0, "state_share"] == pytest.approx(1099 / 1399)
    # MÉXICO, the EU list (two countries) and CHILE - the EU counts once
    assert plants.loc[0, "markets"] == 3
    assert plants.loc[0, "name"] == "BRAND 20"
    assert plants.loc[0, "cnpj"] == "12.345.678/0001-90"
    assert plants.loc[0, "search_url"].startswith("https://www.google.com/search?q=")
    assert "COMPANY+20+LTDA" in plants.loc[0, "search_url"]
    assert plants.loc[0, "map_url"].startswith("https://www.google.com/maps/search/")


def test_experienced_exporters_for_an_unlisted_market(con) -> None:
    plants = suppliers.experienced_exporters(con, categories=["frozen"], min_markets=2)

    assert list(plants["sif"]) == ["20"]


def test_search_url_and_map_url_handle_missing_parts() -> None:
    assert suppliers.search_url(None, None, "CUIABÁ", "MT") == ""
    assert suppliers.search_url("BRAND", None, "CUIABÁ", "MT") == (
        "https://www.google.com/search?q=%22BRAND%22+CUIAB%C3%81+MT"
    )
    assert suppliers.map_url(None, None, "CUIABÁ", "MT", None) == ""


def test_recent_changes_compares_the_last_two_snapshots(con) -> None:
    assert suppliers.recent_changes(con, country="Mexico", categories=["frozen"]).empty

    sigsif.ingest_snapshot(
        con,
        [
            plant_approval("MÉXICO", "10", "GO"),
            plant_approval("MÉXICO", "50", "MS"),  # new
            plant_approval("MÉXICO", "40", "GO", CURED),  # unchanged, other category
        ],
        [],
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    sigsif.build_staging(con)

    changes = suppliers.recent_changes(con, country="Mexico", categories=["frozen"])

    assert sorted(zip(changes["sif"], changes["change"], strict=True)) == [
        ("20", "removed"),
        ("30", "removed"),
        ("50", "added"),
    ]


def test_product_kinds_read_in_plain_english() -> None:
    assert suppliers.product_kind(RAW) == "Raw beef & offal"
    assert suppliers.product_kind(CURED) == "Cured / salted"
    assert suppliers.product_kind("PRODUTOS SUBMETIDOS A TRATAMENTO TÉRMICO COCÇÃO (BOVÍDEO)") == (
        "Cooked"
    )
    assert suppliers.product_kind("MIÚDOS COZIDOS CONGELADOS DE BOVINO") == "Cooked"
    assert suppliers.product_kind("COTA HILTON CARNE BOVINA") == "Hilton quota beef"
