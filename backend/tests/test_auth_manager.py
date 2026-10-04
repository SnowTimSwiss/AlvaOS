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


# ── Signed-in devices ────────────────────────────────────────────────────────

def test_devices_are_described_in_plain_words():
    assert am.describe_device("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36") == "Chrome on Windows"
    assert am.describe_device("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                              "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1") == "Safari on iPhone"
    assert am.describe_device("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0") == "Firefox on Linux"
    assert am.describe_device("Mozilla/5.0 (Windows NT 10.0) Chrome/140.0 Safari/537.36 Edg/140.0") == "Edge on Windows"
    assert am.describe_device("") == "Unknown device"


def test_sessions_are_listed_without_secrets_and_others_can_be_revoked():
    am.SESSIONS.clear()
    mine = am._create_session("root")
    other = am._create_session("root")
    third = am._create_session("root")
    rows = am.list_sessions(mine)
    assert rows[0]["current"] is True and len(rows) == 3
    flat = repr(rows)
    for token in (mine, other, third):
        assert token not in flat and am.SESSIONS[token]["csrf_token"] not in flat

    assert am.revoke_session_by_id(am.session_public_id(mine), keep_token=mine) == "current"
    assert am.revoke_session_by_id(am.session_public_id(other), keep_token=mine) == "revoked"
    assert am.revoke_session_by_id("nope", keep_token=mine) == "unknown"
    assert am.revoke_other_sessions(mine) == 1
    assert list(am.SESSIONS) == [mine]
    am.SESSIONS.clear()


def test_last_use_is_written_at_most_every_few_minutes(monkeypatch):
    am.SESSIONS.clear()
    token = am._create_session("root")
    writes = []
    monkeypatch.setattr(am, "_save_sessions", lambda: writes.append(1))
    session = am.SESSIONS[token]
    am._touch_session(session)
    assert writes == []                         # just created
    from datetime import timedelta
    session["last_seen_at"] = (am._utc_now() - timedelta(minutes=6)).isoformat()
    am._touch_session(session)
    assert writes == [1]
    am.SESSIONS.clear()
