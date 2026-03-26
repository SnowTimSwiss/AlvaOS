#!/usr/bin/env python3
"""
AlvaOS Auth Manager
Session management, authentication decorators, rate limiting, and setup state.
"""

import os
import json
import secrets
import hashlib
import functools
import time
from datetime import datetime, timedelta

from flask import request, jsonify
from common import _utc_now, _parse_iso, ensure_directories

# ── File paths ────────────────────────────────────────────────────────────────
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
AUTH_FILE = '/var/lib/alvaos/auth.json'

# ── Session state ─────────────────────────────────────────────────────────────
# token -> {username, role, expires_at, csrf_token}
SESSIONS = {}
SESSION_TTL_HOURS = 24

# Temporary tokens used during 2FA two-step login: temp_token -> {username, role, expires_at}
TEMP_2FA_TOKENS = {}
TEMP_2FA_TTL_SECONDS = 300  # 5 minutes

# ── Rate limiting ─────────────────────────────────────────────────────────────
# ip -> {count, window_start}
LOGIN_RATE_LIMIT = {}
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 900  # 15 minutes


# ── Setup state ───────────────────────────────────────────────────────────────

def is_setup_complete():
    """Check if initial setup has been completed"""
    return os.path.exists(SETUP_STATUS_FILE)


def mark_setup_complete(password, version):
    """Mark the initial setup as complete and store password hash"""
    ensure_directories()

    # Simple hash for 0.1
    salt = secrets.token_hex(8)
    h = hashlib.sha256((password + salt).encode()).hexdigest()

    auth_data = {
        'password_hash': h,
        'salt': salt
    }

    with open(AUTH_FILE, 'w') as f:
        json.dump(auth_data, f)

    setup_data = {
        'setup_completed': True,
        'completed_at': datetime.now().isoformat(),
        'version': version
    }
    with open(SETUP_STATUS_FILE, 'w') as f:
        json.dump(setup_data, f, indent=2)


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


def _create_session(username, role='admin'):
    """Create a new session token, returning the token string."""
    token = secrets.token_hex(32)
    csrf_token = secrets.token_hex(32)
    expires_at = (_utc_now() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    SESSIONS[token] = {'username': username, 'role': role, 'expires_at': expires_at, 'csrf_token': csrf_token}
    return token


def _get_session(token):
    """Return session dict if valid and not expired, else None."""
    if not token or token not in SESSIONS:
        return None
    session = SESSIONS[token]
    expires = _parse_iso(session.get('expires_at'))
    if expires and expires < _utc_now():
        del SESSIONS[token]
        return None
    return session


def _get_current_session():
    """Get the session for the current request."""
    token = request.headers.get('Authorization', '').strip()
    return _get_session(token)


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
    """Decorator to require authentication. Optionally enforce admin role."""
    def decorator(fn):
        @functools.wraps(fn)
        def decorated_function(*args, **kwargs):
            if not is_setup_complete():
                return fn(*args, **kwargs)
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
