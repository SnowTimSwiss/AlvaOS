"""Tests for session and rate-limiting logic in backend/auth_manager.py.

auth_manager imports Flask at module load, so these tests are skipped when
Flask is not installed (e.g. a minimal local environment).
"""

import pytest

pytest.importorskip("flask")

import auth_manager as am  # noqa: E402


def test_create_session_registers_token():
    token = am._create_session("root", role="admin")
    try:
        assert token in am.SESSIONS
        session = am._get_session(token)
        assert session is not None
        assert session["username"] == "root"
        assert session["role"] == "admin"
        assert session["csrf_token"]
        assert token != session["csrf_token"]
    finally:
        am.SESSIONS.pop(token, None)


def test_get_session_unknown_token():
    assert am._get_session("does-not-exist") is None
    assert am._get_session("") is None


def test_expired_session_is_purged_on_access():
    token = am._create_session("root")
    try:
        # Force expiry into the past.
        past = (am._utc_now() - am.timedelta(hours=1)).isoformat()
        am.SESSIONS[token]["expires_at"] = past
        assert am._get_session(token) is None
        # Accessing an expired session should drop it.
        assert token not in am.SESSIONS
    finally:
        am.SESSIONS.pop(token, None)


def test_purge_expired_sessions_removes_only_expired():
    fresh = am._create_session("fresh-user")
    stale = am._create_session("stale-user")
    try:
        am.SESSIONS[stale]["expires_at"] = (
            am._utc_now() - am.timedelta(seconds=1)
        ).isoformat()
        am._purge_expired_sessions()
        assert fresh in am.SESSIONS
        assert stale not in am.SESSIONS
    finally:
        am.SESSIONS.pop(fresh, None)
        am.SESSIONS.pop(stale, None)


def test_rate_limit_blocks_after_max_attempts():
    ip = "203.0.113.7"
    am._reset_rate_limit(ip)
    try:
        for _ in range(am.LOGIN_MAX_ATTEMPTS):
            allowed, _ = am._check_rate_limit(ip)
            assert allowed is True
        # One more should be blocked with a positive retry-after.
        allowed, retry_after = am._check_rate_limit(ip)
        assert allowed is False
        assert retry_after > 0
    finally:
        am._reset_rate_limit(ip)


def test_reset_rate_limit_clears_state():
    ip = "203.0.113.8"
    for _ in range(am.LOGIN_MAX_ATTEMPTS + 2):
        am._check_rate_limit(ip)
    am._reset_rate_limit(ip)
    allowed, _ = am._check_rate_limit(ip)
    assert allowed is True
    am._reset_rate_limit(ip)
