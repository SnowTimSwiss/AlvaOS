"""AlvaOS Files API: browse and download the shared folders (admins)."""

import os
import secrets
import threading
import time
from typing import Any, Dict, List
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

import files_manager
from auth_manager import require_auth
from common import run_sudo_command
from shares_manager import load_shares_state

bp = Blueprint('files', __name__)

UPLOAD_LIMIT_BYTES = 4 * 1024 ** 3


@bp.route('/api/v1/files/shares', methods=['GET'])
@require_auth(require_admin=True)
def files_shares():
    shares = [{'name': s.get('name'), 'protocol': s.get('protocol', 'smb')}
              for s in load_shares_state().values() if isinstance(s, dict) and s.get('name')]
    return jsonify({'success': True, 'shares': sorted(shares, key=lambda s: str(s['name']).lower()),
                    'upload_limit_bytes': UPLOAD_LIMIT_BYTES})


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


# ── Changes ──────────────────────────────────────────────────────────────────

def _folder(data):
    """(share root, folder path, relative folder, error) for a request."""
    shares = load_shares_state()
    share = str(data.get('share') or '')
    folder, rel, error = files_manager.resolve(shares, share, str(data.get('path') or ''))
    root = files_manager.share_root(shares, share)
    if error or folder is None or root is None:
        return None, None, '', error or 'This shared folder does not exist.'
    return root, folder, rel, ''


def _answer(result, error, status=200, **extra):
    if result is None:
        return jsonify({'error': error}), 409
    return jsonify({'success': True, **result, **extra}), status


@bp.route('/api/v1/files/upload', methods=['POST'])
@require_auth(require_admin=True)
def files_upload():
    """The file is the request body; share, folder and name are in the URL."""
    _, folder, _, error = _folder(request.args)
    if error:
        return jsonify({'error': error}), 404
    if (request.content_length or 0) > UPLOAD_LIMIT_BYTES:
        return jsonify({'error': 'Files over 4 GB go through the shared folder on your computer.'}), 413
    result, error = files_manager.upload(folder, str(request.args.get('name') or ''), request.stream)
    return _answer(result, error, 201)


@bp.route('/api/v1/files/mkdir', methods=['POST'])
@require_auth(require_admin=True)
def files_mkdir():
    data = request.get_json(silent=True) or {}
    _, folder, _, error = _folder(data)
    if error:
        return jsonify({'error': error}), 404
    return _answer(*files_manager.run_helper(['files-mkdir', folder, str(data.get('name') or '')]))


@bp.route('/api/v1/files/rename', methods=['POST'])
@require_auth(require_admin=True)
def files_rename():
    data = request.get_json(silent=True) or {}
    _, folder, _, error = _folder(data)
    if error:
        return jsonify({'error': error}), 404
    return _answer(*files_manager.run_helper(['files-rename', folder, str(data.get('old') or ''),
                                              str(data.get('new') or '')]))


@bp.route('/api/v1/files/delete', methods=['POST'])
@require_auth(require_admin=True)
def files_delete():
    """Moves the items into the share's trash (kept 30 days)."""
    data = request.get_json(silent=True) or {}
    root, folder, _, error = _folder(data)
    if error:
        return jsonify({'error': error}), 404
    names = data.get('names') if isinstance(data.get('names'), list) else []
    if not names or len(names) > 500:
        return jsonify({'error': 'Choose what to delete.'}), 400
    moved: List[str] = []
    failed: List[str] = []
    for name in names:
        result, problem = files_manager.run_helper(['files-trash', root, folder, str(name)])
        if result is not None:
            moved.append(str(name))
        else:
            failed.append(f'{name}: {problem}')
    if not moved:
        return jsonify({'error': '; '.join(failed) or 'Nothing was deleted.'}), 409
    return jsonify({'success': True, 'moved': moved, 'failed': failed})


@bp.route('/api/v1/files/trash', methods=['GET'])
@require_auth(require_admin=True)
def files_trash():
    root = files_manager.share_root(load_shares_state(), request.args.get('share', ''))
    if root is None:
        return jsonify({'error': 'This shared folder does not exist.'}), 404
    return _answer(*files_manager.run_helper(['files-trash-list', root]), keep_days=30)


@bp.route('/api/v1/files/trash/restore', methods=['POST'])
@require_auth(require_admin=True)
def files_trash_restore():
    data = request.get_json(silent=True) or {}
    root = files_manager.share_root(load_shares_state(), str(data.get('share') or ''))
    if root is None:
        return jsonify({'error': 'This shared folder does not exist.'}), 404
    return _answer(*files_manager.run_helper(['files-trash-restore', root, str(data.get('id') or '')]))


@bp.route('/api/v1/files/trash/empty', methods=['POST'])
@require_auth(require_admin=True)
def files_trash_empty():
    data = request.get_json(silent=True) or {}
    root = files_manager.share_root(load_shares_state(), str(data.get('share') or ''))
    if root is None:
        return jsonify({'error': 'This shared folder does not exist.'}), 404
    return _answer(*files_manager.run_helper(['files-trash-purge', root, '0']))
