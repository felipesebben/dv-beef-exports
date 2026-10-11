"""Tests for the SIGSIF export-approval snapshots (ingestion/sigsif.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dv_beef_exports.ingestion import sigsif
from dv_beef_exports.ingestion.duckdb_loader import get_connection

RAW = "PRODUTOS EM NATUREZA (BOVÍDEO) (DHC)"
CURED = "PRODUTOS COM ADIÇÃO DE INIBIDORES (BOVÍDEO) (DHC)"
CHICKEN = "PRODUTOS EM NATUREZA (AVES) (DHC)"


def approval(country: str, sif: str, product: str = RAW, **dates: str) -> dict[str, str]:
    return {
        "PAIS": country,
        "AREA": "CARNE",
        "ESTABELECIMENTO": f"PLANT {sif}",
        "SIF": sif,
        "UF": "MT",
        "MUNICIPIO": "CUIABÁ",
        "PRODUTO": product,
        "DT_VALIDADE": dates.get("valid", ""),
        "DT_OCORRENCIA": dates.get("approved", "2026-01-10 00:00:00"),
        "DT_SUSPENSAO": dates.get("suspended", ""),
    }


def registry(sif: str, doc: str = "12.345.678/0001-90", **extra: str) -> dict[str, str]:
    return {
        "CPF_CNPJ": doc,
        "RAZAO_SOCIAL": f"COMPANY {sif} LTDA",
        "NOME_FANTASIA": extra.get("trade", f"BRAND {sif}"),
        "NR_SIF": sif,
        "SITUACAO": "A",
        "LOGRADOURO": "RODOVIA BR 163, KM 10",
        "BAIRRO": "ZONA RURAL",
        "CEP": "78000000",
        "MUNICIPIO": "CUIABÁ",
        "UF": "MT",
        "TELEFONE": "(65) 3333-3333",
        "EMAIL": "owner@example.com",
        "CATEGORIA_CLASSE": extra.get("cls", "ABATEDOURO FRIGORÍFICO - C15 / AB2 - BOVINO"),
        "DATA_OCORRENCIA": extra.get("event", ""),
        "DESCRICAO_OCORRENCIA": extra.get("event", ""),
    }


@pytest.fixture
def con(tmp_path: Path):
    connection = get_connection(tmp_path / "test.duckdb")
    # the ComexStat dimensions the country mapping joins against
    connection.executemany(
        "INSERT INTO staging.dim_country VALUES (?, ?)",
        [("493", "Mexico"), ("160", "China"), ("023", "Germany"), ("275", "France")],
    )
    connection.execute("INSERT INTO staging.dim_economic_bloc VALUES ('22', 'European Union (EU)')")
    connection.executemany(
        "INSERT INTO staging.bridge_country_bloc VALUES (?, '22')", [("023",), ("275",)]
    )
    sigsif.refresh_country_names_pt(
        connection, [{"id": "493", "text": "México"}, {"id": "105", "text": "Brasil"}]
    )
    yield connection
    connection.close()


def test_beef_approvals_keeps_only_bovine_rows() -> None:
    rows = [approval("MÉXICO", "1"), approval("MÉXICO", "2", CHICKEN)]

    kept = sigsif.beef_approvals(rows)

    assert [r["SIF"] for r in kept] == ["1"]


def test_beef_approvals_rejects_a_reshaped_file() -> None:
    with pytest.raises(sigsif.SigsifError, match="missing columns"):
        sigsif.beef_approvals([{"PAIS": "MÉXICO", "PRODUTO": RAW}])


def test_beef_plants_drops_contacts_and_dedupes_event_rows() -> None:
    rows = [registry("1", event="2025"), registry("1", event="2026"), registry("9")]

    plants = sigsif.beef_plants(rows, {"1"})

    assert len(plants) == 1  # same plant twice, differing only in event columns
    assert "TELEFONE" not in plants[0] and "EMAIL" not in plants[0]
    assert plants[0]["CNPJ"] == "12.345.678/0001-90"
    assert plants[0]["RAZAO_SOCIAL"] == "COMPANY 1 LTDA"


def test_beef_plants_keeps_no_personal_data_for_a_cpf_registrant() -> None:
    plants = sigsif.beef_plants([registry("1", doc="123.456.789-00")], {"1"})

    plant = plants[0]
    assert plant["CNPJ"] is None
    assert plant["RAZAO_SOCIAL"] is None and plant["LOGRADOURO"] is None
    assert plant["MUNICIPIO"] == "CUIABÁ"  # a town is not personal data


def test_content_hash_ignores_row_order() -> None:
    a, b = approval("MÉXICO", "1"), approval("MÉXICO", "2")

    assert sigsif.content_hash([a, b], []) == sigsif.content_hash([b, a], [])
    assert sigsif.content_hash([a], []) != sigsif.content_hash([b], [])


def test_ingest_snapshot_skips_an_unchanged_snapshot(con) -> None:
    approvals = [approval("MÉXICO", "1")]

    first = sigsif.ingest_snapshot(con, approvals, [])
    second = sigsif.ingest_snapshot(con, approvals, [])
    third = sigsif.ingest_snapshot(con, [*approvals, approval("MÉXICO", "2")], [])

    assert first is not None and second is None and third is not None
    assert con.execute("SELECT count(*) FROM raw.sigsif_snapshots").fetchone()[0] == 2


def test_build_staging_maps_countries_by_name_override_and_bloc(con) -> None:
    sigsif.ingest_snapshot(
        con,
        [
            approval("MÉXICO", "1"),  # matched on ComexStat's Portuguese name
            approval("CHINA, REPUBLICA POPULAR", "1"),  # override
            approval("UNIÃO EUROPEIA (UE)", "1"),  # bloc -> its members
            approval("BRASIL", "1"),  # the domestic list - dropped
            approval("ATLÂNTIDA", "1"),  # unknown - kept, flagged
        ],
        [],
    )

    sigsif.build_staging(con)

    countries = con.execute(
        "SELECT sigsif_country, country, via_bloc FROM staging.sigsif_approvals ORDER BY ALL"
    ).fetchall()
    assert countries == [
        ("ATLÂNTIDA", None, None),
        ("CHINA, REPUBLICA POPULAR", "China", None),
        ("MÉXICO", "Mexico", None),
        ("UNIÃO EUROPEIA (UE)", "France", "European Union (EU)"),
        ("UNIÃO EUROPEIA (UE)", "Germany", "European Union (EU)"),
    ]
    assert sigsif.unmapped_countries(con) == ["ATLÂNTIDA"]


@pytest.mark.parametrize(
    ("product", "categories"),
    [
        (RAW, ["frozen", "offal"]),
        (CURED, ["salted_dried"]),
        ("PRODUTOS SUBMETIDOS A TRATAMENTO TÉRMICO COCÇÃO (BOVÍDEO) (DHC)", ["processed"]),
        ("MIÚDOS DE BOVINO ''IN NATURA''", ["offal"]),
        ("MIÚDOS COZIDOS CONGELADOS DE BOVINO", ["processed"]),
        ('CARNE DE BOVINO "IN NATURA"', ["frozen"]),
        ("COTA HILTON CARNE BOVINA", ["frozen"]),
        ("CARNE RESFRIADA DE BOVINO SEM OSSO", []),  # chilled: out of scope
        ("PRODUTOS NÃO SUBMETIDOS A TRATAMENTO TÉRMICO (BOVÍDEO) (DHC)", []),
        ("SEBO BOVINO", []),
    ],
)
def test_products_map_to_our_categories(con, product: str, categories: list[str]) -> None:
    sigsif.ingest_snapshot(con, [approval("MÉXICO", "1", product)], [])

    sigsif.build_staging(con)

    assert con.execute("SELECT categories FROM staging.sigsif_approvals").fetchone()[0] == (
        categories
    )


def test_status_reads_suspensions_reinstatements_and_expiry(con) -> None:
    fetched = datetime(2026, 10, 11, tzinfo=UTC)
    sigsif.ingest_snapshot(
        con,
        [
            approval("MÉXICO", "1"),
            approval("MÉXICO", "2", suspended="2026-03-01 00:00:00"),
            # suspended, then approved again later: reinstated
            approval("MÉXICO", "3", suspended="2025-03-01 00:00:00"),
            approval("MÉXICO", "4", valid="2026-01-31 00:00:00"),
        ],
        [],
        fetched,
    )

    sigsif.build_staging(con)

    statuses = dict(con.execute("SELECT sif, status FROM staging.sigsif_approvals").fetchall())
    assert statuses == {"1": "active", "2": "suspended", "3": "active", "4": "expired"}


def test_build_staging_describes_each_plant_once(con) -> None:
    plants = sigsif.beef_plants(
        [
            registry("1", trade="-", cls="ABATEDOURO FRIGORÍFICO - C15"),
            registry("1", trade="-", cls="UNIDADE DE BENEF. DE CARNE - C16"),
        ],
        {"1"},
    )
    sigsif.ingest_snapshot(con, [approval("MÉXICO", "1")], plants)

    sigsif.build_staging(con)

    row = con.execute(
        "SELECT sif, trade_name, plant_type, plant_classes FROM staging.sigsif_plants"
    ).fetchall()
    assert row == [
        (
            "1",
            None,  # "-" is ComexStat's way of saying "no trade name"
            "Slaughterhouse",
            "ABATEDOURO FRIGORÍFICO - C15; UNIDADE DE BENEF. DE CARNE - C16",
        )
    ]


def test_refresh_reports_added_and_removed_approvals(con) -> None:
    files = {
        sigsif.APPROVALS_URL: [approval("MÉXICO", "1"), approval("MÉXICO", "2")],
        sigsif.REGISTRY_URL: [registry("1"), registry("2")],
    }

    def fetch(url: str) -> list[dict[str, str]]:
        return files[url]

    def countries_pt(language: str) -> list[dict]:
        return [{"id": "493", "text": "México"}]

    first = sigsif.refresh(con, fetch, countries_pt)
    files[sigsif.APPROVALS_URL] = [approval("MÉXICO", "2"), approval("MÉXICO", "3")]
    files[sigsif.REGISTRY_URL].append(registry("3"))
    second = sigsif.refresh(con, fetch, countries_pt)
    third = sigsif.refresh(con, fetch, countries_pt)

    assert first.changed and first.added == []  # nothing to compare the first one with
    assert second.changed
    assert second.added == [("MÉXICO", "3", RAW)]
    assert second.removed == [("MÉXICO", "1", RAW)]
    assert not third.changed
    spans = dict(
        con.execute(
            "SELECT sif, in_latest FROM staging.sigsif_approval_spans ORDER BY sif"
        ).fetchall()
    )
    assert spans == {"1": False, "2": True, "3": True}


def test_fetch_csv_reads_the_portal_format(requests_mock) -> None:
    body = "﻿PAIS;SIF\nMÉXICO;1\n".encode()
    requests_mock.get(sigsif.APPROVALS_URL, content=body)

    assert sigsif.fetch_csv(sigsif.APPROVALS_URL) == [{"PAIS": "MÉXICO", "SIF": "1"}]
    assert requests_mock.last_request.headers["User-Agent"] == sigsif.USER_AGENT


def test_fetch_csv_fails_fast_on_a_missing_file(requests_mock) -> None:
    requests_mock.get(sigsif.APPROVALS_URL, status_code=404)

    with pytest.raises(sigsif.SigsifError, match="404"):
        sigsif.fetch_csv(sigsif.APPROVALS_URL)
    assert requests_mock.call_count == 1


def test_fetch_csv_retries_a_server_error(requests_mock, monkeypatch) -> None:
    monkeypatch.setattr(sigsif.fetch_csv.retry, "sleep", lambda _: None)
    requests_mock.get(
        sigsif.APPROVALS_URL,
        [{"status_code": 503}, {"content": b"PAIS;SIF\nCHILE;2\n"}],
    )

    assert sigsif.fetch_csv(sigsif.APPROVALS_URL) == [{"PAIS": "CHILE", "SIF": "2"}]
