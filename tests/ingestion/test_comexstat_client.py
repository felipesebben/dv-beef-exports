"""Tests for the ComexStat API client."""

import tenacity

from dv_beef_exports.ingestion import comexstat_client
from dv_beef_exports.ingestion.comexstat_client import (
    BASE_URL,
    ComexStatError,
    ComexStatTransientError,
    fetch_countries,
    fetch_country_blocs,
    fetch_economic_blocks,
    fetch_exports,
    fetch_ncm_hierarchy,
)


def _mock_response(rows: list[dict]) -> dict:
    return {"data": {"list": rows}}


def test_fetch_exports_sends_expected_request_body(requests_mock) -> None:
    requests_mock.post(f"{BASE_URL}/general", json=_mock_response([]))

    fetch_exports(["02023000"], "2024-01", "2024-03")

    request = requests_mock.last_request
    assert request.qs["language"] == ["en"]
    assert request.headers["Referer"] == f"{BASE_URL}/docs"
    assert request.json() == {
        "flow": "export",
        "monthDetail": True,
        "period": {"from": "2024-01", "to": "2024-03"},
        "filters": [{"filter": "ncm", "values": ["02023000"]}],
        "details": ["country", "ncm"],
        "metrics": ["metricFOB", "metricKG"],
    }


def test_fetch_exports_returns_response_rows(requests_mock) -> None:
    rows = [{"country": "China", "metricFOB": "1200000000"}]
    requests_mock.post(f"{BASE_URL}/general", json=_mock_response(rows))

    result = fetch_exports(["02023000"], "2024-01", "2024-03")

    assert result == rows


def test_fetch_exports_uses_custom_details_and_month_detail(requests_mock) -> None:
    requests_mock.post(f"{BASE_URL}/general", json=_mock_response([]))

    fetch_exports(["02023000"], "2024-01", "2024-03", details=["country"], month_detail=False)

    body = requests_mock.last_request.json()
    assert body["details"] == ["country"]
    assert body["monthDetail"] is False


def test_retries_on_soft_403_then_succeeds(requests_mock, monkeypatch) -> None:
    monkeypatch.setattr(comexstat_client._post_general.retry, "wait", tenacity.wait_none())
    rows = [{"country": "China"}]
    requests_mock.post(
        f"{BASE_URL}/general",
        [{"status_code": 403}, {"json": _mock_response(rows), "status_code": 200}],
    )

    result = fetch_exports(["02023000"], "2024-01", "2024-03")

    assert result == rows
    assert requests_mock.call_count == 2


def test_retries_on_rate_limit_then_succeeds(requests_mock, monkeypatch) -> None:
    monkeypatch.setattr(comexstat_client._post_general.retry, "wait", tenacity.wait_none())
    rows = [{"country": "China"}]
    requests_mock.post(
        f"{BASE_URL}/general",
        [{"status_code": 429}, {"json": _mock_response(rows), "status_code": 200}],
    )

    result = fetch_exports(["02023000"], "2024-01", "2024-03")

    assert result == rows
    assert requests_mock.call_count == 2


def test_raises_transient_error_after_exhausting_retries(requests_mock, monkeypatch) -> None:
    monkeypatch.setattr(comexstat_client._post_general.retry, "wait", tenacity.wait_none())
    requests_mock.post(f"{BASE_URL}/general", status_code=403)

    try:
        fetch_exports(["02023000"], "2024-01", "2024-03")
        raise AssertionError("expected ComexStatTransientError")
    except ComexStatTransientError:
        pass

    assert requests_mock.call_count == 4


def test_raises_comexstat_error_on_bad_request_without_retrying(requests_mock) -> None:
    requests_mock.post(f"{BASE_URL}/general", status_code=400, text="bad filter")

    try:
        fetch_exports(["02023000"], "2024-01", "2024-03")
        raise AssertionError("expected ComexStatError")
    except ComexStatError:
        pass

    assert requests_mock.call_count == 1


def test_raises_comexstat_error_on_unexpected_response_shape(requests_mock) -> None:
    requests_mock.post(f"{BASE_URL}/general", json={"unexpected": "shape"})

    try:
        fetch_exports(["02023000"], "2024-01", "2024-03")
        raise AssertionError("expected ComexStatError")
    except ComexStatError:
        pass


def test_fetch_countries_sends_expected_request(requests_mock) -> None:
    rows = [{"id": "105", "text": "Brazil"}]
    requests_mock.get(f"{BASE_URL}/tables/countries", json=_mock_response(rows))

    result = fetch_countries()

    assert result == rows
    request = requests_mock.last_request
    assert request.qs["language"] == ["en"]
    assert request.headers["Referer"] == f"{BASE_URL}/docs"


def test_fetch_economic_blocks_sends_expected_request(requests_mock) -> None:
    rows = [{"id": "111", "text": "Southern Common Market (MERCOSUL)"}]
    requests_mock.get(f"{BASE_URL}/tables/economic-blocks", json=_mock_response(rows))

    result = fetch_economic_blocks()

    assert result == rows
    assert "add" not in requests_mock.last_request.qs


def test_fetch_country_blocs_includes_add_country_param(requests_mock) -> None:
    rows = [
        {
            "economicBlock": "Southern Common Market (MERCOSUL)",
            "country": "Paraguay",
            "coBlock": "111",
            "coCountry": "586",
        }
    ]
    requests_mock.get(f"{BASE_URL}/tables/economic-blocks", json=_mock_response(rows))

    result = fetch_country_blocs()

    assert result == rows
    assert requests_mock.last_request.qs["add"] == ["country"]


def test_fetch_ncm_hierarchy_filters_to_exact_code_match(requests_mock) -> None:
    rows = [
        {"coNcm": "02023000", "subHeadingCode": "020230", "unit": "KILOGRAM"},
        {"coNcm": "02021000", "subHeadingCode": "020210", "unit": "KILOGRAM"},
    ]
    requests_mock.get(f"{BASE_URL}/tables/ncm", json=_mock_response(rows))

    result = fetch_ncm_hierarchy("02023000")

    assert result == rows[0]
    request = requests_mock.last_request
    assert request.qs["add"] == ["sh"]
    assert request.qs["search"] == ["02023000"]


def test_fetch_ncm_hierarchy_returns_none_when_not_found(requests_mock) -> None:
    requests_mock.get(f"{BASE_URL}/tables/ncm", json=_mock_response([]))

    result = fetch_ncm_hierarchy("99999999")

    assert result is None


def test_get_tables_retries_on_rate_limit_then_succeeds(requests_mock, monkeypatch) -> None:
    monkeypatch.setattr(comexstat_client._get_tables.retry, "wait", tenacity.wait_none())
    rows = [{"id": "105", "text": "Brazil"}]
    requests_mock.get(
        f"{BASE_URL}/tables/countries",
        [{"status_code": 429}, {"json": _mock_response(rows), "status_code": 200}],
    )

    result = fetch_countries()

    assert result == rows
    assert requests_mock.call_count == 2
