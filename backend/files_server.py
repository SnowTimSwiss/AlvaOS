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
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import Flask, Response, g, has_request_context, jsonify, request, send_from_directory

import files_manager
import hub_apps
import photo_dates
import hub_calendar
import hub_caldav
import hub_albums
import link_client
import hub_photos_sync
import hub_video
import hub_chat
import hub_contacts
import hub_data
from password_utils import verify_password

PORT = 8090
STATE_DIR = '/var/lib/alvaos'
USERS_FILE = os.path.join(STATE_DIR, 'users.json')
SHARES_FILE = os.path.join(STATE_DIR, 'shares.json')
POOLS_FILE = os.path.join(STATE_DIR, 'pools.json')
AUTH_FILE = os.path.join(STATE_DIR, 'auth.json')
SESSIONS_FILE = os.path.join(STATE_DIR, 'files_sessions.json')
SESSION_DAYS = 14
DEVICE_DAYS = 120            # a phone with the app stays signed in while it is used
PAIR_CODE_SECONDS = 10 * 60
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


def _password_tag(user: str, role: str) -> str:
    """A fingerprint of the stored password: when it changes (or the person
    is removed), their Files sessions end, also in browsers signed in before."""
    stored = _read_json(AUTH_FILE) if role == 'admin' else _read_json(USERS_FILE).get(user)
    if not isinstance(stored, dict):
        return ''
    value = stored if role == 'admin' else stored.get('files_auth')
    if not value:
        return ''
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def _new_session(user: str, role: str, device: Optional[Dict[str, Any]] = None) -> str:
    """A browser session, or with `device` the session of a phone with the
    AlvaOS app (listed under Devices in the Hub, and removable there)."""
    token = secrets.token_urlsafe(32)
    days = DEVICE_DAYS if device else SESSION_DAYS
    with _lock:
        sessions = _load_sessions()
        session: Dict[str, Any] = {'user': user, 'role': role, 'tag': _password_tag(user, role),
                                   'expires_at': (_now() + timedelta(days=days)).isoformat()}
        if device:
            session['device'] = device
        sessions[_key(token)] = session
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
        tag = _password_tag(session.get('user', ''), session.get('role', ''))
        if not tag or tag != session.get('tag'):
            del sessions[_key(token)]           # password changed or person removed
            _save_sessions(sessions)
            return None
        # People keep using Files: renew at most once an hour.
        device = session.get('device') if isinstance(session.get('device'), dict) else None
        renewed = (_now() + timedelta(days=DEVICE_DAYS if device else SESSION_DAYS)).isoformat()
        if renewed[:13] != str(session.get('expires_at'))[:13]:
            session['expires_at'] = renewed
            if device:
                device['last_seen'] = _now().isoformat()
                device['address'] = request.remote_addr or ''
            _save_sessions(sessions)
        return {**session, 'token_key': _key(token)}


def end_sessions_for(user: str) -> None:
    with _lock:
        sessions = _load_sessions()
        _save_sessions({k: v for k, v in sessions.items() if v.get('user') != user})


# ── Who may do what ──────────────────────────────────────────────────────────

def shares_for(session: Dict[str, Any], grants: bool = True) -> Dict[str, Dict[str, Any]]:
    """The shares this person can open, with 'read' or 'write', as over SMB,
    and the folders other people shared with them in the Hub (see _granted)."""
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
        result[share['name']] = {'name': share['name'], 'path': share['path'], 'access': access,
                                 'limit_bytes': share.get('quota_bytes') if isinstance(share.get('quota_bytes'), int) else None}
    if grants and session['role'] != 'admin':
        result.update(_granted(session, result))
    return result


# ── Folders shared with a person (Files › Share with people) ────────────────
#
# A person may give others on the NAS a folder they can open: to look at, or
# to change too. The others see it in Files (and WebDAV) as a folder of
# their own, "Holiday (from anna)". Nothing changes on disk: their file
# operations run as the person who shared it, below that folder only, and
# never with more rights than that person has. Over SMB it is not there.

def _grants_file() -> str:
    return os.path.join(STATE_DIR, 'files_grants.json')


def _load_grants() -> List[Dict[str, Any]]:
    grants = _read_json(_grants_file()).get('grants')
    return [x for x in grants or [] if isinstance(x, dict) and isinstance(x.get('to'), list)]


def _save_grants(grants: List[Dict[str, Any]]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = _grants_file() + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'grants': grants}, f, indent=1)
    os.replace(tmp, _grants_file())


def _granted(session: Dict[str, Any], own: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for grant in _load_grants():
        if session['user'] not in grant['to'] or grant.get('owner') == session['user']:
            continue
        owner = {'user': str(grant.get('owner')), 'role': 'admin' if grant.get('owner') == 'admin' else 'user'}
        source = shares_for(owner, grants=False).get(str(grant.get('share')))
        if not source:
            continue            # the person who shared it cannot open it any more
        access = 'write' if grant.get('access') == 'write' and source['access'] == 'write' else 'read'
        rel = str(grant.get('path') or '')
        base = rel.rsplit('/', 1)[-1] or source['name']
        name, n = f"{base} (from {owner['user']})", 2
        while name in own or name in out:
            name, n = f"{base} (from {owner['user']}) {n}", n + 1
        out[name] = {'name': name, 'path': os.path.join(source['path'], rel) if rel else source['path'],
                     'root': source['path'],      # its trash is the owner's share's
                     'access': access, 'limit_bytes': None, 'granted_by': owner['user'],
                     'act_as': as_user(owner, {}), 'grant': grant.get('id')}
    return out


_OWN = object()   # "not a shared-with-me folder": act as the person


def as_user(session: Dict[str, Any], share: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The account the helper acts as: the person, or root for the admin; in
    a folder someone shared with them, the one who shared it (_granted).
    Without `share`, the folder this request opened with target()."""
    if share is not None:
        acting = share.get('act_as', _OWN)
    else:
        acting = g.get('files_act_as', _OWN) if has_request_context() else _OWN
    if acting is not _OWN:
        return acting
    return None if session['role'] == 'admin' else session['user']


# Asked without any Hub app: who is signed in, which apps they see, their devices.
HUB_PATHS = ('/api/me',)
HUB_PREFIXES = ('/api/devices',)


def need_session(app_id: str = 'files'):
    """The signed-in person, if they may use this Hub app (Files unless said)."""
    session = current()
    if not session:
        return None, (jsonify({'error': 'Please sign in.', 'signed_out': True}), 401)
    if request.method != 'GET' and request.headers.get('X-AlvaOS-Files') != '1':
        # Only the Hub's pages send this header; another website cannot (no CORS).
        return None, (jsonify({'error': 'Request refused.'}), 403)
    if request.path not in HUB_PATHS and not request.path.startswith(HUB_PREFIXES) \
            and not hub_apps.allowed(app_id, session['user'], session['role']):
        # The admin may have turned the app off, or not for this person.
        name = next((a['name'] for a in hub_apps.APPS if a['id'] == app_id), app_id)
        return None, (jsonify({'error': f'{name} is not turned on for you. Ask the person who looks after the NAS.',
                               'app_off': True}), 403)
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
    g.files_act_as = share.get('act_as', _OWN)
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
    device = _device_from(data.get('device'))
    token = _new_session(*who, device=device)
    # The app keeps the session itself; a browser only gets the cookie.
    response = jsonify({'success': True, 'user': who[0], **({'token': token} if device else {})})
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
    settings = hub_apps.load()
    apps = hub_apps.visible(session['user'], session['role'], settings)
    files_on = any(a['id'] == 'files' for a in apps)
    shares = sorted(({'name': s['name'], 'access': s['access'], **({'limited': True} if s['limit_bytes'] else {}),
                      **({'from': s['granted_by']} if s.get('granted_by') else {})}
                     for s in shares_for(session).values()),
                    key=lambda s: s['name'].lower()) if files_on else []
    nas = socket.gethostname().split('.')[0]
    store = hub_apps.store_tiles(session['user'], session['role'], settings)
    return jsonify({'user': session['user'], 'role': session['role'], 'shares': shares,
                    'new_uploads': _new_uploads(session['user']),
                    'nas_name': nas, 'hub': {'name': hub_apps.NAME, 'apps': apps, 'store': store},
                    'features': hub_video.features()})


def _new_uploads(user: str) -> int:
    """Files that came into the person's drop boxes since they last looked at the list."""
    try:
        with _lock:
            links = _load_links()
    except Exception:  # noqa: BLE001 - a broken links file must not break /api/me
        return 0
    return sum(int(v.get('new_files') or 0) for v in links.values() if v.get('owner') == user)


# ── Devices: phones with the AlvaOS app ─────────────────────────────────────
#
# The app signs in once, with a QR code shown in the Hub (Devices › Connect a
# phone) or with name and password, and then has a session of its own. The
# Hub lists these sessions as the person's devices; removing one signs that
# phone out. A pairing code is short, used once, and only valid for minutes.

_pair_codes: Dict[str, Dict[str, Any]] = {}
PAIR_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'      # nothing to mix up (no 0/O, 1/I/L)
ADDRESS_RE = re.compile(r'^https?://[A-Za-z0-9.\-\[\]:]+$')


def _device_from(raw: Any) -> Optional[Dict[str, Any]]:
    """What the app says about itself; None for a browser."""
    if not isinstance(raw, dict):
        return None
    def text(key: str, limit: int) -> str:
        return re.sub(r'[\x00-\x1f\x7f]', '', str(raw.get(key) or ''))[:limit].strip()
    now = _now().isoformat()
    return {'id': secrets.token_hex(6), 'name': text('name', 60) or 'Phone', 'model': text('model', 60),
            'platform': text('platform', 20) or 'android', 'app_version': text('app_version', 30),
            'created_at': now, 'last_seen': now, 'address': request.remote_addr or ''}


def _addresses(origin: str) -> List[str]:
    """Where the app can reach the Hub at home: the address this browser uses. Away from
    home the app comes through AlvaOS Link (the NAS's Link address is in the QR code)."""
    url = origin.rstrip('/')
    return [url] if url and ADDRESS_RE.match(url) else []


def _qr_data_url(text: str) -> str:
    """The QR code as an image for the page (python3-qrcode, as for two-step sign-in)."""
    try:
        import base64
        import io
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
        buf = io.BytesIO()
        image.save(buf)
        return 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ''


@app.post('/api/devices/pair-code')
def pair_code():
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    code = ''.join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
    expires = time.time() + PAIR_CODE_SECONDS
    with _lock:
        for key in [k for k, v in _pair_codes.items() if v['expires'] < time.time()]:
            del _pair_codes[key]
        # One open code per person: a new QR code makes the last one useless.
        for key in [k for k, v in _pair_codes.items() if v['user'] == session['user']]:
            del _pair_codes[key]
        _pair_codes[_key(code)] = {'user': session['user'], 'role': session['role'],
                                   'tag': session.get('tag'), 'expires': expires}
    nas = socket.gethostname().split('.')[0]
    addresses = _addresses(str(data.get('origin') or request.host_url))
    link_id = link_client.node_id()      # '' when Link is off: the app then works at home only
    link = 'alvaos://pair?' + '&'.join([f'c={code}', f'n={quote(nas)}', f'u={quote(session["user"])}']
                                         + [f'a={quote(a, safe="")}' for a in addresses]
                                         + ([f'l={link_id}'] if link_id else []))
    return jsonify({'code': f'{code[:4]}-{code[4:]}', 'link': link, 'qr': _qr_data_url(link),
                    'addresses': addresses, 'away': bool(link_id), 'nas_name': nas, 'expires_in': PAIR_CODE_SECONDS})


@app.post('/api/devices/pair')
def pair():
    """The app trades a pairing code for a session of its own."""
    if _limited(request.remote_addr or ''):
        return jsonify({'error': 'Too many attempts. Wait a few minutes.'}), 429
    data = request.get_json(silent=True) or {}
    code = re.sub(r'[^A-Z0-9]', '', str(data.get('code') or '').upper())
    with _lock:
        entry = _pair_codes.pop(_key(code), None) if len(code) == 8 else None
    if not entry or entry['expires'] < time.time():
        return jsonify({'error': 'This code is not valid (any more). Show a new QR code in the Hub › Devices.'}), 401
    if _password_tag(entry['user'], entry['role']) != entry['tag']:
        return jsonify({'error': 'This code is not valid any more. Show a new QR code in the Hub › Devices.'}), 401
    _attempts.pop(request.remote_addr or '', None)
    device = _device_from(data.get('device') or {}) or {}
    token = _new_session(entry['user'], entry['role'], device=device)
    return jsonify({'success': True, 'user': entry['user'], 'token': token, 'device': device['id'],
                    'nas_name': socket.gethostname().split('.')[0]})


def _device_sessions(session: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """(key, session) of the devices this person may see: their own; the admin all."""
    return [(k, v) for k, v in _load_sessions().items() if isinstance(v.get('device'), dict)
            and (v.get('user') == session['user'] or session['role'] == 'admin')]


@app.get('/api/devices')
def devices():
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        found = _device_sessions(session)
    phones = {p['device']: p for p in hub_photos_sync.phones_of(session) if p.get('device')}
    out = []
    for key, v in sorted(found, key=lambda kv: str(kv[1]['device'].get('last_seen')), reverse=True):
        d = v['device']
        backup = phones.get(d.get('id')) if v.get('user') == session['user'] else None
        out.append({'id': d.get('id'), 'name': d.get('name'), 'model': d.get('model'), 'platform': d.get('platform'),
                    'app_version': d.get('app_version'), 'created_at': d.get('created_at'),
                    'last_seen': d.get('last_seen'), 'address': d.get('address'), 'user': v.get('user'),
                    'this': key == session.get('token_key'),
                    'backup': {k: backup[k] for k in ('albums', 'count', 'last_sync')} if backup else None})
    return jsonify({'devices': out})


@app.patch('/api/devices/<device_id>')
def rename_device(device_id):
    session, refused = need_session()
    if refused:
        return refused
    name = re.sub(r'[\x00-\x1f\x7f]', '', str((request.get_json(silent=True) or {}).get('name') or ''))[:60].strip()
    if not name:
        return jsonify({'error': 'Give it a name.'}), 400
    with _lock:
        sessions = _load_sessions()
        hits = [k for k, _ in _device_sessions(session) if sessions[k]['device'].get('id') == device_id]
        for k in hits:
            sessions[k]['device']['name'] = name
        _save_sessions(sessions)
    return jsonify({'success': True}) if hits else (jsonify({'error': 'That device is not known here.'}), 404)


@app.delete('/api/devices/<device_id>')
def remove_device(device_id):
    """Signs that phone out. Its backed-up pictures stay where they are."""
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        sessions = _load_sessions()
        hits = [k for k, _ in _device_sessions(session) if sessions[k]['device'].get('id') == device_id]
        for k in hits:
            del sessions[k]
        _save_sessions(sessions)
    if hits:
        link_client.remove_device(device_id)     # its Link key is not let in any more
    return jsonify({'success': True}) if hits else (jsonify({'error': 'That device is not known here.'}), 404)


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
        result, error = files_manager.run_helper(['files-search', path, query], timeout=30,
                                                 user=as_user(session, share))
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


@app.get('/api/photos/sources')
def photo_sources():
    """Where Photos looks for this person: their own photos (personal folder
    or the place the admin chose, see hub_apps.own_folder) and every photo
    library they may read."""
    session, refused = need_session()
    if refused:
        return refused
    settings = hub_apps.load()
    if not hub_apps.allowed('photos', session['user'], session['role'], settings):
        return jsonify({'error': 'Photos is not turned on for you.', 'app_off': True}), 403
    mine = shares_for(session)
    sources = []
    own = hub_apps.own_folder('photos', session['user'], _read_json(SHARES_FILE), settings)
    if own and own[0] in mine:
        if own[1]:
            # The Photos folder in the personal folder: made the first time, as the person.
            # "Already there" is the usual answer and fine.
            files_manager.run_helper(['files-mkdir', mine[own[0]]['path'], own[1]], user=as_user(session))
        sources.append({'share': own[0], 'path': own[1], 'own': True, 'writable': mine[own[0]]['access'] == 'write'})
    for name in settings['apps']['photos'].get('libraries', []):
        if name in mine and not any(s['share'] == name for s in sources):
            sources.append({'share': name, 'path': '', 'own': False})
    return jsonify({'sources': sources})


_space_cache: Dict[str, Tuple[float, Optional[Dict[str, Optional[int]]]]] = {}
SPACE_SECONDS = 60


@app.get('/api/space')
def space():
    """How much of its space limit a shared folder uses ("42 of 100 GB").
    Only for folders with a limit; read through the helper, at most once a minute."""
    session, refused = need_session()
    if refused:
        return refused
    share, _, _, bad = target(session, {'share': request.args.get('share'), 'path': ''})
    if bad:
        return bad
    if not share.get('limit_bytes'):
        return jsonify({'share': share['name'], 'limit_bytes': None, 'used_bytes': None})
    now = time.time()
    cached = _space_cache.get(share['path'])
    if not cached or now - cached[0] > SPACE_SECONDS:
        import share_quota
        from common import CMD, run_sudo_command
        cached = (now, share_quota.read_usage(share['path'], run_sudo_command, CMD['BTRFS']))
        _space_cache[share['path']] = cached
    usage = cached[1] or {}
    return jsonify({'share': share['name'], 'limit_bytes': usage.get('limit_bytes') or share['limit_bytes'],
                    'used_bytes': usage.get('used_bytes')})


@app.get('/api/media')
def media():
    """Photos and videos of one share (or a folder of it), newest first, as
    the person: the Photos view."""
    session, refused = need_session()
    if refused:
        return refused
    if not hub_apps.allowed('photos', session['user'], session['role']):
        return jsonify({'error': 'Photos is not turned on for you.', 'app_off': True}), 403
    share, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    result, error = files_manager.run_helper(['files-media', path], timeout=60, user=as_user(session))
    if result is None:
        return jsonify({'error': error or 'The photos could not be read.'}), 409
    found = result.get('results') or []
    # The date the photo was taken, where known; the rest is looked up in the background.
    user = as_user(session)
    todo = photo_dates.fill(found, path, _photo_dates)
    if todo:
        photo_dates.look_up(todo, lambda p: _read_head(p, user), _photo_dates, _in_background)
    results = [{**item, 'folder': '/'.join(p for p in (rel, str(item.get('folder') or '')) if p),
                'share': share['name']} for item in found]
    return jsonify({'share': share['name'], 'path': rel, 'results': results,
                    'complete': bool(result.get('complete')), 'dates_pending': len(todo)})


def _in_background(work) -> None:
    _thumbnail_pool().submit(work)   # the same low-priority workers as thumbnails


def _read_head(path: str, user: Optional[str]) -> Optional[bytes]:
    stream, _ = files_manager.open_stream(path, part=(0, photo_dates.HEAD_BYTES), user=user)
    if stream is None:
        return None
    data = b''
    for piece in stream:
        data += piece
        if len(data) >= photo_dates.HEAD_BYTES:
            break
    if hasattr(stream, 'close'):
        stream.close()
    return data[:photo_dates.HEAD_BYTES]


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
        if not isinstance(entry, dict) or (entry.get('snapshot_class') or 'data') not in ('data', 'full_data', 'copy'):
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
PREVIEW_MAX_SOURCE_BYTES = 120 * 1024 ** 2
# Pictures the browser shows itself, and what the Hub makes a still or a JPEG of.
THUMB_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif') \
    + hub_video.PREVIEW_TYPES + hub_video.VIDEO_TYPES


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


def _thumb_dir() -> str:
    """Thumbnails go to the Hub cache place the admin chose (a pool), else
    the system disk as before. A pool that is gone falls back too."""
    cache = hub_apps.cache_dir(_read_json(POOLS_FILE))
    if cache and os.access(os.path.join(cache, 'thumbs'), os.W_OK):
        return os.path.join(cache, 'thumbs')
    return THUMB_DIR


_photo_dates = photo_dates.DateCache(lambda: os.path.dirname(_thumb_dir()))


def _read_part(path: str, user: Optional[str]):
    """read(start, length) -> bytes of the file, as the person, for hub_video."""
    def read(start: int, length: int) -> Optional[bytes]:
        stream, _ = files_manager.open_stream(path, part=(start, length), user=user)
        if stream is None:
            return None
        return b''.join(stream)
    return read


def _make_preview(path: str, user: Optional[str], size: int, side: int) -> Optional[bytes]:
    """A JPEG of a picture (at most `side` pixels), or a still of a video."""
    name = os.path.basename(path)
    if hub_video.is_video(name):
        return hub_video.video_still(name, size, _read_part(path, user), workdir=_thumb_dir())
    stream, _ = files_manager.open_stream(path, user=user)
    if stream is None:
        return None
    return hub_video.jpeg(b''.join(stream), name, side, 80 if side <= THUMB_SIZE else 86)


def _thumb_response(path: str, user: Optional[str], stamp: str, large: bool = False):
    side = hub_video.MAX_PREVIEW_SIDE if large else THUMB_SIZE
    key = hashlib.sha256(f'{user}|{path}|{stamp}{"|large" if large else ""}'.encode()).hexdigest()
    cached = os.path.join(_thumb_dir(), key[:2], key + '.jpg')
    headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'}
    try:
        with open(cached, 'rb') as f:
            return Response(f.read(), mimetype='image/jpeg', headers=headers)
    except OSError:
        pass
    size = files_manager.file_size(path, user=user)
    video = hub_video.is_video(os.path.basename(path))
    limit = PREVIEW_MAX_SOURCE_BYTES if large else THUMB_MAX_SOURCE_BYTES
    if size is None or (size > limit and not video):
        return jsonify({'error': 'No preview for this file.'}), 404
    os.makedirs(_thumb_dir(), exist_ok=True)
    thumb = _thumbnail_pool().submit(_make_preview, path, user, size, side).result(timeout=90)
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


@app.get('/api/preview')
def preview():
    """A JPEG of a picture a browser cannot show (HEIC from phones, TIFF), for the viewer."""
    session, refused = need_session()
    if refused:
        return refused
    _, path, rel, bad = target(session, request.args)
    if bad:
        return bad
    if not rel or not rel.lower().endswith(hub_video.PREVIEW_TYPES):
        return jsonify({'error': 'No preview for this file.'}), 404
    return _thumb_response(path, as_user(session), str(request.args.get('v') or ''), large=True)


# ── Videos a browser cannot play: a converted copy, made when asked ─────────

_converter = hub_video.Converter(lambda: os.path.dirname(_thumb_dir()))


def _video_target(session: Dict[str, Any], args: Any):
    """(path, name, size, key, error response) for a video the person may open."""
    share, path, rel, bad = target(session, args)
    if bad:
        return None, '', 0, '', bad
    name = os.path.basename(rel)
    if not rel or not hub_video.is_video(name):
        return None, '', 0, '', (jsonify({'error': 'This is not a video.'}), 404)
    user = as_user(session)
    size = files_manager.file_size(path, user=user)
    if size is None:
        return None, '', 0, '', (jsonify({'error': 'The video could not be read.'}), 404)
    key = hub_video.Converter.key(user, path, size, str(args.get('v') or ''))
    return path, name, size, key, None


def _video_status(key: str, args: Any) -> Dict[str, Any]:
    status = _converter.status(key)
    status['ffmpeg'] = hub_video.ffmpeg() is not None
    if status['state'] == 'ready':
        status['url'] = '/api/video/stream?' + '&'.join(f'{k}={quote(str(args.get(k) or ""), safe="")}' for k in ('share', 'path', 'v'))
    return status


@app.get('/api/video')
def video_info():
    """Whether a converted copy of a video exists or is being made."""
    session, refused = need_session()
    if refused:
        return refused
    path, _, _, key, bad = _video_target(session, request.args)
    if bad or path is None:
        return bad
    return jsonify(_video_status(key, request.args))


@app.post('/api/video/convert')
def video_convert():
    """Starts making a copy that every browser plays (H.264 + AAC, at most 720 pixels high)."""
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    path, name, size, key, bad = _video_target(session, data)
    if bad or path is None:
        return bad
    user = as_user(session)
    status = _converter.start(key, name, size, lambda: files_manager.open_stream(path, user=user)[0] or [])
    status['ffmpeg'] = hub_video.ffmpeg() is not None
    return jsonify(status)


@app.get('/api/video/stream')
def video_stream():
    """The converted copy, with Range requests so the player can jump around."""
    session, refused = need_session()
    if refused:
        return refused
    path, _, _, key, bad = _video_target(session, request.args)
    if bad or path is None:
        return bad
    out = _converter.output(key)
    if not os.path.isfile(out):
        return jsonify({'error': 'There is no converted copy of this video yet.'}), 404
    os.utime(out, None)
    from flask import send_file
    response = send_file(out, mimetype='video/mp4', conditional=True, max_age=0)
    response.headers['Cache-Control'] = 'private, max-age=600'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


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
    if data.get('replace') is True:
        # A changed text file: the old one goes to the trash (so it can come
        # back), then the new one takes its name.
        share = shares_for(session)[str(data.get('share'))]
        files_manager.run_helper(['files-trash', share.get('root', share['path']), path, str(data.get('name') or '')],
                                 user=as_user(session))
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
        result, problem = files_manager.run_helper(['files-trash', share.get('root', share['path']), path, str(name)],
                                                   user=as_user(session))
        if result is not None:
            moved.append(str(name))
        else:
            failed.append(f'{name}: {problem}')
    if not moved:
        return jsonify({'error': '; '.join(failed) or 'Nothing was deleted.'}), 409
    return jsonify({'success': True, 'moved': moved, 'failed': failed})


# ── Sharing a folder with people on the NAS (see _granted) ──────────────────

GRANT_ACCESS = ('read', 'write')
MAX_GRANT_PEOPLE = 50


def _people() -> List[str]:
    return sorted(n for n, v in _read_json(USERS_FILE).items() if isinstance(v, dict) and v.get('files_auth'))


def _grant_view(grant: Dict[str, Any]) -> Dict[str, Any]:
    return {k: grant.get(k) for k in ('id', 'share', 'path', 'to', 'access', 'created_at')}


@app.get('/api/people')
def people():
    """The others who may sign in here, to share a folder with."""
    session, refused = need_session()
    if refused:
        return refused
    return jsonify({'people': [p for p in _people() if p != session['user']]})


@app.get('/api/grants')
def grants_list():
    """The folders this person shared ("mine"), optionally for one folder."""
    session, refused = need_session()
    if refused:
        return refused
    share, rel = request.args.get('share'), request.args.get('path')
    mine = [x for x in _load_grants() if x.get('owner') == session['user']
            and (share is None or (x.get('share') == share and x.get('path') == (rel or '')))]
    return jsonify({'grants': [_grant_view(x) for x in mine]})


@app.post('/api/grants')
def grants_save():
    """Share a folder with people: {share, path, to: [names], access: read|write}.
    An empty `to` stops sharing it."""
    session, refused = need_session()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    share, path, rel, bad = target(session, data)
    if bad:
        return bad
    if share.get('granted_by'):
        return jsonify({'error': f'Only {share["granted_by"]} can share this folder further.'}), 403
    access = data.get('access')
    if access not in GRANT_ACCESS:
        return jsonify({'error': 'Choose whether they may only look or also change.'}), 400
    if access == 'write' and share['access'] != 'write':
        return jsonify({'error': f'You can only look at "{share["name"]}", so you can only let others look.'}), 403
    to = data.get('to')
    known = set(_people())
    if not isinstance(to, list) or len(to) > MAX_GRANT_PEOPLE or any(
            not isinstance(p, str) or p not in known or p == session['user'] for p in to):
        return jsonify({'error': 'Choose people who can sign in here.'}), 400
    entries, error = files_manager.list_entries(path, user=as_user(session))
    if entries is None:
        return jsonify({'error': error or 'That is not a folder you can open.'}), 404
    with _lock:
        grants = _load_grants()
        old = next((x for x in grants if x.get('owner') == session['user'] and x.get('share') == share['name']
                    and x.get('path') == rel), None)
        if old:
            grants.remove(old)
        grant = None
        if to:
            grant = {'id': old['id'] if old else secrets.token_hex(6), 'owner': session['user'],
                     'share': share['name'], 'path': rel, 'to': sorted(set(to)), 'access': access,
                     'created_at': old['created_at'] if old else _now().isoformat()}
            grants.append(grant)
        _save_grants(grants)
    return jsonify({'success': True, 'grant': _grant_view(grant) if grant else None})


@app.post('/api/grants/<grant_id>/delete')
def grants_delete(grant_id):
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        grants = _load_grants()
        keep = [x for x in grants if not (x.get('id') == grant_id
                                         and (x.get('owner') == session['user'] or session['role'] == 'admin'))]
        if len(keep) == len(grants):
            return jsonify({'error': 'This is not shared by you.'}), 404
        _save_grants(keep)
    return jsonify({'success': True})


@app.get('/api/trash')
def trash():
    session, refused = need_session()
    if refused:
        return refused
    share, _, _, bad = target(session, {'share': request.args.get('share'), 'path': ''})
    if bad:
        return bad
    if share.get('granted_by'):
        # The trash belongs to the whole share of the person who shared it.
        return jsonify({'success': True, 'items': [], 'keep_days': 30,
                        'note': f'What you delete here goes to the trash of {share["granted_by"]}.'})
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
    if share.get('granted_by'):
        return jsonify({'error': f'Ask {share["granted_by"]} to get it back from their trash.'}), 403
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
            'new_files': int(link.get('new_files') or 0), 'last_upload': link.get('last_upload'),
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
    if share.get('granted_by'):
        return jsonify({'error': f'Only {share["granted_by"]} can make links for this folder.'}), 403
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


@app.post('/api/links/seen')
def links_seen():
    """The person has looked at the list: the "new" counts start again."""
    session, refused = need_session()
    if refused:
        return refused
    with _lock:
        links = _load_links()
        changed = False
        for v in links.values():
            if v.get('owner') == session['user'] and v.get('new_files'):
                v['new_files'] = 0
                changed = True
        if changed:
            _save_links(links)
    return jsonify({'success': True})


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


def _count_file(token: str) -> None:
    with _lock:
        links = _load_links()
        if token in links:
            links[token]['new_files'] = int(links[token].get('new_files') or 0) + 1
            links[token]['last_upload'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
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
    result, error = files_manager.run_helper(_finish_args(base, data, size), user=owner)
    if result is not None:
        _count_file(token)
    return _helper_answer(result, error, 201)


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


# ── Other Hub apps (their own modules) ───────────────────────────────────────

hub_data.setup(need_session=need_session, shares_for=shares_for, as_user=as_user, read_json=_read_json,
               shares_file=lambda: SHARES_FILE)
app.register_blueprint(hub_calendar.bp)
app.register_blueprint(hub_chat.bp)
app.register_blueprint(hub_contacts.bp)
app.register_blueprint(hub_caldav.bp)
app.register_blueprint(hub_photos_sync.bp)
app.register_blueprint(hub_albums.bp)


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
def public_links_need_files():
    """Share links are part of Files: while Files is off in the Hub, they do not open."""
    if request.path.startswith(('/api/public/', '/s/')) and not hub_apps.load()['apps']['files']['enabled']:
        return jsonify({'error': 'This link does not work at the moment.'}), 404
    return None


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
