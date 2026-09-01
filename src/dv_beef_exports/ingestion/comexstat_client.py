"""
Client for the ComexStat /general API – Brazil's foreign trade statistics.

See docs/decisions for the research behind this design
"""

from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

BASE_URL = "https://api-comexstat.mdic.gov.br"
TIMEOUT_SECONDS = 30

class ComexStatError(Exception):
    """Raised when the ComexStat API returns an error we shouldn't retry."""

class ComexStatTransientError(ComexStatError):
    """Raised for errors worth retrying (soft Cloudflare 403s, 5xx, network issues)."""

def fetch_exports(
        ncm_codes: list[str],
        period_from: str,
        period_to: str,
        details: list[str] | None = None,
        month_detail: bool = True,
) -> list[dict[str, Any]]:
    """
    Fetch export records from ComexStat for the given NCM codes and period.

    Args:
        ncm_codes: 8-digit NCM codes to filter on (see ncm_codes.py).
        period_from: start month, "YYYY-MM".
        period_to: end month, "YYYY-MM".
        details: grouping dimensions, e.g. ["country", "ncm"].
            Defaults to ["country", "ncm"] if not given.
        month_detail: whether to break results out by month, vs. one
            aggregate row per group for the whole period.

    Returns:
        Result rows as returned by the API. Requested with ?language=en,
        so country/NCM names come back human-readable directly — no
        separate code-lookup step needed.

    Raises:
        ComexStatError: on a non-retryable API error (bad request,
            unexpected response shape).
        ComexStatTransientError: if retries are exhausted on a transient
            error (network issue, soft 403, 5xx).
    """
    if details is None:
        details = ["country", "ncm"]

    body = {
        "flow": "export",
        "monthDetail": month_detail,
        "period": {"from": period_from, "to": period_to},
        "filters": [{"filter": "ncm", "values": ncm_codes}],
        "details": details,
        "metrics": ["metricFOB", "metricKG"],
    }

    return _post_general(body)

@retry(
    retry=retry_if_exception_type(ComexStatTransientError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _post_general(body: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        response = requests.post(
            f"{BASE_URL}/general",
            params={"language": "en"},
            json=body,
            headers={"Referer": f"{BASE_URL}/docs"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ComexStatTransientError(f"Network error calling ComexStat: {exc}") from exc

    if response.status_code == 403:
        raise ComexStatTransientError("ComexStat returned 403 (likely a soft Cloudflare block)")
    if response.status_code >= 500:
        raise ComexStatTransientError(f"ComexStat returned {response.status_code}")
    if response.status_code >= 400:
        raise ComexStatError(f"ComexStat returned {response.status_code}: {response.text}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ComexStatError(f"ComexStat returned a non-JSON response: {exc}") from exc

    try:
        return payload["data"]["list"]
    except (KeyError, TypeError) as exc:
        raise ComexStatError(f"Unexpected response shape from ComexStat: {exc}") from exc