#!/usr/bin/env python3
"""AlvaOS Files over WebDAV (port 8091), for the file apps of computers and
phones: Finder (Go › Connect to Server), Windows (Map network drive), GNOME
Files, Documents/FE File Explorer on a phone.

The address is http://<nas>:8091/. People sign in with the same name and
password as for the shares and see the same shares with the same rights;
every file operation runs as them through the helper (`--as`), so Linux
checks the rights exactly as for SMB. The admin account is not offered here
(it would act as root, and WebDAV cannot ask for a two-step code).

It runs next to the Files app in the same process, on a server that streams
request bodies (also chunked ones), so a big upload is never parked on the
system disk. Nothing is overwritten in place: a file that is replaced (or
deleted, or moved onto) goes to the share's trash first.
"""

import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

from flask import Flask, Response, request

import files_manager
import files_server as fs

PORT = 8091
REALM = 'AlvaOS Files'
CREDENTIAL_SECONDS = 600
PART_SUFFIX = '.alvaos-upload'

app = Flask(__name__)
_cred_lock = threading.Lock()
# sha256(name:password) -> (name, until, the stored hash it was checked against)
_good_credentials: Dict[str, Tuple[str, float, str]] = {}


# ── Signing in (HTTP Basic, checked like the Files app) ─────────────────────

def _challenge(message: str = 'Sign in with your name and password for the shared folders.'):
    return Response(message + '\n', 401, {'WWW-Authenticate': f'Basic realm="{REALM}", charset="UTF-8"',
                                         'Content-Type': 'text/plain; charset=utf-8'})


# Only wrong passwords count: Finder and Windows send many requests at once,
# and several devices at home share one address.
FAILURE_WINDOW = 15 * 60
MAX_FAILURES = 10
_failures: Dict[str, List[float]] = {}


def _too_many_failures(ip: str, now: float) -> bool:
    with _cred_lock:
        recent = [t for t in _failures.get(ip, []) if now - t < FAILURE_WINDOW]
        _failures[ip] = recent
        return len(recent) >= MAX_FAILURES


def _note_failure(ip: str, now: float) -> None:
    with _cred_lock:
        _failures.setdefault(ip, []).append(now)


def _stored_hash(user: str) -> str:
    entry = fs._read_json(fs.USERS_FILE).get(user)
    return str(entry.get('files_auth')) if isinstance(entry, dict) else ''


def signed_in() -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    auth = request.authorization
    if not auth or auth.type != 'basic' or not auth.username:
        return None, _challenge()
    name = str(auth.username).strip().lower()
    password = str(auth.password or '')
    key = hashlib.sha256(f'{name}\0{password}'.encode()).hexdigest()
    now = time.time()
    with _cred_lock:
        known = _good_credentials.get(key)
    if known and known[1] > now and known[2] == _stored_hash(known[0]):
        return _admit(known[0], 'user')   # a changed password ends this at once
    if name == 'admin':
        return None, _challenge('The admin account cannot be used here. Sign in as a person from Storage › Users.')
    ip = request.remote_addr or ''
    if _too_many_failures(ip, now):
        return None, Response('Too many wrong passwords. Wait a few minutes.\n', 429)
    who, error, _ = fs.check_login(name, password, '')
    if not who:
        _note_failure(ip, now)
        return None, _challenge(error or 'That name or password is not right.')
    with _cred_lock:
        for k in [k for k, v in _good_credentials.items() if v[1] < now]:
            del _good_credentials[k]
        _good_credentials[key] = (who[0], now + CREDENTIAL_SECONDS, _stored_hash(who[0]))
    return _admit(who[0], who[1])


def _admit(user: str, role: str):
    """WebDAV belongs to Files: only for people who may use Files in the Hub."""
    import hub_apps
    if not hub_apps.allowed('files', user, role):
        return None, Response('Files is not turned on for you. Ask the person who looks after the NAS.\n', 403)
    return {'user': user, 'role': role}, None


# ── Paths: /<share>/<folder>/<name> ─────────────────────────────────────────

def _split(raw: str) -> Tuple[str, str]:
    """('Share', 'a/b') from '/Share/a/b/' (already URL-decoded)."""
    parts = [p for p in raw.split('/') if p not in ('', '.')]
    return (parts[0], '/'.join(parts[1:])) if parts else ('', '')


def _resolve(session, raw: str):
    """(share, absolute path, relative path, error response). share None = the root."""
    share_name, rel = _split(raw)
    if not share_name:
        return None, None, '', None
    share = fs.shares_for(session).get(share_name)
    if not share:
        return None, None, '', Response('Not found\n', 404)
    path, rel, error = files_manager.resolve({'s': share}, share['name'], rel)
    if error or path is None:
        return None, None, '', Response('Not found\n', 404)
    return share, path, rel, None


def _href(share: str, rel: str, folder: bool) -> str:
    path = '/'.join(p for p in (share, rel) if p)
    return '/' + quote(path) + ('/' if folder and path else '')


def _entries(path: str, user: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    entries, _ = files_manager.list_entries(path, user=user)
    if entries is None:
        return None
    return [e for e in entries if e.get('type') in ('file', 'folder') and not str(e['name']).endswith(PART_SUFFIX)]


def _stat(path: str, user: Optional[str]) -> Optional[Dict[str, Any]]:
    """The entry for one file or folder (from its parent's listing)."""
    parent, name = os.path.split(path.rstrip('/'))
    for entry in _entries(parent, user) or []:
        if entry['name'] == name:
            return entry
    return None


# ── PROPFIND ─────────────────────────────────────────────────────────────────

def _http_date(iso: Optional[str]) -> str:
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    return format_datetime(when.astimezone(timezone.utc), usegmt=True)


def _response_xml(href: str, name: str, folder: bool, size: int = 0, modified: Optional[str] = None) -> str:
    props = [f'<D:displayname>{escape(name)}</D:displayname>',
             f'<D:getlastmodified>{_http_date(modified)}</D:getlastmodified>',
             '<D:supportedlock><D:lockentry><D:lockscope><D:exclusive/></D:lockscope>'
             '<D:locktype><D:write/></D:locktype></D:lockentry></D:supportedlock>']
    if folder:
        props.append('<D:resourcetype><D:collection/></D:resourcetype>')
    else:
        mime, _ = files_manager.content_type(name, False)
        props += ['<D:resourcetype/>', f'<D:getcontentlength>{int(size)}</D:getcontentlength>',
                  f'<D:getcontenttype>{escape(mime)}</D:getcontenttype>',
                  f'<D:getetag>"{int(size)}-{escape(str(modified or ""))}"</D:getetag>']
    return (f'<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop>{"".join(props)}</D:prop>'
            '<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>')


def _multistatus(parts: List[str]) -> Response:
    body = '<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">' + ''.join(parts) + \
           '</D:multistatus>'
    return Response(body, 207, {'Content-Type': 'application/xml; charset=utf-8'})


def propfind(session, raw: str) -> Response:
    depth = request.headers.get('Depth', '1')
    share, path, rel, bad = _resolve(session, raw)
    if bad:
        return bad
    user = fs.as_user(session)
    if share is None:   # the root: one folder per share
        parts = [_response_xml('/', 'AlvaOS Files', True)]
        if depth != '0':
            parts += [_response_xml(_href(name, '', True), name, True)
                      for name in sorted(fs.shares_for(session))]
        return _multistatus(parts)
    if not rel:
        entry: Optional[Dict[str, Any]] = {'name': share['name'], 'type': 'folder'}
    else:
        entry = _stat(path, user)
    if entry is None:
        return Response('Not found\n', 404)
    folder = entry['type'] == 'folder'
    parts = [_response_xml(_href(share['name'], rel, folder), entry['name'], folder,
                           entry.get('size_bytes', 0), entry.get('modified_at'))]
    if folder and depth != '0':
        children = _entries(path, user)
        if children is None:
            return Response('Forbidden\n', 403)
        for child in children:
            child_rel = f'{rel}/{child["name"]}' if rel else child['name']
            parts.append(_response_xml(_href(share['name'], child_rel, child['type'] == 'folder'), child['name'],
                                       child['type'] == 'folder', child.get('size_bytes', 0),
                                       child.get('modified_at')))
    return _multistatus(parts)


# ── Reading ──────────────────────────────────────────────────────────────────

def get_file(session, raw: str, head: bool) -> Response:
    share, path, rel, bad = _resolve(session, raw)
    if bad:
        return bad
    if share is None or not rel:
        return Response('This is a folder. Open it in a file app as a WebDAV address.\n', 200,
                        {'Content-Type': 'text/plain; charset=utf-8'})
    user = fs.as_user(session)
    size = files_manager.file_size(path, user=user)
    if size is None:
        entry = _stat(path, user)
        if entry and entry['type'] == 'folder':
            return Response('This is a folder.\n', 200, {'Content-Type': 'text/plain; charset=utf-8'})
        return Response('Not found\n', 404)
    mime, _ = files_manager.content_type(os.path.basename(path), False)
    headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(size)}
    if head:
        return Response(b'', 200, {**headers, 'Content-Type': mime})
    part, ok = files_manager.parse_range(request.headers.get('Range'), size)
    if not ok:
        return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
    stream, error = files_manager.open_stream(path, part=part, user=user)
    if stream is None:
        return Response((error or 'The file could not be read.') + '\n', 403)
    if part:
        headers['Content-Length'] = str(part[1])
        headers['Content-Range'] = f'bytes {part[0]}-{part[0] + part[1] - 1}/{size}'
    return Response(stream, 206 if part else 200, mimetype=mime, headers=headers, direct_passthrough=True)


# ── Changing ─────────────────────────────────────────────────────────────────

def _writable(session, raw: str):
    """(share, folder, name, error) for something to create or change."""
    share, path, rel, bad = _resolve(session, raw)
    if bad:
        return None, None, '', bad
    if share is None or not rel:
        return None, None, '', Response('The shared folders themselves cannot be changed here.\n', 403)
    if share['access'] != 'write':
        return None, None, '', Response('You can only read this shared folder.\n', 403)
    folder, name = os.path.split(path)
    return share, folder, name, None


def _helper(args: List[str], session, timeout: int = 600) -> Optional[str]:
    """Runs one helper operation as the person; None when it worked."""
    result, error = files_manager.run_helper(args, timeout=timeout, user=fs.as_user(session))
    return None if result is not None else (error or 'That did not work.')


def _to_trash(share, folder: str, name: str, session) -> Optional[str]:
    return _helper(['files-trash', share['path'], folder, name], session)


def put(session, raw: str) -> Response:
    share, folder, name, bad = _writable(session, raw)
    if bad:
        return bad
    user = fs.as_user(session)
    existing = _stat(os.path.join(folder, name), user)
    if existing and existing['type'] == 'folder':
        return Response('A folder has this name.\n', 409)
    # Saved as a hidden part file first; a file it replaces goes to the trash.
    target = f'.{secrets.token_hex(4)}.{name}' if existing else name
    if len(target.encode()) > 255:
        target = f'.{secrets.token_hex(8)}.alvaos-new'
    result, error = files_manager.pipe_helper(['files-part-write', folder, target, '0'], request.stream, user=user)
    if result is None:
        _helper(['files-part-abort', folder, target], session)
        return Response((error or 'The file could not be saved.') + '\n', 409)
    problem = _helper(['files-part-finish', folder, target, str(int(result.get('size') or 0))], session)
    if problem:
        return Response(problem + '\n', 409)
    if existing:
        problem = _to_trash(share, folder, name, session) or _helper(['files-rename', folder, target, name], session)
        if problem:
            _to_trash(share, folder, target, session)
            return Response(problem + '\n', 409)
    return Response(b'', 204 if existing else 201)


def mkcol(session, raw: str) -> Response:
    _, folder, name, bad = _writable(session, raw)
    if bad:
        return bad
    if request.content_length:
        return Response('Unsupported\n', 415)
    problem = _helper(['files-mkdir', folder, name], session)
    if problem:
        return Response(problem + '\n', 405 if 'already there' in problem else 409)
    return Response(b'', 201)


def delete(session, raw: str) -> Response:
    share, folder, name, bad = _writable(session, raw)
    if bad:
        return bad
    if _stat(os.path.join(folder, name), fs.as_user(session)) is None:
        return Response('Not found\n', 404)
    problem = _to_trash(share, folder, name, session)
    return Response((problem + '\n') if problem else b'', 403 if problem else 204)


def _destination(session):
    """(share, folder, name, error) from the Destination header."""
    dest = request.headers.get('Destination', '')
    if not dest:
        return None, None, '', Response('Destination is missing.\n', 400)
    return _writable(session, unquote(urlsplit(dest).path))


def move_or_copy(session, raw: str, copying: bool) -> Response:
    if copying:   # copying only needs to read the source
        share, path, rel, bad = _resolve(session, raw)
        if bad:
            return bad
        if share is None or not rel:
            return Response('The shared folders themselves cannot be copied here.\n', 403)
        folder, name = os.path.split(path)
    else:
        share, folder, name, bad = _writable(session, raw)
        if bad:
            return bad
    dshare, dfolder, dname, bad = _destination(session)
    if bad:
        return bad
    if dshare['name'] != share['name']:
        return Response('Move and copy work inside one shared folder; copy across shared folders on your '
                        'computer.\n', 502)
    user = fs.as_user(session)
    if _stat(os.path.join(folder, name), user) is None:
        return Response('Not found\n', 404)
    existed = _stat(os.path.join(dfolder, dname), user) is not None
    if existed:
        if request.headers.get('Overwrite', 'T').upper() == 'F':
            return Response('The destination exists.\n', 412)
        problem = _to_trash(share, dfolder, dname, session)
        if problem:
            return Response(problem + '\n', 409)
    if copying:
        result, error = files_manager.run_helper(['files-copy', folder, name, dfolder], timeout=3600, user=user)
        if result is None:
            return Response((error or 'The copy did not work.') + '\n', 409)
        made = str(result.get('name') or name)
        if made != dname:
            problem = _helper(['files-rename', dfolder, made, dname], session)
            if problem:
                return Response(problem + '\n', 409)
    else:
        if os.path.normpath(folder) == os.path.normpath(dfolder):
            problem = _helper(['files-rename', folder, name, dname], session)
        else:
            problem = _helper(['files-move', folder, name, dfolder], session)
            if not problem and dname != name:
                problem = _helper(['files-rename', dfolder, name, dname], session)
        if problem:
            return Response(problem + '\n', 409)
    return Response(b'', 204 if existed else 201)


# Finder and Windows only write to a WebDAV server that can lock. Every
# change here is a single step through the helper, so locks are only
# acknowledged, not enforced.
def lock(raw: str) -> Response:
    token = f'opaquelocktoken:{secrets.token_hex(16)}'
    request.get_data(cache=False)   # the lock request body is not needed
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
           '<D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope><D:depth>0</D:depth>'
           '<D:timeout>Second-3600</D:timeout>'
           f'<D:locktoken><D:href>{token}</D:href></D:locktoken>'
           f'<D:lockroot><D:href>{escape(quote(raw))}</D:href></D:lockroot>'
           '</D:activelock></D:lockdiscovery></D:prop>')
    return Response(xml, 200, {'Content-Type': 'application/xml; charset=utf-8', 'Lock-Token': f'<{token}>'})


def proppatch(raw: str) -> Response:
    # Times and attributes are kept by the file system; say yes so clients go on.
    return _multistatus([f'<D:response><D:href>{escape(quote(raw))}</D:href><D:propstat><D:prop/>'
                         '<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>'])


METHODS = ['OPTIONS', 'GET', 'HEAD', 'PUT', 'DELETE', 'MKCOL', 'PROPFIND', 'PROPPATCH', 'MOVE', 'COPY',
           'LOCK', 'UNLOCK']


@app.route('/', defaults={'raw': ''}, methods=METHODS)
@app.route('/<path:raw>', methods=METHODS)
def dav(raw):
    raw = '/' + raw
    import tls_manager
    if not request.is_secure and tls_manager.https_only() and (request.remote_addr or '') not in ('127.0.0.1', '::1'):
        # File managers do not follow redirects reliably; say where to go.
        host = (request.host or '').rsplit(':', 1)[0]
        return Response(f'This NAS only answers over HTTPS: https://{host}:{tls_manager.PORTS["dav"]}/\n', 403,
                        {'Content-Type': 'text/plain; charset=utf-8'})
    if request.method == 'OPTIONS':
        return Response(b'', 200, {'DAV': '1, 2', 'Allow': ', '.join(METHODS), 'MS-Author-Via': 'DAV'})
    session, refused = signed_in()
    if refused:
        return refused
    method = request.method
    if method == 'PROPFIND':
        return propfind(session, raw)
    if method in ('GET', 'HEAD'):
        return get_file(session, raw, method == 'HEAD')
    if method == 'PUT':
        return put(session, raw)
    if method == 'MKCOL':
        return mkcol(session, raw)
    if method == 'DELETE':
        return delete(session, raw)
    if method in ('MOVE', 'COPY'):
        return move_or_copy(session, raw, method == 'COPY')
    if method == 'LOCK':
        return lock(raw)
    if method == 'UNLOCK':
        return Response(b'', 204)
    return proppatch(raw)


def serve_in_background(host: str = '0.0.0.0', port: int = PORT) -> Optional[threading.Thread]:
    """Starts the WebDAV server next to the Files app. Werkzeug's threaded
    server streams request bodies (chunked too) straight to the helper."""
    from werkzeug.serving import make_server
    try:
        server = make_server(host, port, app, threaded=True)
    except OSError as e:
        print(f'WebDAV not started on port {port}: {e}')
        return None
    thread = threading.Thread(target=server.serve_forever, name='alvaos-webdav', daemon=True)
    thread.start()
    print(f'AlvaOS Files WebDAV on port {port}')
    return thread
