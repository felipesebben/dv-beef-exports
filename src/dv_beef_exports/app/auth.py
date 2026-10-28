"""
Who may use the app: Google sign-in (OpenID Connect, Streamlit's st.login)
plus an allowlist of e-mails and/or company domains. No passwords exist
anywhere - Google proves who the person is (with its own 2-step
verification and brute-force protection), and the allowlist decides whether
that person may come in.

Configuration lives in Streamlit secrets, never in git (`docs/DEPLOY.md`):

    [auth]                      # Streamlit's own OIDC settings
    redirect_uri = "https://<app>.streamlit.app/oauth2callback"
    cookie_secret = "<random, 32+ bytes>"
    [auth.google]
    client_id = "..."
    client_secret = "..."
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

    [access]                    # this app's allowlist
    allowed_emails = ["someone@example.com"]
    allowed_domains = ["sebbeninternational.com.br"]

Fails closed: with no [auth] or no allowlist configured, nobody gets in.
For local development and tests only, DV_BEEF_EXPORTS_AUTH=off switches the
gate off (never set it on a deployed app).

The data itself is public (committed to a public repo); this protects the
app - its analysis and supplier pages - not the raw numbers.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from enum import Enum

import streamlit as st

from dv_beef_exports.app.design import card, note, page_header

PROVIDER = "google"
AUTH_OFF_ENV = "DV_BEEF_EXPORTS_AUTH"


class Access(Enum):
    MISCONFIGURED = "misconfigured"  # fail closed
    SIGN_IN = "sign_in"  # not signed in yet
    DENIED = "denied"  # signed in, not on the allowlist (or e-mail unverified)
    GRANTED = "granted"


def _normalise(values: Iterable[str] | None) -> set[str]:
    return {v.strip().lower().lstrip("@") for v in (values or []) if v and v.strip()}


def is_allowed(
    email: str | None, allowed_emails: Iterable[str], allowed_domains: Iterable[str]
) -> bool:
    """Whether an e-mail is on the allowlist: an exact (case-insensitive)
    address, or an address whose domain is exactly an allowed domain - so
    "sebbeninternational.com.br" admits "ana@sebbeninternational.com.br" but
    not "ana@evil-sebbeninternational.com.br" or "...com.br.evil.com"."""
    if not email or email.count("@") != 1:
        return False
    address = email.strip().lower()
    domain = address.split("@", 1)[1]
    return address in _normalise(allowed_emails) or domain in _normalise(allowed_domains)


def decide(
    secrets: Mapping,
    is_logged_in: bool,
    user: Mapping,
) -> Access:
    """The whole access decision, as a pure function of the secrets and the
    signed-in user's claims - so every path is unit-tested."""
    auth = secrets.get("auth") if hasattr(secrets, "get") else None
    access = secrets.get("access") if hasattr(secrets, "get") else None
    if not auth or PROVIDER not in auth or not access:
        return Access.MISCONFIGURED
    emails, domains = access.get("allowed_emails", []), access.get("allowed_domains", [])
    if not _normalise(emails) and not _normalise(domains):
        return Access.MISCONFIGURED  # an empty allowlist would mean "nobody", say so
    if not is_logged_in:
        return Access.SIGN_IN
    # Google always sends email_verified; an unverified address proves nothing
    if user.get("email_verified") is not True:
        return Access.DENIED
    return Access.GRANTED if is_allowed(user.get("email"), emails, domains) else Access.DENIED


def _secrets() -> Mapping:
    try:
        return st.secrets.to_dict()
    except Exception:  # no secrets file at all
        return {}


def require_login() -> None:
    """Stop the script unless the visitor is signed in and allowed. Call in
    main.py before anything that shows data."""
    if os.environ.get(AUTH_OFF_ENV, "").lower() == "off":
        return
    # .get: st.user only has is_logged_in once [auth] is configured
    decision = decide(_secrets(), bool(st.user.get("is_logged_in", False)), st.user.to_dict())
    if decision is Access.GRANTED:
        return
    page_header(
        "Beef export intelligence",
        "Sign in",
        "This app is for the Sebben team. Sign in with the Google account your access was "
        "set up for.",
    )
    with card("signin"):
        if decision is Access.MISCONFIGURED:
            note(
                "Sign-in isn't set up for this app yet, so it stays closed. An administrator "
                "needs to add the login settings (see docs/DEPLOY.md)."
            )
        elif decision is Access.SIGN_IN:
            st.button(
                "Sign in with Google",
                type="primary",
                icon=":material/login:",
                on_click=st.login,
                args=(PROVIDER,),
            )
        else:
            note(
                f"You're signed in as **{st.user.get('email', 'an unknown account')}**, "
                "which doesn't have access. Ask the team for access, or sign in with "
                "another account."
            )
            st.button("Sign out", icon=":material/logout:", on_click=st.logout)
    st.stop()


def signed_in_as() -> str | None:
    """The signed-in e-mail, for the footer (None when the gate is off)."""
    if os.environ.get(AUTH_OFF_ENV, "").lower() == "off" or not st.user.get("is_logged_in"):
        return None
    return st.user.get("email")
