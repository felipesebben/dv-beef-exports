"""Smoke test: proves the package installs and imports cleanly, and that CI is wired up."""

import dv_beef_exports


def test_package_imports() -> None:
    assert dv_beef_exports is not None
