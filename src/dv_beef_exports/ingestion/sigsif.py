"""
SIGSIF: which Brazilian plants (SIF establishments) are approved to export
which products to which countries - MAPA's own lists (ADR 0007).

Source: MAPA's open-data portal, dataset "Plataforma de Gestão Agropecuária -
PGA - SIGSIF" (CC-BY, monthly). Two of its CSVs, downloaded directly - never
the legacy sigsif.agricultura.gov.br site (its robots.txt disallows every
automated client) and never the portal's /api/ path (disallowed too):

- "Estabelecimentos Nacionais Habilitados": one row per country x plant x
  product approval;
- "Estabelecimentos Registrados no SIF": the plant registry (CNPJ, names,
  address), used to describe the plants.

Each file is a current-state list with no history, so every refresh stores a
snapshot - but only when the beef content actually changed (a content hash),
so the tracked database grows with real changes, not with each run.

Kept narrow on purpose:
- only bovine approvals (the project is beef), and only registry rows for
  plants holding one;
- never the registry's phone/e-mail columns, and no name or address for a
  registrant identified by a CPF (a person, not a company) - the repo is
  public.

Layers: raw.sigsif_snapshots / raw.sigsif_approvals / raw.sigsif_plants
(append-only, VARCHAR), rebuilt into staging.sigsif_approvals (latest
snapshot, mapped to ComexStat countries and our product categories, with a
status) and staging.sigsif_plants, plus staging.sigsif_approval_spans (when
each approval was first and last seen across snapshots).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import duckdb
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_DATASET = "https://dados.agricultura.gov.br/dataset/062166e3-b515-4274-8e7d-68aadd64b820"
APPROVALS_URL = (
    f"{_DATASET}/resource/fcb7f87d-0092-4a52-a44b-b3550747b4c2/download/"
    "sigsifestabelecimentosnacionais.csv"
)
REGISTRY_URL = (
    f"{_DATASET}/resource/97277e92-264a-4dc0-9aea-f87b8ea93798/download/"
    "sigsifestabelecimentosregistradosnosif.csv"
)
USER_AGENT = "dv-beef-exports/0.1 (+https://github.com/felipesebben/dv-beef-exports)"
TIMEOUT_SECONDS = 180

BOVINE_PATTERN = re.compile(r"BOV", re.IGNORECASE)

# SIGSIF approves *processing types*, not tariff codes, so this is the
# closest honest mapping onto ncm_codes.py's categories (ADR 0007). First
# match wins; [] = a bovine product outside our 11 codes. "Em natureza"
# (raw) covers meat and offal alike, and frozen and chilled alike - chilled
# isn't tracked here, so a raw-products approval counts for frozen + offal.
PRODUCT_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (r"N[ÃA]O SUBMETIDOS A TRATAMENTO", []),  # raw-cured/seasoned: no tracked code
    (r"HIDR[ÓO]LISE|SEBO|GELATINA|SORO|ENVOLT[ÓO]RIO|COMPOSTOS", []),
    (r"RESFRIAD", []),  # chilled only - out of scope (ADR 0002)
    (r"COZID|COC[ÇC][ÃA]O|TRATAMENTO T[ÉE]RMICO|ESTERILIZA|A BASE DE CARNE", ["processed"]),
    (r"INIBIDORES|SALGAD", ["salted_dried"]),
    (r"MI[ÚU]DOS|EST[ÔO]MAGO", ["offal"]),
    (r"CARNE.*(IN NATURA|CONGELADA)|HILTON", ["frozen"]),
    (r"EM NATUREZA|IN NATURA", ["frozen", "offal"]),
]

# SIGSIF's Portuguese country names -> ComexStat country codes, where the
# name doesn't match ComexStat's own Portuguese name (checked 2026-10-11).
# A bloc maps to its members.
COUNTRY_OVERRIDES: dict[str, list[str]] = {
    "CHINA, REPUBLICA POPULAR": ["160"],
    "CORÉIA (DO SUL), REPÚBLICA DA": ["190"],
    "GEORGIA, REPUBLICA DA": ["291"],
    "ILHAS SALOMÃO": ["677"],
    "IRÃ, REPÚBLICA ISLÂMICA DO": ["372"],
    "MOLDÁVIA, REPÚBLICA DA": ["494"],
    "RÚSSIA, FEDERAÇÃO DA": ["676"],
    # Russia, Belarus, Kazakhstan, Armenia, Kyrgyzstan
    "UNIÃO ECONÔMICA EURASIÁTICA (UEE)": ["676", "085", "153", "064", "625"],
}
# expanded through ComexStat's own bloc membership (staging.bridge_country_bloc)
BLOC_OVERRIDES = {"UNIÃO EUROPEIA (UE)": "European Union (EU)"}
# the domestic list, not an export market
EXCLUDED_COUNTRIES = ("BRASIL",)
_EXCLUDED_SQL = ", ".join(f"'{name}'" for name in EXCLUDED_COUNTRIES)

_CNPJ = re.compile(r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$")
_APPROVAL_COLUMNS = [
    "PAIS",
    "AREA",
    "ESTABELECIMENTO",
    "SIF",
    "UF",
    "MUNICIPIO",
    "PRODUTO",
    "DT_VALIDADE",
    "DT_OCORRENCIA",
    "DT_SUSPENSAO",
]
_PLANT_COLUMNS = [
    "NR_SIF",
    "CNPJ",
    "RAZAO_SOCIAL",
    "NOME_FANTASIA",
    "SITUACAO",
    "LOGRADOURO",
    "BAIRRO",
    "CEP",
    "MUNICIPIO",
    "UF",
    "CATEGORIA_CLASSE",
]


class SigsifError(Exception):
    """A download failed or the file isn't shaped as expected."""


class SigsifTransientError(SigsifError):
    """Worth retrying: network error, 429 or 5xx."""


@retry(
    retry=retry_if_exception_type(SigsifTransientError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    reraise=True,
)
def fetch_csv(url: str) -> list[dict[str, str]]:
    """Download one of the portal's CSVs (UTF-8, ';'-separated) as rows."""
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SigsifTransientError(f"Network error downloading {url}: {exc}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise SigsifTransientError(f"{response.status_code} downloading {url}")
    if response.status_code != 200:
        raise SigsifError(f"{response.status_code} downloading {url}")
    text = response.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def beef_approvals(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The bovine approvals, trimmed to the columns stored."""
    missing = set(_APPROVAL_COLUMNS) - set(rows[0]) if rows else set(_APPROVAL_COLUMNS)
    if missing:
        raise SigsifError(f"approvals file is missing columns {sorted(missing)}")
    return [
        {col: (row[col] or "").strip() for col in _APPROVAL_COLUMNS}
        for row in rows
        if BOVINE_PATTERN.search(row["PRODUTO"] or "")
    ]


def beef_plants(registry: list[dict[str, str]], sifs: set[str]) -> list[dict[str, str | None]]:
    """Registry rows for the given SIF numbers, without contact columns, and
    without name/address when the registrant is a person (CPF, not CNPJ).
    The registry repeats a plant once per logged event; with the event
    columns dropped those rows are identical, so each is kept once."""
    if registry and "NR_SIF" not in registry[0]:
        raise SigsifError("registry file has no NR_SIF column")
    plants: list[dict[str, str | None]] = []
    seen: set[tuple] = set()
    for row in registry:
        if (row.get("NR_SIF") or "").strip() not in sifs:
            continue
        doc = (row.get("CPF_CNPJ") or "").strip()
        is_company = bool(_CNPJ.match(doc))
        plant: dict[str, str | None] = {
            "NR_SIF": row["NR_SIF"].strip(),
            "CNPJ": doc if is_company else None,
        }
        for col in _PLANT_COLUMNS[2:]:
            value = (row.get(col) or "").strip() or None
            private = col in ("RAZAO_SOCIAL", "NOME_FANTASIA", "LOGRADOURO", "BAIRRO", "CEP")
            plant[col] = value if is_company or not private else None
        key = tuple(plant.values())
        if key not in seen:
            seen.add(key)
            plants.append(plant)
    return plants


def content_hash(approvals: list[dict], plants: list[dict]) -> str:
    """Order-independent fingerprint of what a snapshot would store."""
    digest = hashlib.sha256()
    for part in (approvals, plants):
        for line in sorted("\x1f".join(str(r[k] or "") for k in sorted(r)) for r in part):
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        digest.update(b"\x1e")
    return digest.hexdigest()


_CREATE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS raw.sigsif_snapshots (
        snapshot_id     UUID NOT NULL PRIMARY KEY,
        snapshot_seq    BIGINT NOT NULL UNIQUE,
        fetched_at      TIMESTAMPTZ NOT NULL,
        content_hash    VARCHAR NOT NULL,
        approval_rows   INTEGER NOT NULL,
        plant_rows      INTEGER NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS raw.sigsif_approvals (
        snapshot_id UUID NOT NULL,
        {", ".join(f"{c.lower()} VARCHAR" for c in _APPROVAL_COLUMNS)}
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS raw.sigsif_plants (
        snapshot_id UUID NOT NULL,
        {", ".join(f"{c.lower()} VARCHAR" for c in _PLANT_COLUMNS)}
    )
    """,
]


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    for sql in _CREATE_SQL:
        con.execute(sql)


@dataclass
class SigsifResult:
    """What one SIGSIF refresh found. `changed` = a new snapshot was stored."""

    changed: bool
    fetched_at: datetime
    approvals: int = 0
    plants: int = 0
    added: list[tuple[str, str, str]] = field(default_factory=list)
    removed: list[tuple[str, str, str]] = field(default_factory=list)
    unmapped_countries: list[str] = field(default_factory=list)


def ingest_snapshot(
    con: duckdb.DuckDBPyConnection,
    approvals: list[dict[str, str]],
    plants: list[dict[str, str | None]],
    fetched_at: datetime | None = None,
) -> uuid.UUID | None:
    """Store a snapshot unless it matches the latest one. Returns the new
    snapshot id, or None when nothing changed."""
    ensure_tables(con)
    fetched_at = fetched_at or datetime.now(UTC)
    fingerprint = content_hash(approvals, plants)
    latest = con.execute(
        "SELECT content_hash FROM raw.sigsif_snapshots ORDER BY snapshot_seq DESC LIMIT 1"
    ).fetchone()
    if latest and latest[0] == fingerprint:
        return None
    snapshot_id = uuid.uuid4()
    con.execute(
        "INSERT INTO raw.sigsif_snapshots "
        "SELECT ?, coalesce(max(snapshot_seq), 0) + 1, ?, ?, ?, ? FROM raw.sigsif_snapshots",
        [snapshot_id, fetched_at, fingerprint, len(approvals), len(plants)],
    )
    if approvals:
        con.executemany(
            f"INSERT INTO raw.sigsif_approvals VALUES (?{', ?' * len(_APPROVAL_COLUMNS)})",
            [[snapshot_id, *(r[c] or None for c in _APPROVAL_COLUMNS)] for r in approvals],
        )
    if plants:
        con.executemany(
            f"INSERT INTO raw.sigsif_plants VALUES (?{', ?' * len(_PLANT_COLUMNS)})",
            [[snapshot_id, *(p[c] for c in _PLANT_COLUMNS)] for p in plants],
        )
    return snapshot_id


def _category_case_sql() -> str:
    whens = " ".join(
        f"WHEN regexp_matches(upper(produto), '{pattern}') THEN {categories!r}::VARCHAR[]"
        for pattern, categories in PRODUCT_CATEGORY_RULES
    )
    return f"CASE {whens} ELSE []::VARCHAR[] END"


def _strip_accents_sql(expr: str) -> str:
    return f"upper(strip_accents(trim({expr})))"


def build_staging(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild the staging.sigsif_* tables from the latest snapshot (and the
    approval spans from all of them). Returns staging.sigsif_approvals' row
    count. Needs staging.dim_country/bridge_country_bloc for the country
    mapping - unmatched names keep a NULL co_pais (see unmapped_countries())."""
    ensure_tables(con)
    overrides = [(name, code) for name, codes in COUNTRY_OVERRIDES.items() for code in codes]
    con.execute(
        "CREATE OR REPLACE TEMP TABLE sigsif_country_override (pais VARCHAR, co_pais VARCHAR)"
    )
    if overrides:
        con.executemany("INSERT INTO sigsif_country_override VALUES (?, ?)", overrides)
    con.execute("CREATE OR REPLACE TEMP TABLE sigsif_bloc_override (pais VARCHAR, bloc VARCHAR)")
    con.executemany("INSERT INTO sigsif_bloc_override VALUES (?, ?)", list(BLOC_OVERRIDES.items()))

    # ComexStat's English names live in dim_country; its Portuguese names
    # don't, so the automatic match is on accent-stripped English-or-
    # Portuguese spellings kept in sigsif_country_pt (filled by
    # refresh_country_names_pt()), falling back to the overrides above.
    con.execute(
        "CREATE TABLE IF NOT EXISTS staging.sigsif_country_pt (co_pais VARCHAR, name_pt VARCHAR)"
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE staging.sigsif_country_map AS
        WITH names AS (
            SELECT DISTINCT pais FROM raw.sigsif_approvals WHERE pais NOT IN ({_EXCLUDED_SQL})
        ),
        overridden AS (
            SELECT n.pais, o.co_pais, NULL AS via_bloc
            FROM names n JOIN sigsif_country_override o USING (pais)
            UNION ALL
            SELECT n.pais, b.co_pais, bo.bloc
            FROM names n
            JOIN sigsif_bloc_override bo USING (pais)
            JOIN staging.dim_economic_bloc eb ON eb.name = bo.bloc
            JOIN staging.bridge_country_bloc b ON b.co_bloc = eb.co_bloc
        ),
        matched AS (
            SELECT n.pais, p.co_pais, NULL AS via_bloc
            FROM names n
            JOIN staging.sigsif_country_pt p
                ON {_strip_accents_sql("p.name_pt")} = {_strip_accents_sql("n.pais")}
            WHERE n.pais NOT IN (SELECT pais FROM sigsif_country_override)
                AND n.pais NOT IN (SELECT pais FROM sigsif_bloc_override)
        ),
        unmatched AS (
            SELECT n.pais, NULL AS co_pais, NULL AS via_bloc
            FROM names n
            WHERE n.pais NOT IN (SELECT pais FROM overridden)
                AND n.pais NOT IN (SELECT pais FROM matched)
        )
        SELECT * FROM overridden UNION ALL SELECT * FROM matched UNION ALL SELECT * FROM unmatched
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE staging.sigsif_approvals AS
        WITH latest AS (
            SELECT snapshot_id, fetched_at FROM raw.sigsif_snapshots
            ORDER BY snapshot_seq DESC LIMIT 1
        ),
        a AS (
            SELECT
                r.*,
                l.fetched_at,
                TRY_CAST(r.dt_validade AS TIMESTAMP)::DATE   AS valid_until,
                TRY_CAST(r.dt_ocorrencia AS TIMESTAMP)::DATE AS approved_on,
                TRY_CAST(r.dt_suspensao AS TIMESTAMP)::DATE  AS suspended_on
            FROM raw.sigsif_approvals r JOIN latest l USING (snapshot_id)
        )
        SELECT
            a.pais                          AS sigsif_country,
            m.co_pais,
            c.name                          AS country,
            m.via_bloc,
            a.sif,
            a.estabelecimento               AS plant_name,
            a.uf,
            a.municipio                     AS municipality,
            a.produto                       AS product,
            {_category_case_sql()}          AS categories,
            a.valid_until,
            a.approved_on,
            a.suspended_on,
            -- a suspension followed by a later approval event is a reinstatement
            CASE
                WHEN a.suspended_on IS NOT NULL
                    AND (a.approved_on IS NULL OR a.approved_on <= a.suspended_on)
                    THEN 'suspended'
                WHEN a.valid_until IS NOT NULL AND a.valid_until < a.fetched_at::DATE
                    THEN 'expired'
                ELSE 'active'
            END                             AS status,
            a.fetched_at::DATE              AS as_of
        FROM a
        LEFT JOIN staging.sigsif_country_map m ON m.pais = a.pais
        LEFT JOIN staging.dim_country c ON c.co_pais = m.co_pais
        WHERE a.pais NOT IN ({_EXCLUDED_SQL})
    """)

    con.execute("""
        CREATE OR REPLACE TABLE staging.sigsif_plants AS
        WITH latest AS (
            SELECT snapshot_id FROM raw.sigsif_snapshots ORDER BY snapshot_seq DESC LIMIT 1
        )
        -- a plant registered under several classes has one registry row each
        SELECT
            p.nr_sif                                        AS sif,
            any_value(p.cnpj)                               AS cnpj,
            any_value(p.razao_social)                       AS legal_name,
            nullif(any_value(p.nome_fantasia), '-')         AS trade_name,
            any_value(p.situacao)                           AS registry_status,
            any_value(p.logradouro)                         AS street,
            any_value(p.bairro)                             AS district,
            any_value(p.cep)                                AS postcode,
            any_value(p.municipio)                          AS municipality,
            any_value(p.uf)                                 AS uf,
            string_agg(DISTINCT p.categoria_classe, '; ' ORDER BY p.categoria_classe)
                                                            AS plant_classes,
            -- what kind of supplier this is, in a trader's words
            CASE
                WHEN bool_or(p.categoria_classe ILIKE '%ABATEDOURO%') THEN 'Slaughterhouse'
                WHEN bool_or(p.categoria_classe ILIKE '%BENEF%'
                    OR p.categoria_classe ILIKE '%CÁRNEOS%') THEN 'Processing plant'
                WHEN bool_or(p.categoria_classe ILIKE '%ENTREPOSTO%'
                    OR p.categoria_classe ILIKE '%ESTOCAGEM%') THEN 'Cold store'
                ELSE 'Other'
            END                                             AS plant_type
        FROM raw.sigsif_plants p JOIN latest USING (snapshot_id)
        GROUP BY p.nr_sif
    """)

    con.execute("""
        CREATE OR REPLACE TABLE staging.sigsif_approval_spans AS
        SELECT
            a.pais AS sigsif_country, a.sif, a.produto AS product,
            min(s.fetched_at)::DATE AS first_seen,
            max(s.fetched_at)::DATE AS last_seen,
            max(s.snapshot_seq) = (SELECT max(snapshot_seq) FROM raw.sigsif_snapshots)
                AS in_latest
        FROM raw.sigsif_approvals a JOIN raw.sigsif_snapshots s USING (snapshot_id)
        GROUP BY ALL
    """)
    return con.execute("SELECT count(*) FROM staging.sigsif_approvals").fetchone()[0]


def unmapped_countries(con: duckdb.DuckDBPyConnection) -> list[str]:
    """SIGSIF country names with no ComexStat code - add them to
    COUNTRY_OVERRIDES."""
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT pais FROM staging.sigsif_country_map WHERE co_pais IS NULL ORDER BY 1"
        ).fetchall()
    ]


def refresh_country_names_pt(con: duckdb.DuckDBPyConnection, countries_pt: list[dict]) -> None:
    """Store ComexStat's Portuguese country names (/tables/countries?language=pt)
    for the automatic SIGSIF name match."""
    con.execute(
        "CREATE OR REPLACE TABLE staging.sigsif_country_pt (co_pais VARCHAR, name_pt VARCHAR)"
    )
    if countries_pt:
        con.executemany(
            "INSERT INTO staging.sigsif_country_pt VALUES (?, ?)",
            [(r["id"], r["text"].strip()) for r in countries_pt],
        )


def _approval_keys(con: duckdb.DuckDBPyConnection, seq_offset: int) -> set[tuple[str, str, str]]:
    rows = con.execute(
        """
        SELECT pais, sif, produto FROM raw.sigsif_approvals
        WHERE snapshot_id = (
            SELECT snapshot_id FROM raw.sigsif_snapshots
            ORDER BY snapshot_seq DESC LIMIT 1 OFFSET ?
        )
        """,
        [seq_offset],
    ).fetchall()
    return set(rows)


def refresh(
    con: duckdb.DuckDBPyConnection,
    fetch: Any = fetch_csv,
    fetch_countries_pt: Any = None,
) -> SigsifResult:
    """Download both files, store a snapshot if the beef content changed,
    and rebuild staging. `fetch`/`fetch_countries_pt` are injectable for
    tests."""
    from dv_beef_exports.ingestion.comexstat_client import fetch_countries

    approvals = beef_approvals(fetch(APPROVALS_URL))
    plants = beef_plants(fetch(REGISTRY_URL), {a["SIF"] for a in approvals})
    fetched_at = datetime.now(UTC)
    snapshot_id = ingest_snapshot(con, approvals, plants, fetched_at)
    refresh_country_names_pt(con, (fetch_countries_pt or fetch_countries)(language="pt"))
    build_staging(con)

    result = SigsifResult(
        changed=snapshot_id is not None,
        fetched_at=fetched_at,
        approvals=len(approvals),
        plants=len(plants),
        unmapped_countries=unmapped_countries(con),
    )
    if result.changed:
        has_previous = con.execute("SELECT count(*) FROM raw.sigsif_snapshots").fetchone()[0] > 1
        if has_previous:
            now, before = _approval_keys(con, 0), _approval_keys(con, 1)
            result.added = sorted(now - before)
            result.removed = sorted(before - now)
    return result
