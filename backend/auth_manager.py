#!/usr/bin/env python3
"""
AlvaOS Auth Manager
Session management, authentication decorators, rate limiting, and setup state.
"""

import hashlib
import os
import json
import re
import secrets
import functools
from typing import Any, Dict
import time
from datetime import datetime, timedelta

from flask import has_request_context, request, jsonify
from common import _utc_now, _parse_iso, ensure_directories
from password_utils import hash_password

# ── File paths ────────────────────────────────────────────────────────────────
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
AUTH_FILE = '/var/lib/alvaos/auth.json'
SESSIONS_FILE = '/var/lib/alvaos/sessions.json'
SIGNIN_LOG_FILE = '/var/lib/alvaos/signin_log.json'
IDLE_HOURS = 8           # a session nobody used for this long ends (closed browser, lost laptop)
FAILED_KEPT = 50

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
    expired = [t for t, s in SESSIONS.items()
               if (_parse_iso(s.get('expires_at')) and _parse_iso(s['expires_at']) < now) or _idle(s, now)]
    for t in expired:
        del SESSIONS[t]
    expired_temp = [t for t, s in TEMP_2FA_TOKENS.items() if _parse_iso(s.get('expires_at')) and _parse_iso(s['expires_at']) < now]
    for t in expired_temp:
        del TEMP_2FA_TOKENS[t]
    if expired:
        _save_sessions()


LAST_SEEN_RESOLUTION = timedelta(minutes=5)   # avoid a disk write per request


def session_public_id(token):
    """A stable id for a session that can be shown and sent around. The
    token itself is a credential and never leaves the server again."""
    return hashlib.sha256(str(token).encode()).hexdigest()[:16]


def describe_device(user_agent):
    """'Firefox on Windows' from a User-Agent header, or 'Unknown device'."""
    ua = str(user_agent or '')
    if not ua:
        return 'Unknown device'
    browser = next((name for pattern, name in (
        (r'Edg/', 'Edge'), (r'OPR/|Opera', 'Opera'), (r'Firefox/', 'Firefox'),
        (r'Chrome/|CriOS/', 'Chrome'), (r'Safari/', 'Safari'), (r'curl/', 'curl'),
    ) if re.search(pattern, ua)), 'A browser')
    system = next((name for pattern, name in (
        (r'iPhone', 'iPhone'), (r'iPad', 'iPad'), (r'Android', 'Android'), (r'Windows', 'Windows'),
        (r'Mac OS X|Macintosh', 'macOS'), (r'CrOS', 'ChromeOS'), (r'Linux', 'Linux'),
    ) if re.search(pattern, ua)), '')
    return f'{browser} on {system}' if system else browser


def _client_details():
    if not has_request_context():
        return {}
    return {
        'ip': request.remote_addr or '',
        'device': describe_device(request.headers.get('User-Agent', '')),
    }


def _create_session(username, role='admin'):
    """Create a new session token, returning the token string."""
    token = secrets.token_hex(32)
    csrf_token = secrets.token_hex(32)
    now = _utc_now()
    expires_at = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    SESSIONS[token] = {'username': username, 'role': role, 'expires_at': expires_at, 'csrf_token': csrf_token,
                       'created_at': now.isoformat(), 'last_seen_at': now.isoformat(), **_client_details()}
    _save_sessions()
    return token


# ── Sign-in history ───────────────────────────────────────────────────────────
# Shown right after signing in (never on the sign-in page, which anyone on the
# network can open): when and from where the last sign-in was, and how many
# wrong passwords were tried since.

def _read_signin_log(path=None):
    try:
        with open(path or SIGNIN_LOG_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_signin_log(data, path=None):
    path = path or SIGNIN_LOG_FILE
    try:
        ensure_directories()
        tmp = f'{path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"Warning: could not write the sign-in log: {e}")


def record_failed_signin(path=None):
    data = _read_signin_log(path)
    failed = [f for f in data.get('failed', []) if isinstance(f, dict)][-(FAILED_KEPT - 1):]
    failed.append({'at': _utc_now().isoformat(), **_client_details()})
    data['failed'] = failed
    _write_signin_log(data, path)


def record_signin(path=None):
    """Note this sign-in; returns what the person should know about the
    previous one: {'previous': {...} | None, 'failed': n, 'failed_from': [...]}."""
    data = _read_signin_log(path)
    failed = [f for f in data.get('failed', []) if isinstance(f, dict)]
    previous = data.get('last') if isinstance(data.get('last'), dict) else None
    _write_signin_log({'last': {'at': _utc_now().isoformat(), **_client_details()}, 'failed': []}, path)
    return {'previous': previous, 'failed': len(failed),
            'failed_from': sorted({str(f.get('ip') or '') for f in failed if f.get('ip')})[:5]}


def _touch_session(session):
    """Remember when (and from where) a session was last used, at most every
    few minutes so an open dashboard does not write to disk on every poll."""
    now = _utc_now()
    last = _parse_iso(session.get('last_seen_at'))
    if last and now - last < LAST_SEEN_RESOLUTION:
        return
    session['last_seen_at'] = now.isoformat()
    session.update(_client_details())
    _save_sessions()


def list_sessions(current_token=None):
    """Every active session, newest use first, without the secrets."""
    _purge_expired_sessions()
    rows = []
    for token, session in SESSIONS.items():
        rows.append({
            'id': session_public_id(token),
            'username': session.get('username'),
            'device': session.get('device') or 'Unknown device',
            'ip': session.get('ip') or '',
            'created_at': session.get('created_at'),
            'last_seen_at': session.get('last_seen_at') or session.get('created_at'),
            'expires_at': session.get('expires_at'),
            'current': token == current_token,
        })
    current = [r for r in rows if r['current']]
    others = sorted((r for r in rows if not r['current']), key=lambda r: str(r['last_seen_at'] or ''), reverse=True)
    return current + others


def revoke_session_by_id(public_id, keep_token=None):
    """Sign out one session by its public id. The current one is refused."""
    for token in list(SESSIONS):
        if session_public_id(token) == public_id:
            if token == keep_token:
                return 'current'
            del SESSIONS[token]
            _save_sessions()
            return 'revoked'
    return 'unknown'


def revoke_other_sessions(keep_token):
    """Sign out everywhere except the session making the request."""
    others = [t for t in SESSIONS if t != keep_token]
    for token in others:
        del SESSIONS[token]
    TEMP_2FA_TOKENS.clear()
    if others:
        _save_sessions()
    return len(others)


def _idle(session, now):
    last = _parse_iso(session.get('last_seen_at'))
    return bool(last and now - last > timedelta(hours=IDLE_HOURS))


def _get_session(token):
    """Return session dict if valid, not expired and not idle too long, else None."""
    if not token or token not in SESSIONS:
        return None
    session = SESSIONS[token]
    expires = _parse_iso(session.get('expires_at'))
    if (expires and expires < _utc_now()) or _idle(session, _utc_now()):
        del SESSIONS[token]
        _save_sessions()
        return None
    return session


def admin_signed_in(token):
    """Whether this sign-in is an admin's and still valid, without changing
    anything (asked from another thread by admin_terminal.py)."""
    session = SESSIONS.get(token) if token else None
    if not session or session.get('role') != 'admin':
        return False
    now = _utc_now()
    expires = _parse_iso(session.get('expires_at'))
    return not ((expires and expires < now) or _idle(session, now))


def _get_current_session():
    """Get the session for the current request."""
    token = request.headers.get('Authorization', '').strip()
    session = _get_session(token)
    if session is not None:
        _touch_session(session)
    return session


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
