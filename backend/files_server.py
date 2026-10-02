#!/usr/bin/env python3
"""AlvaOS Files: the shared folders as an app of their own, on port 8090.

Everyone from Storage › Users signs in with their share password and sees
exactly the shares they can open over the network, with the same rights:
every file operation runs through `alvaos-priv --as <person>`, so Linux
itself checks them. The AlvaOS admin signs in as "admin" with the admin
password (and 2FA code) and sees every share.

No database: people and shares come from the same files the rest of AlvaOS
uses (users.json, shares.json). Sessions live in files_sessions.json.
"""

import hashlib
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory

import files_manager
from password_utils import verify_password

PORT = 8090
STATE_DIR = '/var/lib/alvaos'
USERS_FILE = os.path.join(STATE_DIR, 'users.json')
SHARES_FILE = os.path.join(STATE_DIR, 'shares.json')
AUTH_FILE = os.path.join(STATE_DIR, 'auth.json')
SESSIONS_FILE = os.path.join(STATE_DIR, 'files_sessions.json')
SESSION_DAYS = 14
COOKIE = 'alvaos_files'
LINK_TTL_SECONDS = 600
UPLOAD_LIMIT_BYTES = 4 * 1024 ** 3
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60

APP_ROOT = '/opt/alvaos/webui/files-app'
if not os.path.isdir(APP_ROOT):
    APP_ROOT = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'files-app')

app = Flask(__name__, static_folder=None)
_lock = threading.Lock()
_links: Dict[str, Dict[str, Any]] = {}
_attempts: Dict[str, Dict[str, float]] = {}


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Sessions ─────────────────────────────────────────────────────────────────

def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_sessions() -> Dict[str, Dict[str, Any]]:
    sessions = _read_json(SESSIONS_FILE)
    now = _now().isoformat()
    return {k: v for k, v in sessions.items() if isinstance(v, dict) and str(v.get('expires_at')) > now}


def _save_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f'{SESSIONS_FILE}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(sessions, f)
    os.replace(tmp, SESSIONS_FILE)


def _new_session(user: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        sessions = _load_sessions()
        sessions[_key(token)] = {'user': user, 'role': role,
                                 'expires_at': (_now() + timedelta(days=SESSION_DAYS)).isoformat()}
        _save_sessions(sessions)
    return token


def current() -> Optional[Dict[str, Any]]:
    token = request.cookies.get(COOKIE, '')
    if not token:
        return None
    with _lock:
        sessions = _load_sessions()
        session = sessions.get(_key(token))
        if not session:
            return None
        # People keep using Files: renew at most once an hour.
        renewed = (_now() + timedelta(days=SESSION_DAYS)).isoformat()
        if renewed[:13] != str(session.get('expires_at'))[:13]:
            session['expires_at'] = renewed
            _save_sessions(sessions)
        return dict(session)


def end_sessions_for(user: str) -> None:
    with _lock:
        sessions = _load_sessions()
        _save_sessions({k: v for k, v in sessions.items() if v.get('user') != user})


# ── Who may do what ──────────────────────────────────────────────────────────

def shares_for(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The shares this person can open, with 'read' or 'write', as over SMB."""
    result = {}
    for share in _read_json(SHARES_FILE).values():
        if not isinstance(share, dict) or not share.get('name') or not str(share.get('path', '')).startswith('/'):
            continue
        if session['role'] == 'admin':
            access = 'write'
        else:
            if share.get('protocol', 'smb') != 'smb':
                continue
            perms = share.get('smb_permissions')
            perms = perms if isinstance(perms, dict) else {}
            access = perms.get(session['user']) or ('write' if share.get('guest_access') else '')
            if access not in ('read', 'write'):
                continue
            if share.get('read_only'):
                access = 'read'
        result[share['name']] = {'name': share['name'], 'path': share['path'], 'access': access}
    return result


def as_user(session: Dict[str, Any]) -> Optional[str]:
    """The account the helper acts as: the person, or root for the admin."""
    return None if session['role'] == 'admin' else session['user']


def need_session():
    session = current()
    if not session:
        return None, (jsonify({'error': 'Please sign in.', 'signed_out': True}), 401)
    if request.method != 'GET' and request.headers.get('X-AlvaOS-Files') != '1':
        # Only the Files page sends this header; another website cannot (no CORS).
        return None, (jsonify({'error': 'Request refused.'}), 403)
    return session, None


def target(session: Dict[str, Any], data: Dict[str, Any], write: bool = False):
    """(share, absolute folder, relative folder, error response)."""
    shares = shares_for(session)
    name = str(data.get('share') or '')
    share = shares.get(name)
    if not share:
        return None, None, '', (jsonify({'error': 'This shared folder is not yours to open.'}), 404)
    if write and share['access'] != 'write':
        return None, None, '', (jsonify({'error': f'You can only look at "{name}", not change it.'}), 403)
    path, rel, error = files_manager.resolve({'s': share}, name, str(data.get('path') or ''))
    if error or path is None:
        return None, None, '', (jsonify({'error': error or 'Invalid path.'}), 404)
    return share, path, rel, None


# ── Sign in ──────────────────────────────────────────────────────────────────

def _limited(ip: str) -> bool:
    now = time.time()
    entry = _attempts.get(ip)
    if not entry or now - entry['start'] > LOGIN_WINDOW_SECONDS:
        entry = _attempts[ip] = {'start': now, 'count': 0}
    entry['count'] += 1
    return entry['count'] > LOGIN_MAX_ATTEMPTS


def check_login(username: str, password: str, code: str) -> Tuple[Optional[Tuple[str, str]], str, bool]:
    """((user, role), error, needs_code)."""
    if username == 'admin':
        auth = _read_json(AUTH_FILE)
        ok, _ = verify_password(password, auth)
        if not ok:
            return None, 'That name or password is not right.', False
        secret = auth.get('totp_secret')
        if secret:
            if not code:
                return None, '', True
            try:
                import pyotp
                if not pyotp.TOTP(secret).verify(code, valid_window=1):
                    return None, 'That code is not right.', True
            except ImportError:
                return None, 'Two-step sign-in is not available.', True
        return ('admin', 'admin'), '', False
    user = _read_json(USERS_FILE).get(username)
    if not isinstance(user, dict):
        return None, 'That name or password is not right.', False
    if not user.get('files_auth'):
        return None, ('Your account is not ready for AlvaOS Files yet. Ask whoever runs this NAS to set your '
                      'password once more (Storage › Users).'), False
    ok, _ = verify_password(password, user['files_auth'])
    if not ok:
        return None, 'That name or password is not right.', False
    return (username, 'user'), '', False


@app.post('/api/login')
def login():
    if _limited(request.remote_addr or ''):
        return jsonify({'error': 'Too many attempts. Wait a few minutes.'}), 429
    data = request.get_json(silent=True) or {}
    who, error, needs_code = check_login(str(data.get('username') or '').strip().lower(),
                                         str(data.get('password') or ''), str(data.get('code') or '').strip())
    if needs_code and not error:
        return jsonify({'needs_code': True}), 401
    if not who:
        return jsonify({'error': error, 'needs_code': needs_code}), 401
    _attempts.pop(request.remote_addr or '', None)
    token = _new_session(*who)
    response = jsonify({'success': True, 'user': who[0]})
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite='Strict',
                        secure=request.is_secure, path='/')
    return response


@app.post('/api/logout')
def logout():
    token = request.cookies.get(COOKIE, '')
    if token:
        with _lock:
            sessions = _load_sessions()
            sessions.pop(_key(token), None)
            _save_sessions(sessions)
    response = jsonify({'success': True})
    response.delete_cookie(COOKIE, path='/')
    return response


@app.get('/api/me')
def me():
    session, refused = need_session()
    if refused:
        return refused
    shares = sorted(({'name': s['name'], 'access': s['access']} for s in shares_for(session).values()),
                    key=lambda s: s['name'].lower())
    nas = socket.gethostname().split('.')[0]
    return jsonify({'user': session['user'], 'role': session['role'], 'shares': shares,
                    'nas_name': nas, 'upload_limit_bytes': UPLOAD_LIMIT_BYTES})


# ── Browsing and files ───────────────────────────────────────────────────────

def _helper_answer(result, error, status=200, **extra):
    if result is None:
        return jsonify({'error': error}), 409
    return jsonify({'success': True, **result, **extra}), status


@app.get('/api/list')
def list_folder():
    session, refused = need_session()
    if refused:
        return refused
    share, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    entries, error = files_manager.list_entries(path, user=as_user(session))
    if entries is None:
        return jsonify({'error': error}), 404
    return jsonify({'share': share['name'], 'access': share['access'], 'path': rel, 'entries': entries})


@app.post('/api/link')
def link():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, rel, bad = target(session, data)
    if bad:
        return bad
    if not rel:
        return jsonify({'error': 'Choose a file.'}), 400
    token = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for t in [t for t, v in _links.items() if v['expires'] < now]:
            del _links[t]
        _links[token] = {'path': path, 'name': os.path.basename(rel), 'user': as_user(session),
                         'inline': bool(data.get('inline')), 'expires': now + LINK_TTL_SECONDS}
    return jsonify({'url': f'/api/get/{token}', 'expires_in': LINK_TTL_SECONDS})


@app.get('/api/get/<token>')
def get_file(token):
    """The unguessable link (10 minutes, one file) is the permission, so the
    browser can show and download big files itself."""
    with _lock:
        item = dict(_links.get(token) or {})
    if not item or item['expires'] < time.time():
        return jsonify({'error': 'This link has expired. Open the file again.'}), 404
    if 'size' not in item:
        item['size'] = files_manager.file_size(item['path'], user=item['user'])
        with _lock:
            if token in _links:
                _links[token]['size'] = item['size']
    size = item['size']
    part, ok = files_manager.parse_range(request.headers.get('Range'), size or 0) if size is not None else (None, True)
    if not ok:
        return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
    stream, error = files_manager.open_stream(item['path'], part=part, user=item['user'])
    if stream is None:
        return jsonify({'error': error or 'The file could not be read.'}), 404
    mime, inline = files_manager.content_type(item['name'], item['inline'])
    headers = {
        'Content-Disposition': f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(item['name'])}",
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                                   "style-src 'unsafe-inline'" + ('' if mime == 'application/pdf' else '; sandbox'),
        'Cache-Control': 'private, max-age=600',
        'Referrer-Policy': 'no-referrer',
    }
    if size is not None:
        headers['Accept-Ranges'] = 'bytes'
        headers['Content-Length'] = str(part[1] if part else size)
    if part:
        headers['Content-Range'] = f'bytes {part[0]}-{part[0] + part[1] - 1}/{size}'
    return Response(stream, status=206 if part else 200, mimetype=mime, headers=headers, direct_passthrough=True)


THUMB_DIR = os.path.join(STATE_DIR, 'thumbs')
THUMB_SIZE = 320
THUMB_MAX_SOURCE_BYTES = 60 * 1024 ** 2
THUMB_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')


def make_thumbnail(data: bytes) -> Optional[bytes]:
    """A small JPEG. Decoded here, in this unprivileged process, never in the
    helper; Pillow's pixel limit stops decompression bombs."""
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        with Image.open(BytesIO(data)) as source:
            img: Image.Image = ImageOps.exif_transpose(source)
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            out = BytesIO()
            img.save(out, 'JPEG', quality=80)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - any broken image just has no thumbnail
        return None


@app.get('/api/thumb')
def thumbnail():
    session, refused = need_session()
    if refused:
        return refused
    _, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    if not rel or not rel.lower().endswith(THUMB_TYPES):
        return jsonify({'error': 'No preview for this file.'}), 404
    stamp = str(request.args.get('v') or '')
    key = hashlib.sha256(f'{as_user(session)}|{path}|{stamp}'.encode()).hexdigest()
    cached = os.path.join(THUMB_DIR, key[:2], key + '.jpg')
    headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'}
    try:
        with open(cached, 'rb') as f:
            return Response(f.read(), mimetype='image/jpeg', headers=headers)
    except OSError:
        pass
    size = files_manager.file_size(path, user=as_user(session))
    if size is None or size > THUMB_MAX_SOURCE_BYTES:
        return jsonify({'error': 'No preview for this file.'}), 404
    stream, _ = files_manager.open_stream(path, user=as_user(session))
    if stream is None:
        return jsonify({'error': 'No preview for this file.'}), 404
    thumb = make_thumbnail(b''.join(stream))
    if thumb is None:
        return jsonify({'error': 'No preview for this file.'}), 404
    try:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached + '.tmp', 'wb') as f:
            f.write(thumb)
        os.replace(cached + '.tmp', cached)
    except OSError:
        pass
    return Response(thumb, mimetype='image/jpeg', headers=headers)


@app.post('/api/upload')
def upload():
    session, refused = need_session()
    if refused:
        return refused
    _, path, _, bad = target(session, request.args, write=True)
    if bad:
        return bad
    if (request.content_length or 0) > UPLOAD_LIMIT_BYTES:
        return jsonify({'error': 'Files over 4 GB go through the shared folder on your computer.'}), 413
    result, error = files_manager.upload(path, str(request.args.get('name') or ''), request.stream,
                                         user=as_user(session))
    return _helper_answer(result, error, 201)


@app.post('/api/mkdir')
def mkdir():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-mkdir', path, str(data.get('name') or '')],
                                                    user=as_user(session)))


@app.post('/api/rename')
def rename():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(
        ['files-rename', path, str(data.get('old') or ''), str(data.get('new') or '')], user=as_user(session)))


@app.post('/api/delete')
def delete():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    names = data.get('names') if isinstance(data.get('names'), list) else []
    if not names or len(names) > 500:
        return jsonify({'error': 'Choose what to delete.'}), 400
    moved: List[str] = []
    failed: List[str] = []
    for name in names:
        result, problem = files_manager.run_helper(['files-trash', share['path'], path, str(name)],
                                                   user=as_user(session))
        if result is not None:
            moved.append(str(name))
        else:
            failed.append(f'{name}: {problem}')
    if not moved:
        return jsonify({'error': '; '.join(failed) or 'Nothing was deleted.'}), 409
    return jsonify({'success': True, 'moved': moved, 'failed': failed})


@app.get('/api/trash')
def trash():
    session, refused = need_session()
    if refused:
        return refused
    share, _, _, bad = target(session, {'share': request.args.get('share'), 'path': ''})
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-trash-list', share['path']], user=as_user(session)),
                          keep_days=30)


@app.post('/api/trash/restore')
def trash_restore():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, _, _, bad = target(session, {'share': data.get('share'), 'path': ''}, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-trash-restore', share['path'], str(data.get('id') or '')],
                                                    user=as_user(session)))


@app.post('/api/trash/empty')
def trash_empty():
    session, refused = need_session()
    if refused:
        return refused
    if session['role'] != 'admin':
        return jsonify({'error': 'Only the admin can empty a trash. Items go by themselves after 30 days.'}), 403
    data = request.get_json(silent=True) or {}
    share, _, _, bad = target(session, {'share': data.get('share'), 'path': ''}, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-trash-purge', share['path'], '0']))


# ── The app itself ───────────────────────────────────────────────────────────

@app.get('/')
def index():
    return send_from_directory(APP_ROOT, 'index.html')


@app.get('/<path:name>')
def asset(name):
    if name.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(APP_ROOT, name)


@app.after_request
def headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'")
    return response


def main() -> None:
    try:
        import waitress
    except ImportError:
        app.run(host='0.0.0.0', port=PORT)
        return
    print(f'AlvaOS Files on port {PORT}')
    waitress.serve(app, host='0.0.0.0', port=PORT, threads=8, ident='AlvaOS Files',
                   max_request_body_size=UPLOAD_LIMIT_BYTES)


if __name__ == '__main__':
    main()
