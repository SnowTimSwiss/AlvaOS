"""AlvaOS Link (Settings › AlvaOS Link): on or off, and who is connected. See link_daemon.py."""

from flask import Blueprint, jsonify, request

import link_client
from auth_manager import require_auth

bp = Blueprint('link', __name__)


def _public(status):
    """What the page needs; the peers' addresses on the loopback network stay inside."""
    if status is None:
        return {'available': False, 'enabled': False, 'running': False, 'node_id': '', 'online': False, 'peers': []}
    peers = [{'id': p['id'], 'kind': p.get('kind'), 'name': p.get('name'), 'user': p.get('user', ''),
              'connected': bool(p.get('connected')), 'last_seen': p.get('last_seen')} for p in status.get('peers', [])]
    return {'available': True, 'enabled': bool(status.get('enabled')), 'running': bool(status.get('running')),
            'node_id': status.get('node_id', ''), 'online': bool(status.get('relay')), 'peers': peers}


@bp.route('/api/v1/link', methods=['GET'])
@require_auth(require_admin=True)
def link_status():
    return jsonify(_public(link_client.status()))


@bp.route('/api/v1/link', methods=['POST'])
@require_auth(require_admin=True)
def link_switch():
    """{"enabled": bool}: Link on or off. Off closes the way in for every phone and buddy at once."""
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'Say whether Link should be on.'}), 400
    answer = link_client.set_enabled(enabled)
    if answer is None:
        return jsonify({'error': 'AlvaOS Link is not running on this NAS. Restart the NAS or reinstall the update.'}), 503
    return jsonify({'success': True, **_public(answer)})
