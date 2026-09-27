#!/usr/bin/env python3
"""
AlvaOS Auth Manager
Session management, authentication decorators, rate limiting, and setup state.
"""

import os
import json
import secrets
import functools
from typing import Any, Dict
import time
from datetime import datetime, timedelta

from flask import request, jsonify
from common import _utc_now, _parse_iso, ensure_directories
from password_utils import hash_password

# ── File paths ────────────────────────────────────────────────────────────────
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
AUTH_FILE = '/var/lib/alvaos/auth.json'
SESSIONS_FILE = '/var/lib/alvaos/sessions.json'

# ── Session state ─────────────────────────────────────────────────────────────
# token -> {username, role, expires_at, csrf_token}
SESSIONS: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_HOURS = 24

# Temporary tokens used during 2FA two-step login: temp_token -> {username, role, expires_at}
TEMP_2FA_TOKENS: Dict[str, Dict[str, Any]] = {}
TEMP_2FA_TTL_SECONDS = 300  # 5 minutes

# ── Rate limiting ─────────────────────────────────────────────────────────────
# ip -> {count, window_start}
LOGIN_RATE_LIMIT: Dict[str, Dict[str, Any]] = {}
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 900  # 15 minutes


# ── Setup state ───────────────────────────────────────────────────────────────

def is_setup_complete():
    """Check if initial setup has been completed"""
    return os.path.exists(SETUP_STATUS_FILE)


def mark_setup_complete(password, version):
    """Mark the initial setup as complete and store password hash"""
    ensure_directories()

    auth_data = hash_password(password)

    with open(AUTH_FILE, 'w') as f:
        json.dump(auth_data, f)

    setup_data = {
        'setup_completed': True,
        'completed_at': datetime.now().isoformat(),
        'version': version
    }
    with open(SETUP_STATUS_FILE, 'w') as f:
        json.dump(setup_data, f, indent=2)


# ── Session persistence ───────────────────────────────────────────────────────
# Sessions used to live only in memory, so every backend restart - including the
# one at the end of each update - silently logged everyone out. They are kept on
# disk instead, in a file only the alvaos user can read: the tokens in it are
# bearer credentials, exactly like the password hash next to it.

def _save_sessions():
    """Persist sessions to disk, atomically and with owner-only permissions."""
    try:
        ensure_directories()
        tmp_path = f"{SESSIONS_FILE}.tmp"
        payload = {'sessions': SESSIONS}
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(payload, f)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, SESSIONS_FILE)
    except Exception as e:
        # A failure here must never break the request in flight; the worst case
        # is falling back to the previous behaviour of losing sessions.
        print(f"Warning: could not persist sessions: {e}")


def _load_sessions():
    """Restore sessions from disk, dropping anything already expired."""
    global SESSIONS
    try:
        if not os.path.exists(SESSIONS_FILE):
            return
        with open(SESSIONS_FILE, 'r') as f:
            data = json.load(f)
        stored = data.get('sessions') if isinstance(data, dict) else None
        if not isinstance(stored, dict):
            return
        now = _utc_now()
        restored = {}
        for token, session in stored.items():
            if not isinstance(session, dict):
                continue
            expires = _parse_iso(session.get('expires_at'))
            if expires and expires < now:
                continue
            if not session.get('csrf_token'):
                continue
            restored[token] = session
        SESSIONS.clear()
        SESSIONS.update(restored)
    except Exception as e:
        print(f"Warning: could not restore sessions: {e}")


# ── Session management ────────────────────────────────────────────────────────

def _purge_expired_sessions():
    """Remove sessions that have passed their expiry time."""
    now = _utc_now()
    expired = [t for t, s in SESSIONS.items() if _parse_iso(s.get('expires_at')) and _parse_iso(s['expires_at']) < now]
    for t in expired:
        del SESSIONS[t]
    expired_temp = [t for t, s in TEMP_2FA_TOKENS.items() if _parse_iso(s.get('expires_at')) and _parse_iso(s['expires_at']) < now]
    for t in expired_temp:
        del TEMP_2FA_TOKENS[t]
    if expired:
        _save_sessions()


def _create_session(username, role='admin'):
    """Create a new session token, returning the token string."""
    token = secrets.token_hex(32)
    csrf_token = secrets.token_hex(32)
    expires_at = (_utc_now() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    SESSIONS[token] = {'username': username, 'role': role, 'expires_at': expires_at, 'csrf_token': csrf_token}
    _save_sessions()
    return token


def _get_session(token):
    """Return session dict if valid and not expired, else None."""
    if not token or token not in SESSIONS:
        return None
    session = SESSIONS[token]
    expires = _parse_iso(session.get('expires_at'))
    if expires and expires < _utc_now():
        del SESSIONS[token]
        _save_sessions()
        return None
    return session


def _get_current_session():
    """Get the session for the current request."""
    token = request.headers.get('Authorization', '').strip()
    return _get_session(token)


def _destroy_session(token):
    """Revoke a single session. Returns True if a session was removed."""
    if not token or token not in SESSIONS:
        return False
    del SESSIONS[token]
    _save_sessions()
    return True


def _destroy_all_sessions():
    """Revoke every session. Used when the password changes."""
    SESSIONS.clear()
    TEMP_2FA_TOKENS.clear()
    _save_sessions()


# ── Auth decorators ───────────────────────────────────────────────────────────

def require_csrf_token(f):
    """Decorator to require a valid CSRF token for state-changing operations."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        session = _get_current_session()
        if not session:
            return jsonify({'error': 'Authentication required'}), 401

        # Get CSRF token from header
        csrf_token = request.headers.get('X-CSRF-Token', '').strip()
        if not csrf_token:
            return jsonify({'error': 'CSRF token missing'}), 403

        # Verify CSRF token matches session
        if csrf_token != session.get('csrf_token', ''):
            return jsonify({'error': 'Invalid CSRF token'}), 403

        return f(*args, **kwargs)
    return decorated_function


def require_auth(f=None, require_admin=False):
    """Decorator to require authentication. Optionally enforce admin role.

    Authentication is enforced even before first-run setup is complete. An
    unconfigured box used to serve every endpoint unauthenticated, which made the
    setup window a full takeover opportunity for anything that could reach the
    port. The only endpoints reachable before setup are the ones on the
    PRE_SETUP_ALLOWLIST (see alvaos-backend.py).
    """
    def decorator(fn):
        @functools.wraps(fn)
        def decorated_function(*args, **kwargs):
            _purge_expired_sessions()
            session = _get_current_session()
            if not session:
                return jsonify({'error': 'Authentication required'}), 401
            if require_admin and session.get('role') != 'admin':
                return jsonify({'error': 'Admin privileges required'}), 403
            return fn(*args, **kwargs)
        return decorated_function
    # Support both @require_auth and @require_auth(require_admin=True)
    if f is not None:
        return decorator(f)
    return decorator


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _check_rate_limit(ip):
    """Returns (allowed, retry_after_seconds). Updates rate limit state."""
    now = time.time()
    entry = LOGIN_RATE_LIMIT.get(ip)
    if entry is None or now - entry['window_start'] > LOGIN_WINDOW_SECONDS:
        LOGIN_RATE_LIMIT[ip] = {'count': 0, 'window_start': now}
        entry = LOGIN_RATE_LIMIT[ip]
    entry['count'] += 1
    if entry['count'] > LOGIN_MAX_ATTEMPTS:
        retry_after = int(LOGIN_WINDOW_SECONDS - (now - entry['window_start'])) + 1
        return False, retry_after
    return True, 0


def _reset_rate_limit(ip):
    LOGIN_RATE_LIMIT.pop(ip, None)
