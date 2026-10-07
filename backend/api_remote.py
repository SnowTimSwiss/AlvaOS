"""Remote access (Settings › Remote access): Tailscale and Cloudflare Tunnel. See remote_access.py."""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth

bp = Blueprint('remote', __name__)


def _answer(ok, message):
    from app_services import remote
    if not ok:
        return jsonify({'error': message}), 400
    return jsonify({'success': True, 'message': message, **remote.status()})


@bp.route('/api/v1/remote-access', methods=['GET'])
@require_auth(require_admin=True)
def remote_status():
    from app_services import remote
    return jsonify(remote.status())


@bp.route('/api/v1/remote-access/tailscale', methods=['POST'])
@require_auth(require_admin=True)
def remote_tailscale():
    """{"enabled": bool} or {"logout": true}."""
    from app_services import remote
    data = request.get_json(silent=True) or {}
    if data.get('logout') is True:
        return _answer(*remote.tailscale_logout())
    if not isinstance(data.get('enabled'), bool):
        return jsonify({'error': 'Say whether Tailscale should be on.'}), 400
    return _answer(*remote.set_tailscale(data['enabled']))


@bp.route('/api/v1/remote-access/cloudflare/zones', methods=['POST'])
@require_auth(require_admin=True)
def remote_cloudflare_zones():
    """The domains an API token can use: {"api_token"} (empty: the saved one)."""
    from app_services import remote
    zones, problem = remote.cloudflare_zones(str((request.get_json(silent=True) or {}).get('api_token') or ''))
    if problem:
        return jsonify({'error': problem}), 400
    return jsonify({'success': True, 'zones': zones})


@bp.route('/api/v1/remote-access/cloudflare', methods=['POST', 'DELETE'])
@require_auth(require_admin=True)
def remote_cloudflare():
    """POST {api_token, zone_id, name} or {tunnel_token, hostname}; DELETE removes it."""
    from app_services import remote
    if request.method == 'DELETE':
        return _answer(*remote.cloudflare_disconnect())
    return _answer(*remote.cloudflare_connect(request.get_json(silent=True) or {}))
