"""Tests for the DuckDB medallion loader (raw / staging / marts)."""

from pathlib import Path

import duckdb

from dv_beef_exports.ingestion import duckdb_loader
from dv_beef_exports.ingestion.duckdb_loader import (
    build_marts,
    build_staging,
    get_connection,
    ingest_raw,
    refresh_dim_country,
    refresh_ncm_hierarchy,
)

CHINA_FEB_2024 = {
    "coNcm": "02023000",
    "ncm": "Frozen bovine meat, boneless",
    "country": "China",
    "year": "2024",
    "monthNumber": "02",
    "metricFOB": "428582214",
    "metricKG": "96055461",
}


def _connect(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    return get_connection(tmp_path / "test.duckdb")


def test_get_connection_creates_schemas_and_raw_table(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    con = get_connection(db_path)

    assert db_path.exists()
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (0,)
    con.close()


def test_get_connection_seeds_dim_ncm_from_ncm_codes(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    count = con.execute("SELECT count(*) FROM staging.dim_ncm").fetchone()[0]
    boneless = con.execute(
        "SELECT description_en, category FROM staging.dim_ncm WHERE ncm_code = '02023000'"
    ).fetchone()

    assert count == 11
    assert boneless == ("Boneless beef, frozen", "frozen")
    con.close()


def test_ingest_raw_appends_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    assert inserted == 1
    row = con.execute(
        "SELECT co_ncm, country, year, month_number, metric_fob, metric_kg FROM raw.exports"
    ).fetchone()
    assert row == ("02023000", "China", "2024", "02", "428582214", "96055461")
    con.close()


def test_ingest_raw_records_what_was_requested(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    ingest_raw(
        con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03", details=["country", "ncm"]
    )

    row = con.execute(
        "SELECT requested_ncm_codes, requested_period_from, requested_period_to, "
        "requested_details FROM raw.exports"
    ).fetchone()
    assert row == (["02023000"], "2024-01", "2024-03", ["country", "ncm"])
    con.close()


def test_ingest_raw_does_not_overwrite_previous_pulls(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    # append-only: re-pulling the same period adds a second raw row, it
    # doesn't replace the first (see docs/decisions/0004)
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (2,)
    con.close()


def test_ingest_raw_handles_empty_rows(tmp_path: Path) -> None:
    con = _connect(tmp_path)

    inserted = ingest_raw(con, [], ["02023000"], "2024-01", "2024-03")

    assert inserted == 0
    assert con.execute("SELECT count(*) FROM raw.exports").fetchone() == (0,)
    con.close()


def test_build_staging_dedupes_to_the_most_recent_pull(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    # two conflicting pulls for the same key, inserted directly so fetched_at
    # ordering is controlled rather than relying on real clock timing
    con.execute(
        """
        INSERT INTO raw.exports VALUES
        ('11111111-1111-1111-1111-111111111111', '2024-04-01 00:00:00+00',
         ['02023000'], '2024-02', '2024-02', ['country', 'ncm'],
         '02023000', 'Frozen bovine meat, boneless', 'China',
         '2024', '02', '100000000', '20000000'),
        ('22222222-2222-2222-2222-222222222222', '2024-05-01 00:00:00+00',
         ['02023000'], '2024-02', '2024-02', ['country', 'ncm'],
         '02023000', 'Frozen bovine meat, boneless', 'China',
         '2024', '02', '428582214', '96055461')
        """
    )

    build_staging(con)

    row = con.execute(
        "SELECT fob_usd, kg FROM staging.exports WHERE ncm_code = '02023000' AND country = 'China'"
    ).fetchone()
    assert row == (428582214, 96055461)  # the later (2024-05) pull wins
    con.close()


def test_build_staging_types_and_joins_dim_ncm(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")

    build_staging(con)

    row = con.execute(
        "SELECT ncm_code, ncm_name, category, country, year, month, fob_usd, kg "
        "FROM staging.exports"
    ).fetchone()
    assert row == (
        "02023000",
        "Boneless beef, frozen",
        "frozen",
        "China",
        2024,
        2,
        428582214,
        96055461,
    )
    con.close()


def test_build_marts_converts_kg_to_metric_ton(tmp_path: Path) -> None:
    con = _connect(tmp_path)
    ingest_raw(con, [CHINA_FEB_2024], ["02023000"], "2024-01", "2024-03")
    build_staging(con)

    build_marts(con)

    row = con.execute("SELECT kg, metric_ton FROM marts.exports").fetchone()
    assert row == (96055461, 96055461 / 1000.0)
    con.close()


def test_get_connection_reseed_preserves_hierarchy_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    con = get_connection(db_path)
    con.execute(
        "UPDATE staging.dim_ncm SET sh6_code = '020230', unit = 'KILOGRAM' "
        "WHERE ncm_code = '02023000'"
    )
    con.close()

    # simulate a later, fresh connection (e.g. a new script run) — its
    # dim_ncm upsert must not wipe out sh6_code/unit set by a previous
    # refresh_ncm_hierarchy() call
    con2 = get_connection(db_path)
    row = con2.execute(
        "SELECT sh6_code, unit FROM staging.dim_ncm WHERE ncm_code = '02023000'"
    ).fetchone()
    assert row == ("020230", "KILOGRAM")
    con2.close()


def test_refresh_ncm_hierarchy_dedupes_by_sh6_and_updates_dim_ncm(
    tmp_path: Path, monkeypatch
) -> None:
    con = _connect(tmp_path)
    fake_hierarchy = {
        "02022010": {
            "unit": "KILOGRAM",
            "subHeadingCode": "020220",
            "subHeading": "Other cuts with bone in",
            "headingCode": "0202",
            "heading": "Meat of bovine animals, frozen",
            "chapterCode": "02",
            "chapter": "Meat and edible meat offal",
        },
        "02022020": {
            "unit": "KILOGRAM",
            "subHeadingCode": "020220",
            "subHeading": "Other cuts with bone in",
            "headingCode": "0202",
            "heading": "Meat of bovine animals, frozen",
            "chapterCode": "02",
            "chapter": "Meat and edible meat offal",
        },
        "02023000": {
            "unit": "KILOGRAM",
            "subHeadingCode": "020230",
            "subHeading": "Frozen, boneless meat of bovine animals",
            "headingCode": "0202",
            "heading": "Meat of bovine animals, frozen",
            "chapterCode": "02",
            "chapter": "Meat and edible meat offal",
        },
    }
    monkeypatch.setattr(
        duckdb_loader,
        "fetch_ncm_hierarchy",
        lambda code, language="en": fake_hierarchy.get(code),
    )

    sh6_count = refresh_ncm_hierarchy(con)

    # two of the three faked codes share sh6 "020220" -> deduped to 2 groups
    assert sh6_count == 2
    hierarchy_row = con.execute(
        "SELECT sh6_name, chapter_code FROM staging.dim_ncm_hierarchy WHERE sh6_code = '020220'"
    ).fetchone()
    assert hierarchy_row == ("Other cuts with bone in", "02")

    dim_ncm_row = con.execute(
        "SELECT sh6_code, unit FROM staging.dim_ncm WHERE ncm_code = '02022010'"
    ).fetchone()
    assert dim_ncm_row == ("020220", "KILOGRAM")

    # a tracked code with no fake data (e.g. an offal code) is left null,
    # not an error — fetch_ncm_hierarchy() returning None is expected
    unmapped_row = con.execute(
        "SELECT sh6_code, unit FROM staging.dim_ncm WHERE ncm_code = '02062100'"
    ).fetchone()
    assert unmapped_row == (None, None)
    con.close()


def test_refresh_dim_country_populates_all_three_tables(tmp_path: Path, monkeypatch) -> None:
    con = _connect(tmp_path)
    monkeypatch.setattr(
        duckdb_loader,
        "fetch_countries",
        lambda language="en": [
            {"id": "105", "text": "Brazil"},
            {"id": "160", "text": "\nChina"},
        ],
    )
    monkeypatch.setattr(
        duckdb_loader,
        "fetch_economic_blocks",
        lambda language="en": [
            {"id": "48", "text": "South America"},
            {"id": "111", "text": "Southern Common Market (MERCOSUL)"},
        ],
    )
    monkeypatch.setattr(
        duckdb_loader,
        "fetch_country_blocs",
        lambda language="en": [
            {
                "coCountry": "063",
                "economicBlock": "South America",
                "coBlock": "48",
                "country": "Argentina",
            },
            {
                "coCountry": "063",
                "economicBlock": "Southern Common Market (MERCOSUL)",
                "coBlock": "111",
                "country": "Argentina",
            },
        ],
    )

    refresh_dim_country(con)

    china_name = con.execute(
        "SELECT name FROM staging.dim_country WHERE co_pais = '160'"
    ).fetchone()[0]
    assert china_name == "China"  # leading whitespace stripped

    bloc_count = con.execute(
        "SELECT count(*) FROM staging.bridge_country_bloc WHERE co_pais = '063'"
    ).fetchone()[0]
    assert bloc_count == 2  # Argentina: in both a region and a trade bloc
    con.close()
