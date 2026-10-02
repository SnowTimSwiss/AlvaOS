"""AlvaOS Files API: browse and download the shared folders (admins)."""

import os
import secrets
import threading
import time
from typing import Any, Dict
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

import files_manager
from auth_manager import require_auth
from common import run_sudo_command
from shares_manager import load_shares_state

bp = Blueprint('files', __name__)


@bp.route('/api/v1/files/shares', methods=['GET'])
@require_auth(require_admin=True)
def files_shares():
    shares = [{'name': s.get('name'), 'protocol': s.get('protocol', 'smb')}
              for s in load_shares_state().values() if isinstance(s, dict) and s.get('name')]
    return jsonify({'success': True, 'shares': sorted(shares, key=lambda s: str(s['name']).lower())})


@bp.route('/api/v1/files/list', methods=['GET'])
@require_auth(require_admin=True)
def files_list():
    path, rel, error = files_manager.resolve(load_shares_state(), request.args.get('share', ''),
                                             request.args.get('path', ''))
    if error or path is None:
        return jsonify({'error': error}), 404
    entries, error = files_manager.list_folder(path, run_sudo_command)
    if entries is None:
        return jsonify({'error': error}), 404
    return jsonify({'success': True, 'share': request.args.get('share'), 'path': rel, 'entries': entries})


# Short-lived links, so the browser can download or show a big file itself
# (an <a download> or <img src> cannot send the sign-in header). A link is
# bound to one file and expires after ten minutes.
LINK_TTL_SECONDS = 600
_links: Dict[str, Dict[str, Any]] = {}
_links_lock = threading.Lock()


def _prune_links(now):
    for token in [t for t, link in _links.items() if link['expires'] < now]:
        del _links[token]


@bp.route('/api/v1/files/link', methods=['POST'])
@require_auth(require_admin=True)
def files_link():
    data = request.get_json(silent=True) or {}
    share = str(data.get('share') or '')
    path, rel, error = files_manager.resolve(load_shares_state(), share, str(data.get('path') or ''))
    if error or not rel or path is None:
        return jsonify({'error': error or 'Choose a file.'}), 404
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _links_lock:
        _prune_links(now)
        _links[token] = {'path': path, 'name': os.path.basename(rel), 'expires': now + LINK_TTL_SECONDS,
                         'inline': bool(data.get('inline'))}
    return jsonify({'success': True, 'url': f'/api/v1/files/get/{token}', 'expires_in': LINK_TTL_SECONDS})


@bp.route('/api/v1/files/get/<token>', methods=['GET'])
def files_get(token):
    """Reachable without a session on purpose: the unguessable, expiring link is the permission."""
    with _links_lock:
        _prune_links(time.time())
        link = dict(_links.get(token) or {})
    if not link:
        return jsonify({'error': 'This link has expired. Open the file again in AlvaOS Files.'}), 404
    stream, error = files_manager.open_stream(link['path'])
    if stream is None:
        return jsonify({'error': error or 'The file could not be read.'}), 404
    mime, inline = files_manager.content_type(link['name'], link['inline'])
    disposition = 'inline' if inline else 'attachment'
    headers = {
        'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(link['name'])}",
        'X-Content-Type-Options': 'nosniff',
        # Sandboxed, except PDFs: the browser's PDF viewer does not run in a sandbox.
        'Content-Security-Policy': "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                                   "style-src 'unsafe-inline'" + ('' if mime == 'application/pdf' else '; sandbox'),
        'Cache-Control': 'private, no-store',
        'Referrer-Policy': 'no-referrer',
    }
    return Response(stream, mimetype=mime, headers=headers, direct_passthrough=True)
