"""Tests for the ComexStat API client."""

import tenacity

from dv_beef_exports.ingestion import comexstat_client
from dv_beef_exports.ingestion.comexstat_client import (
    BASE_URL,
    ComexStatError,
    ComexStatTransientError,
    fetch_exports,
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
