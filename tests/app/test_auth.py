"""Tests for the login gate (app/auth.py): Google sign-in plus an allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dv_beef_exports.app.auth import AUTH_OFF_ENV, Access, decide, is_allowed

APP_PATH = str(Path(__file__).parents[2] / "src" / "dv_beef_exports" / "app" / "main.py")

SECRETS = {
    "auth": {
        "redirect_uri": "http://localhost:8501/oauth2callback",
        "cookie_secret": "not-a-real-secret-only-for-tests-0123456789",
        "google": {
            "client_id": "id",
            "client_secret": "secret",
            "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        },
    },
    "access": {
        "allowed_emails": ["Partner@Gmail.com"],
        "allowed_domains": ["sebbeninternational.com.br"],
    },
}


@pytest.mark.parametrize(
    ("email", "allowed"),
    [
        ("ana@sebbeninternational.com.br", True),
        ("ANA@SebbenInternational.com.br", True),  # case-insensitive
        ("partner@gmail.com", True),  # an allowed address
        ("other@gmail.com", False),
        ("ana@evil-sebbeninternational.com.br", False),  # look-alike domain
        ("ana@sebbeninternational.com.br.evil.com", False),  # suffix trick
        ("ana@mail.sebbeninternational.com.br", False),  # subdomains aren't the domain
        ("sebbeninternational.com.br", False),  # no address at all
        ("a@b@sebbeninternational.com.br", False),
        ("", False),
        (None, False),
    ],
)
def test_allowlist_matches_addresses_and_exact_domains(email, allowed) -> None:
    access = SECRETS["access"]

    assert is_allowed(email, access["allowed_emails"], access["allowed_domains"]) is allowed


def test_a_leading_at_in_the_domain_list_is_forgiven() -> None:
    assert is_allowed("ana@acme.com", [], ["@acme.com"])


def _user(email: str, verified: bool = True) -> dict:
    return {"email": email, "email_verified": verified}


def test_access_needs_a_verified_allowed_google_account() -> None:
    assert decide(SECRETS, True, _user("ana@sebbeninternational.com.br")) is Access.GRANTED
    assert decide(SECRETS, True, _user("other@gmail.com")) is Access.DENIED
    # an address Google hasn't verified proves nothing
    assert decide(SECRETS, True, _user("ana@sebbeninternational.com.br", False)) is Access.DENIED
    assert decide(SECRETS, True, {"email": "ana@sebbeninternational.com.br"}) is Access.DENIED
    assert decide(SECRETS, False, {}) is Access.SIGN_IN


def test_without_sign_in_settings_the_sharing_list_is_the_lock() -> None:
    """No [auth]: the app relies on Community Cloud's "only specific people"
    sharing, which blocks uninvited visitors before the app runs."""
    assert decide({}, False, {}) is Access.PLATFORM
    assert decide({"access": SECRETS["access"]}, False, {}) is Access.PLATFORM


@pytest.mark.parametrize(
    "secrets",
    [
        {"auth": {"redirect_uri": "x"}, "access": SECRETS["access"]},  # no Google provider
        {"auth": SECRETS["auth"]},  # no allowlist
        {"auth": SECRETS["auth"], "access": {"allowed_emails": [], "allowed_domains": [" "]}},
    ],
)
def test_a_half_configured_sign_in_fails_closed(secrets) -> None:
    # even a "signed-in" visitor gets nothing while the setup is incomplete
    assert decide(secrets, True, _user("ana@sebbeninternational.com.br")) is Access.MISCONFIGURED


def _app(monkeypatch: pytest.MonkeyPatch, secrets: dict | None) -> AppTest:
    monkeypatch.delenv(AUTH_OFF_ENV, raising=False)
    at = AppTest.from_file(APP_PATH)
    for key, value in (secrets or {}).items():
        at.secrets[key] = value
    at.run(timeout=30)
    return at


def _shows_data(at: AppTest) -> bool:
    return any(t.value == "Market overview" for t in at.title) or bool(at.dataframe)


def test_without_sign_in_settings_the_app_opens_behind_the_sharing_list(monkeypatch) -> None:
    at = _app(monkeypatch, None)

    assert not at.exception
    assert _shows_data(at)


def test_a_half_configured_app_stays_closed(monkeypatch) -> None:
    at = _app(monkeypatch, {"auth": {"redirect_uri": "x"}, "access": SECRETS["access"]})

    assert not at.exception
    assert not _shows_data(at)
    html = " ".join(e.proto.body for e in at.get("html"))
    assert "only half set up, so the app stays closed" in html


def test_a_visitor_sees_only_the_sign_in_button(monkeypatch) -> None:
    at = _app(monkeypatch, SECRETS)

    assert not at.exception
    assert not _shows_data(at)
    assert [b.label for b in at.button] == ["Sign in with Google"]
