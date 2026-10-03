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
                    'nas_name': nas})


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


@app.get('/api/search')
def search():
    """Names below a folder of one share, or in every share of the person
    (everywhere=1), always as the person."""
    session, refused = need_session()
    if refused:
        return refused
    query = str(request.args.get('q') or '').strip()
    if not query or len(query) > 200:
        return jsonify({'error': 'Type what to look for.'}), 400
    if request.args.get('everywhere') == '1':
        places = [(s, s['path'], '') for _, s in sorted(shares_for(session).items())]
    else:
        share, path, rel, bad = target(session, request.args)
        if bad:
            return bad
        places = [(share, path, rel)]
    results: List[Dict[str, Any]] = []
    complete = True
    for share, path, rel in places:
        if len(results) >= SEARCH_LIMIT:
            complete = False
            break
        result, error = files_manager.run_helper(['files-search', path, query], timeout=30, user=as_user(session))
        if result is None:
            if len(places) == 1:
                return jsonify({'error': error or 'The search did not work.'}), 409
            continue
        complete = complete and bool(result.get('complete'))
        for item in result.get('results') or []:
            folder = '/'.join(p for p in (rel, str(item.get('folder') or '')) if p)
            results.append({**item, 'folder': folder, 'share': share['name']})
    if len(results) > SEARCH_LIMIT:
        results, complete = results[:SEARCH_LIMIT], False
    return jsonify({'path': places[0][2] if len(places) == 1 else '', 'query': query, 'results': results,
                    'complete': complete})


@app.get('/api/media')
def media():
    """Photos and videos of one share (or a folder of it), newest first, as
    the person: the Photos view."""
    session, refused = need_session()
    if refused:
        return refused
    share, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    result, error = files_manager.run_helper(['files-media', path], timeout=60, user=as_user(session))
    if result is None:
        return jsonify({'error': error or 'The photos could not be read.'}), 409
    results = [{**item, 'folder': '/'.join(p for p in (rel, str(item.get('folder') or '')) if p),
                'share': share['name']} for item in result.get('results') or []]
    return jsonify({'share': share['name'], 'path': rel, 'results': results,
                    'complete': bool(result.get('complete'))})


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


# ── Previous versions (from restore points) ─────────────────────────────────

SNAPSHOTS_FILE = os.path.join(STATE_DIR, 'backup_snapshots.json')
MAX_VERSION_POINTS = 100


def _restore_points_for(file_path: str) -> List[Tuple[Dict[str, Any], str]]:
    """(restore point, folder in it that held the file), newest first."""
    try:
        with open(SNAPSHOTS_FILE) as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or (entry.get('snapshot_class') or 'data') not in ('data', 'full_data'):
            continue
        source = os.path.normpath(str(entry.get('source_path') or '/'))
        snap = os.path.normpath(str(entry.get('snapshot_path') or ''))
        if not snap.startswith('/mnt/') or not file_path.startswith(source.rstrip('/') + '/'):
            continue
        inner = os.path.relpath(os.path.dirname(file_path), source)
        found.append((entry, snap if inner == '.' else os.path.join(snap, inner)))
    found.sort(key=lambda item: str(item[0].get('created_at') or ''), reverse=True)
    return found[:MAX_VERSION_POINTS]


def _point_id(entry: Dict[str, Any]) -> str:
    return hashlib.sha256(str(entry.get('snapshot_path') or '').encode()).hexdigest()[:16]


def _versions(session, data):
    """(share, file path, versions newest first, error response)."""
    share, path, rel, bad = target(session, data)
    if bad:
        return None, None, [], bad
    if not rel:
        return None, None, [], (jsonify({'error': 'Choose a file.'}), 400)
    points = _restore_points_for(path)
    name = os.path.basename(path)
    if not points:
        return share, path, [], None
    result, error = files_manager.run_helper(
        ['files-versions', name, os.path.dirname(path)] + [folder for _, folder in points],
        timeout=60, user=as_user(session))
    if result is None:
        return None, None, [], (jsonify({'error': error or 'The restore points could not be read.'}), 409)
    found = result.get('versions') or []
    current = found[0] if found else None
    seen = {(current['size_bytes'], current['modified_at'])} if current else set()
    versions = []
    for (entry, folder), item in zip(points, found[1:], strict=False):
        if not item or (item['size_bytes'], item['modified_at']) in seen:
            continue
        seen.add((item['size_bytes'], item['modified_at']))
        versions.append({'id': _point_id(entry), 'created_at': entry.get('created_at'), 'folder': folder,
                         'size_bytes': item['size_bytes'], 'modified_at': item['modified_at']})
    return share, path, versions, None


@app.get('/api/versions')
def list_versions():
    session, refused = need_session()
    if refused:
        return refused
    _, _, versions, bad = _versions(session, request.args)
    if bad:
        return bad
    return jsonify({'versions': [{k: v for k, v in item.items() if k != 'folder'} for item in versions]})


def _chosen_version(session, data):
    share, path, versions, bad = _versions(session, data)
    if bad:
        return None, None, None, bad
    chosen = next((v for v in versions if v['id'] == str(data.get('id') or '')), None)
    if not chosen:
        return None, None, None, (jsonify({'error': 'This version is not there any more.'}), 404)
    return share, path, chosen, None


@app.post('/api/versions/link')
def version_link():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, chosen, bad = _chosen_version(session, data)
    if bad:
        return bad
    name = os.path.basename(path)
    token = secrets.token_urlsafe(32)
    with _lock:
        _links[token] = {'path': os.path.join(chosen['folder'], name), 'name': name, 'user': as_user(session),
                         'inline': bool(data.get('inline')), 'expires': time.time() + LINK_TTL_SECONDS}
    return jsonify({'url': f'/api/get/{token}', 'expires_in': LINK_TTL_SECONDS})


def restored_name(name: str, when: datetime) -> str:
    """'plan.txt' -> 'plan (restored 2026-10-01 0300).txt' (as on the Backup page)."""
    stem, ext = os.path.splitext(name)
    if not stem:
        stem, ext = name, ''
    return f"{stem} (restored {when.strftime('%Y-%m-%d %H%M')}){ext}"


@app.post('/api/versions/restore')
def version_restore():
    """The old version is copied next to the file; nothing is overwritten."""
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, path, chosen, bad = _chosen_version(session, data)
    if bad:
        return bad
    if share['access'] != 'write':
        return jsonify({'error': f'You can only look at "{share["name"]}", not change it.'}), 403
    try:
        when = datetime.fromisoformat(str(chosen['created_at'])).astimezone()
    except ValueError:
        when = datetime.now().astimezone()
    name = os.path.basename(path)
    result, error = files_manager.run_helper(
        ['files-restore-version', chosen['folder'], name, os.path.dirname(path), restored_name(name, when)],
        timeout=3600, user=as_user(session))
    if result is None:
        return jsonify({'error': error or 'The old version could not be restored.'}), 409
    return jsonify({'success': True, 'name': result.get('name')})


SEARCH_LIMIT = 200
THUMB_DIR = os.path.join(STATE_DIR, 'thumbs')
THUMB_SIZE = 320
THUMB_MAX_SOURCE_BYTES = 60 * 1024 ** 2
THUMB_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')


def _low_priority() -> None:
    """Runs in each thumbnail worker: decoding photos must not slow down
    people copying files (on Linux nice applies per thread)."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
        pass


_thumb_workers = None


def _thumbnail_pool():
    global _thumb_workers
    if _thumb_workers is None:
        from concurrent.futures import ThreadPoolExecutor
        _thumb_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='thumbs', initializer=_low_priority)
    return _thumb_workers


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
    return _thumb_response(path, as_user(session), str(request.args.get('v') or ''))


def _thumb_response(path: str, user: Optional[str], stamp: str):
    key = hashlib.sha256(f'{user}|{path}|{stamp}'.encode()).hexdigest()
    cached = os.path.join(THUMB_DIR, key[:2], key + '.jpg')
    headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'}
    try:
        with open(cached, 'rb') as f:
            return Response(f.read(), mimetype='image/jpeg', headers=headers)
    except OSError:
        pass
    size = files_manager.file_size(path, user=user)
    if size is None or size > THUMB_MAX_SOURCE_BYTES:
        return jsonify({'error': 'No preview for this file.'}), 404
    stream, _ = files_manager.open_stream(path, user=user)
    if stream is None:
        return jsonify({'error': 'No preview for this file.'}), 404
    thumb = _thumbnail_pool().submit(make_thumbnail, b''.join(stream)).result(timeout=60)
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


def _zip_response(stream, name: str):
    return Response(stream, mimetype='application/zip', direct_passthrough=True, headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name + '.zip')}",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@app.get('/api/zip')
def zip_folder():
    """A folder (or a whole share) as one ZIP download, made while it downloads."""
    session, refused = need_session()
    if refused:
        return refused
    share, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    stream, error = files_manager.open_zip(path, user=as_user(session))
    if stream is None:
        return jsonify({'error': error or 'This folder could not be read.'}), 404
    return _zip_response(stream, os.path.basename(rel) if rel else share['name'])


# Uploads arrive in pieces of up to PIECE_LIMIT_BYTES, so big files work, a
# dropped connection continues where it stopped, and the web server never
# holds more than one piece on the system disk.
PIECE_LIMIT_BYTES = 64 * 1024 ** 2


@app.get('/api/upload/status')
def upload_status():
    session, refused = need_session()
    if refused:
        return refused
    _, path, _, bad = target(session, request.args, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-part-size', path, str(request.args.get('name') or '')],
                                                    user=as_user(session)))


@app.post('/api/upload/piece')
def upload_piece():
    session, refused = need_session()
    if refused:
        return refused
    _, path, _, bad = target(session, request.args, write=True)
    if bad:
        return bad
    if (request.content_length or 0) > PIECE_LIMIT_BYTES:
        return jsonify({'error': 'Pieces are at most 64 MB.'}), 413
    offset = str(request.args.get('offset') or '0')
    if not offset.isdigit():
        return jsonify({'error': 'Invalid offset.'}), 400
    return _helper_answer(*files_manager.pipe_helper(
        ['files-part-write', path, str(request.args.get('name') or ''), offset], request.stream, user=as_user(session)))


@app.post('/api/upload/finish')
def upload_finish():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    size = str(data.get('size') if data.get('size') is not None else '')
    if not size.isdigit():
        return jsonify({'error': 'Invalid size.'}), 400
    return _helper_answer(*files_manager.run_helper(_finish_args(path, data, size), user=as_user(session)), 201)


def _finish_args(path: str, data: Dict[str, Any], size: str) -> List[str]:
    """files-part-finish, with the file's own date when the browser sent it."""
    name = str(data.get('name') or '')
    try:
        mtime = int(float(data.get('modified') or 0) / 1000)   # JavaScript File.lastModified, in ms
    except (TypeError, ValueError):
        mtime = 0
    if mtime > 0:
        return ['files-part-finish-dated', path, name, size, str(mtime)]
    return ['files-part-finish', path, name, size]


@app.post('/api/upload/abort')
def upload_abort():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    _, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    return _helper_answer(*files_manager.run_helper(['files-part-abort', path, str(data.get('name') or '')],
                                                    user=as_user(session)))


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


@app.post('/api/copy')
def copy_items():
    """Copy items into a folder of the same share."""
    return _move_or_copy('files-copy')


@app.post('/api/move')
def move():
    """Move items to another folder of the same share."""
    return _move_or_copy('files-move')


def _move_or_copy(operation: str):
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, path, _, bad = target(session, data, write=True)
    if bad:
        return bad
    _, dest, _, bad = target(session, {'share': share['name'], 'path': data.get('to')}, write=True)
    if bad:
        return bad
    names = data.get('names') if isinstance(data.get('names'), list) else []
    if not names or len(names) > 500:
        return jsonify({'error': 'Choose what to move.'}), 400
    moved: List[str] = []
    failed: List[str] = []
    for name in names:
        result, problem = files_manager.run_helper([operation, path, str(name), dest], user=as_user(session),
                                                   timeout=3600)
        if result is not None:
            moved.append(str(name))
        else:
            failed.append(f'{name}: {problem}')
    if not moved:
        return jsonify({'error': '; '.join(failed) or 'Nothing was done.'}), 409
    return jsonify({'success': True, 'moved': moved, 'failed': failed})


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


# ── Share links ──────────────────────────────────────────────────────────────
# A link to a file or folder that anyone with it can open, read-only, as the
# person who made it (so it never shows more than they may see). Optional
# password and expiry; every link is listed and can be removed.

LINKS_FILE = os.path.join(STATE_DIR, 'files_links.json')
SECRET_FILE = os.path.join(STATE_DIR, 'files_secret')
LINK_DAYS = (0, 1, 7, 30, 90)
DROP_LIMITS_GB = (0, 1, 5, 20, 100)   # how much an upload link takes in all; 0 = up to the share's space


def _secret() -> bytes:
    try:
        with open(SECRET_FILE, 'rb') as f:
            data = f.read()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    data = secrets.token_bytes(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return data


def _load_links() -> Dict[str, Dict[str, Any]]:
    links = _read_json(LINKS_FILE)
    now = _now().isoformat()
    return {k: v for k, v in links.items() if isinstance(v, dict) and (not v.get('expires_at') or v['expires_at'] > now)}


def _save_links(links: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f'{LINKS_FILE}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(links, f, indent=1)
    os.replace(tmp, LINKS_FILE)


def _public_link(link: Dict[str, Any], token: str) -> Dict[str, Any]:
    return {'id': link['id'], 'url': f'/s/{token}', 'share': link['share'], 'path': link['path'],
            'name': link['name'], 'kind': link['kind'], 'mode': link.get('mode', 'view'),
            'max_bytes': link.get('max_bytes'), 'received': link.get('received', 0), 'owner': link['owner'],
            'created_at': link['created_at'], 'expires_at': link.get('expires_at'),
            'has_password': bool(link.get('password'))}


@app.post('/api/links')
def create_link():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, path, rel, bad = target(session, data)
    if bad:
        return bad
    if not rel:
        return jsonify({'error': 'Share a folder or file inside the shared folder, not all of it.'}), 400
    try:
        days = int(data.get('days', 7))
    except (TypeError, ValueError):
        days = -1
    if days not in LINK_DAYS:
        return jsonify({'error': 'Choose how long the link works.'}), 400
    password = str(data.get('password') or '')
    if password and len(password) < 4:
        return jsonify({'error': 'Use at least 4 characters for the link password.'}), 400
    kind = 'folder' if data.get('kind') == 'folder' else 'file'
    mode = 'upload' if data.get('mode') == 'upload' else 'view'
    if mode == 'upload' and kind != 'folder':
        return jsonify({'error': 'Upload links are for folders.'}), 400
    if mode == 'upload' and share['access'] != 'write':
        return jsonify({'error': f'You can only look at "{share["name"]}", so nobody can upload there through you.'}), 403
    try:
        max_gb = int(data.get('max_gb') or 0) if mode == 'upload' else 0
    except (TypeError, ValueError):
        max_gb = -1
    if max_gb not in DROP_LIMITS_GB:
        return jsonify({'error': 'Choose how much people may upload.'}), 400
    from password_utils import hash_password
    token = secrets.token_urlsafe(18)
    link = {'id': secrets.token_hex(6), 'share': share['name'], 'path': rel, 'name': os.path.basename(rel),
            'kind': kind, 'mode': mode, 'max_bytes': max_gb * 1024 ** 3 if max_gb else None, 'received': 0,
            'owner': session['user'], 'role': session['role'], 'created_at': _now().isoformat(),
            'expires_at': (_now() + timedelta(days=days)).isoformat() if days else None,
            'password': hash_password(password) if password else None}
    with _lock:
        links = _load_links()
        links[token] = link
        _save_links(links)
    return jsonify({'success': True, **_public_link(link, token)}), 201


@app.get('/api/links')
def list_links():
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        links = _load_links()
    mine = [_public_link(v, k) for k, v in links.items()
            if session['role'] == 'admin' or v.get('owner') == session['user']]
    return jsonify({'links': sorted(mine, key=lambda x: x['created_at'], reverse=True)})


@app.post('/api/links/<link_id>/delete')
def delete_link(link_id):
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        links = _load_links()
        token = next((k for k, v in links.items() if v.get('id') == link_id), None)
        if token is None:
            return jsonify({'error': 'This link does not exist any more.'}), 404
        if session['role'] != 'admin' and links[token].get('owner') != session['user']:
            return jsonify({'error': 'Only the person who made a link can remove it.'}), 403
        del links[token]
        _save_links(links)
    return jsonify({'success': True})


def _unlock_value(token: str) -> str:
    import hmac
    return hmac.new(_secret(), token.encode(), hashlib.sha256).hexdigest()


def opened_link(token: str, upload: bool = False):
    """(link, base path, owner account, error response) for a visitor.
    Upload links ("drop box") only take files; view links only give them."""
    with _lock:
        link = _load_links().get(token)
    if not link:
        return None, None, None, (jsonify({'error': 'This link does not work any more. It may have expired '
                                                    'or been removed.'}), 404)
    if link.get('password'):
        import hmac
        if not hmac.compare_digest(request.cookies.get(f'alvaos_link_{link["id"]}', ''), _unlock_value(token)):
            return link, None, None, (jsonify({'error': 'This link needs a password.', 'needs_password': True}), 401)
    if upload != (link.get('mode') == 'upload'):
        return None, None, None, (jsonify({'error': 'This link does not allow that.'}), 403)
    owner = {'user': link['owner'], 'role': link.get('role', 'user')}
    share = shares_for(owner).get(link['share'])
    if not share or (upload and share['access'] != 'write'):
        return None, None, None, (jsonify({'error': 'This link does not work any more.'}), 404)
    base, _, error = files_manager.resolve({'s': share}, share['name'], link['path'])
    if error or base is None:
        return None, None, None, (jsonify({'error': 'This link does not work any more.'}), 404)
    return link, base, as_user(owner), None


def _inside_link(link: Dict[str, Any], base: str, sub: str):
    """The path a visitor asks for, inside the link's folder (or the file itself)."""
    if link['kind'] == 'file':
        return (base, link['name']) if not sub else (None, '')
    rel = files_manager.clean_relative_path(sub)
    if rel is None:
        return None, ''
    return (os.path.join(base, rel) if rel else base), rel


@app.get('/api/public/<token>')
def public_info(token):
    with _lock:
        stored = _load_links().get(token) or {}
    link, _, _, bad = opened_link(token, upload=stored.get('mode') == 'upload')
    if bad:
        return bad
    room = None if not link.get('max_bytes') else max(0, int(link['max_bytes']) - int(link.get('received') or 0))
    return jsonify({'name': link['name'], 'kind': link['kind'], 'mode': link.get('mode', 'view'), 'room_bytes': room,
                    'owner': link['owner'],
                    'expires_at': link.get('expires_at'), 'nas_name': socket.gethostname().split('.')[0]})


@app.post('/api/public/<token>/unlock')
def public_unlock(token):
    if _limited(request.remote_addr or ''):
        return jsonify({'error': 'Too many attempts. Wait a few minutes.'}), 429
    with _lock:
        link = _load_links().get(token)
    if not link or not link.get('password'):
        return jsonify({'error': 'This link does not work any more.'}), 404
    ok, _ = verify_password(str((request.get_json(silent=True) or {}).get('password') or ''), link['password'])
    if not ok:
        return jsonify({'error': 'That password is not right.'}), 401
    response = jsonify({'success': True})
    response.set_cookie(f'alvaos_link_{link["id"]}', _unlock_value(token), max_age=7 * 86400, httponly=True,
                        samesite='Lax', secure=request.is_secure, path='/')
    return response


@app.get('/api/public/<token>/list')
def public_list(token):
    link, base, owner, bad = opened_link(token)
    if bad:
        return bad
    if link['kind'] != 'folder':
        return jsonify({'error': 'This link is a file.'}), 400
    path, rel = _inside_link(link, base, request.args.get('path', ''))
    if path is None:
        return jsonify({'error': 'Invalid path.'}), 404
    entries, error = files_manager.list_entries(path, user=owner)
    if entries is None:
        return jsonify({'error': error}), 404
    return jsonify({'path': rel, 'entries': [e for e in entries if not str(e.get('name', '')).startswith('.')]})


def _public_stream(token: str, inline: bool):
    link, base, owner, bad = opened_link(token)
    if bad:
        return bad
    sub = request.args.get('path', '')
    path, rel = _inside_link(link, base, sub)
    if path is None or (link['kind'] == 'folder' and not rel):
        return jsonify({'error': 'Choose a file.'}), 404
    name = os.path.basename(path)
    size = files_manager.file_size(path, user=owner)
    part, ok = files_manager.parse_range(request.headers.get('Range'), size or 0) if size is not None else (None, True)
    if not ok:
        return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
    stream, error = files_manager.open_stream(path, part=part, user=owner)
    if stream is None:
        return jsonify({'error': error or 'The file could not be read.'}), 404
    mime, shown_inline = files_manager.content_type(name, inline)
    headers = {
        'Content-Disposition': f"{'inline' if shown_inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'private, max-age=600',
        'Content-Security-Policy': "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                                   "style-src 'unsafe-inline'" + ('' if mime == 'application/pdf' else '; sandbox'),
    }
    if size is not None:
        headers['Accept-Ranges'] = 'bytes'
        headers['Content-Length'] = str(part[1] if part else size)
    if part:
        headers['Content-Range'] = f'bytes {part[0]}-{part[0] + part[1] - 1}/{size}'
    return Response(stream, status=206 if part else 200, mimetype=mime, headers=headers, direct_passthrough=True)


@app.get('/api/public/<token>/file')
def public_file(token):
    return _public_stream(token, request.args.get('inline') == '1')


@app.get('/api/public/<token>/thumb')
def public_thumb(token):
    link, base, owner, bad = opened_link(token)
    if bad:
        return bad
    path, rel = _inside_link(link, base, request.args.get('path', ''))
    if path is None or not path.lower().endswith(THUMB_TYPES):
        return jsonify({'error': 'No preview.'}), 404
    return _thumb_response(path, owner, str(request.args.get('v') or ''))


@app.get('/api/public/<token>/zip')
def public_zip(token):
    link, base, owner, bad = opened_link(token)
    if bad:
        return bad
    if link['kind'] != 'folder':
        return jsonify({'error': 'This link is a file.'}), 400
    path, rel = _inside_link(link, base, request.args.get('path', ''))
    if path is None:
        return jsonify({'error': 'Invalid path.'}), 404
    stream, error = files_manager.open_zip(path, user=owner)
    if stream is None:
        return jsonify({'error': error or 'This folder could not be read.'}), 404
    return _zip_response(stream, os.path.basename(rel) if rel else link['name'])


# ── Upload links ("drop box"): visitors add files, see nothing ─────────────

def _drop_target(token: str):
    """(folder, owner, error) for a visitor's upload request."""
    if request.method != 'GET' and request.headers.get('X-AlvaOS-Files') != '1':
        return None, None, (jsonify({'error': 'Request refused.'}), 403)
    _, base, owner, bad = opened_link(token, upload=True)
    return base, owner, bad


def _free_upload_name(base: str, owner: Optional[str], name: str) -> Tuple[Optional[str], int, str]:
    """The name an upload is saved under, never over an existing file:
    "photo.jpg", then "photo (2).jpg", ... An unfinished upload with that name
    is continued. Returns (name, bytes already there, error)."""
    entries, error = files_manager.list_entries(base, user=owner)
    if entries is None:
        return None, 0, error or 'The folder could not be read.'
    taken = {str(e.get('name')) for e in entries}
    stem, ext = os.path.splitext(name)
    if not stem:
        stem, ext = name, ''
    for n in range(1, 1000):
        candidate = name if n == 1 else f'{stem} ({n}){ext}'
        if candidate in taken:
            continue
        result, error = files_manager.run_helper(['files-part-size', base, candidate], user=owner)
        if result is None:
            return None, 0, error
        return candidate, int(result.get('size') or 0), ''
    return None, 0, 'Too many files with this name.'


@app.post('/api/public/<token>/upload/start')
def public_upload_start(token):
    base, owner, bad = _drop_target(token)
    if bad:
        return bad
    name = str((request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name or '/' in name or '\x00' in name or name.startswith('.') or len(name.encode()) > 240:
        return jsonify({'error': 'This file name cannot be used.'}), 400
    if not _room_left(token, 1):
        return jsonify({'error': 'This link has taken all it may. Ask the person who sent it for more room.'}), 413
    chosen, size, error = _free_upload_name(base, owner, name)
    if chosen is None:
        return jsonify({'error': error}), 409
    return jsonify({'name': chosen, 'size': size})


def _room_left(token: str, adding: int) -> bool:
    """Whether an upload link with a size limit can take `adding` more bytes."""
    with _lock:
        link = _load_links().get(token) or {}
    limit = link.get('max_bytes')
    return not limit or int(link.get('received') or 0) + adding <= int(limit)


def _count_received(token: str, added: int) -> None:
    if added <= 0:
        return
    with _lock:
        links = _load_links()
        if token in links:
            links[token]['received'] = int(links[token].get('received') or 0) + added
            _save_links(links)


@app.post('/api/public/<token>/upload/piece')
def public_upload_piece(token):
    base, owner, bad = _drop_target(token)
    if bad:
        return bad
    length = request.content_length or 0
    if length > PIECE_LIMIT_BYTES:
        return jsonify({'error': 'Pieces are at most 64 MB.'}), 413
    offset = str(request.args.get('offset') or '0')
    if not offset.isdigit():
        return jsonify({'error': 'Invalid offset.'}), 400
    if not length or not _room_left(token, length):
        return jsonify({'error': 'This link has taken all it may. Ask the person who sent it for more room.'}), 413
    result, error = files_manager.pipe_helper(
        ['files-part-write', base, str(request.args.get('name') or ''), offset], request.stream, user=owner)
    if result is not None:
        _count_received(token, int(result.get('size') or 0) - int(offset))
    return _helper_answer(result, error)


@app.post('/api/public/<token>/upload/finish')
def public_upload_finish(token):
    base, owner, bad = _drop_target(token)
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    size = str(data.get('size') if data.get('size') is not None else '')
    if not size.isdigit():
        return jsonify({'error': 'Invalid size.'}), 400
    return _helper_answer(*files_manager.run_helper(_finish_args(base, data, size), user=owner), 201)


@app.get('/alvaos-ca.crt')
def authority_certificate():
    """The NAS's own authority, to trust on a phone (see Settings › Security)."""
    import tls_manager
    try:
        with open(tls_manager.paths()['ca.crt'], 'rb') as f:
            data = f.read()
    except OSError:
        return jsonify({'error': 'HTTPS is not set up yet.'}), 404
    return Response(data, mimetype='application/x-x509-ca-cert',
                    headers={'Content-Disposition': 'attachment; filename="alvaos.crt"'})


@app.get('/s/<token>')
def share_page(token):
    return send_from_directory(APP_ROOT, 'share.html')


# ── The app itself ───────────────────────────────────────────────────────────

@app.get('/')
def index():
    return send_from_directory(APP_ROOT, 'index.html')


@app.get('/<path:name>')
def asset(name):
    if name.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(APP_ROOT, name)


@app.before_request
def send_to_https():
    """While "HTTPS only" is on (Settings › Security), plain HTTP is
    redirected; the certificate stays reachable for new devices."""
    if (request.remote_addr or '') in ('127.0.0.1', '::1'):
        return None
    import tls_manager
    from flask import redirect
    target = tls_manager.redirect_to_https(request, tls_manager.PORTS['files'], keep=('/alvaos-ca.crt',))
    return redirect(target, code=308) if target else None


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
        import files_dav
        import tls_manager
        files_dav.serve_in_background()
        tls_manager.serve_in_background(app, tls_manager.PORTS['files'], 'AlvaOS Files')
        tls_manager.serve_in_background(files_dav.app, tls_manager.PORTS['dav'], 'AlvaOS Files WebDAV')
        app.run(host='0.0.0.0', port=PORT)
        return
    print(f'AlvaOS Files on port {PORT}')
    import files_dav
    import tls_manager
    files_dav.serve_in_background()
    tls_manager.serve_in_background(app, tls_manager.PORTS['files'], 'AlvaOS Files')
    tls_manager.serve_in_background(files_dav.app, tls_manager.PORTS['dav'], 'AlvaOS Files WebDAV')
    waitress.serve(app, host='0.0.0.0', port=PORT, threads=8, ident='AlvaOS Files',
                   max_request_body_size=PIECE_LIMIT_BYTES + 1024 * 1024)


if __name__ == '__main__':
    main()
