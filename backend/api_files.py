"""AlvaOS Files as a built-in app: turned on and off under Apps.

The app itself is files_server.py (its own service on port 8090). Here the
admin only switches that service on or off, and sees who still needs their
password set once more to sign in there.
"""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth
from common import CMD, run_sudo_command

bp = Blueprint('files', __name__)

UNIT = 'alvaos-files.service'
PORT = 8090
HTTPS_PORT = 9443   # tls_manager.PORTS['files']


def _systemctl(*args):
    res, err = run_sudo_command([CMD['SYSTEMCTL'], *args, UNIT], timeout=30)
    return (res.stdout.strip() if res is not None and res.stdout else ''), err


def files_app_state():
    enabled, _ = _systemctl('is-enabled')
    active, _ = _systemctl('is-active')
    from api_auth import load_users_state
    waiting = sorted(name for name, info in load_users_state().items()
                     if isinstance(info, dict) and not info.get('files_auth'))
    return {'enabled': enabled == 'enabled', 'running': active == 'active', 'port': PORT, 'https_port': HTTPS_PORT,
            'people_without_password': waiting}


@bp.route('/api/v1/files-app', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def files_app():
    if request.method == 'POST':
        on = bool((request.get_json(silent=True) or {}).get('enabled'))
        _, err = _systemctl('enable' if on else 'disable', '--now')
        if err:
            return jsonify({'error': f'AlvaOS Files could not be turned {"on" if on else "off"}: {err}'}), 500
    return jsonify({'success': True, **files_app_state()})
