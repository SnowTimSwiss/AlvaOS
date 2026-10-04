"""Pytest bootstrap: make the backend package importable from the tests.

Adds the ``backend/`` directory to ``sys.path`` so tests can simply
``import password_utils`` / ``import common`` regardless of the working
directory pytest is invoked from.
"""

import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The backup and buddy managers are made when app_services is imported; keep
# what they write out of /var/lib/alvaos (a developer machine, or a NAS).
if not os.environ.get("ALVAOS_STATE_DIR"):
    import tempfile
    os.environ["ALVAOS_STATE_DIR"] = tempfile.mkdtemp(prefix="alvaos-test-state-")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_sessions_file(tmp_path, monkeypatch):
    """Never write test sessions to /var/lib/alvaos/sessions.json on a
    developer machine (or a NAS someone runs the tests on)."""
    try:
        import auth_manager
    except Exception:  # Flask missing: those tests are skipped anyway
        return
    monkeypatch.setattr(auth_manager, "SESSIONS_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(auth_manager, "SIGNIN_LOG_FILE", str(tmp_path / "signin_log.json"))
    try:
        import alerts_manager
    except Exception:  # noqa: BLE001 - a missing optional dependency
        return
    monkeypatch.setattr(alerts_manager, "NOTIFICATIONS_STATE_FILE", str(tmp_path / "notifications.json"))
    monkeypatch.setattr(alerts_manager, "ALERTS_STATE_FILE", str(tmp_path / "alerts.json"))
