"""Remote access over WireGuard (Settings › Remote access). See remote_access.py."""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth

bp = Blueprint('remote', __name__)


@bp.route('/api/v1/remote-access', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def remote_settings():
    from app_services import remote
    if request.method == 'POST':
        ok, message = remote.configure(request.get_json(silent=True) or {})
        if not ok:
            return jsonify({'error': message}), 400
        return jsonify({'success': True, 'message': message, **remote.status()})
    return jsonify({'success': True, **remote.status()})


@bp.route('/api/v1/remote-access/devices', methods=['POST'])
@require_auth(require_admin=True)
def remote_add_device():
    """A new phone or computer: its configuration comes back once, never again."""
    from app_services import remote
    device, problem = remote.add_device(str((request.get_json(silent=True) or {}).get('name') or ''))
    if problem:
        return jsonify({'error': problem}), 400
    response = jsonify({'success': True, 'device': device})
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/api/v1/remote-access/devices/<device_id>', methods=['DELETE'])
@require_auth(require_admin=True)
def remote_remove_device(device_id):
    from app_services import remote
    ok, message = remote.remove_device(device_id)
    if not ok:
        return jsonify({'error': message}), 404 if 'not on the list' in message else 400
    return jsonify({'success': True, 'message': message})


@bp.route('/api/v1/remote-access/public-address', methods=['POST'])
@require_auth(require_admin=True)
def remote_public_address():
    """The internet address of the home: from the router when it says (UPnP),
    else from a public service (only on this button). With a warning when it
    cannot be reached from outside (shared by the provider, or a second router)."""
    import ipaddress

    import remote_access
    from app_services import remote
    router = remote.router()
    wan = router.get('wan_ip') or ''
    warning = remote_access.address_warning(wan) if wan else ''
    address = wan if wan and not warning else ''
    if not address:
        import requests
        try:
            text = requests.get('https://api.ipify.org', timeout=10).text.strip()
            ipaddress.ip_address(text)
            address = text
        except Exception:  # noqa: BLE001 - offline, blocked or a strange answer
            if not warning:
                return jsonify({'error': 'The public address could not be found. Look it up in your router.'}), 502
    return jsonify({'success': True, 'address': address, 'warning': warning})


@bp.route('/api/v1/remote-access/router', methods=['POST'])
@require_auth(require_admin=True)
def remote_open_router_port():
    """Ask the router to forward the port (UPnP)."""
    import remote_access
    from app_services import remote
    ok, message, router = remote.open_router_port()
    warning = remote_access.address_warning(router.get('wan_ip') or '')
    if not ok:
        return jsonify({'error': message, 'warning': warning}), 409
    return jsonify({'success': True, 'message': message, 'warning': warning, **remote.status()})
