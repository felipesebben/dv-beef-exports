"""The app's tests run with the login gate off (app/auth.py fails closed
without secrets); test_auth.py switches it back on where it tests the gate."""

from __future__ import annotations

import pytest

from dv_beef_exports.app.auth import AUTH_OFF_ENV


@pytest.fixture(autouse=True)
def _login_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_OFF_ENV, "off")
