"""
Who may use the app. Two levels (docs/DEPLOY.md, "Sign-in"):

1. **Default: Streamlit Community Cloud's sharing list.** With no sign-in
   settings in the app's secrets, the app relies on Settings -> Sharing ->
   "Only specific people can view this app": Streamlit itself asks visitors
   to sign in with their e-mail (a one-time link, or Google) and blocks
   anyone not invited before this code runs. Nothing to configure here.
2. **Optional extra lock: Google sign-in in the app** (OpenID Connect,
   st.login) plus this app's own allowlist of e-mails and/or company
   domains. It switches on as soon as an [auth] section is in the secrets:

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

Once [auth] is present the gate fails closed: a missing provider or an empty
allowlist lets nobody in. DV_BEEF_EXPORTS_AUTH=off switches the in-app gate
off (local development and tests).

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
    PLATFORM = "platform"  # no in-app sign-in configured: the sharing list is the lock
    MISCONFIGURED = "misconfigured"  # in-app sign-in half set up: fail closed
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
    if not auth:
        return Access.PLATFORM
    if PROVIDER not in auth or not access:
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
    if decision in (Access.GRANTED, Access.PLATFORM):
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
                "The in-app sign-in is only half set up, so the app stays closed. An "
                "administrator needs to finish the login settings (see docs/DEPLOY.md)."
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
