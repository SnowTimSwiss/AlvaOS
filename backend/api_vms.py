"""Virtual machines (the Virtual machines page). See vm_manager.py and docs/VMS.md."""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth

bp = Blueprint('vms', __name__)


def _data_pools():
    """Pools the machines' disks may live on: mounted data pools, not the system."""
    from storage_manager import load_pools_state
    return {pid: p for pid, p in load_pools_state().items()
            if isinstance(p, dict) and p.get('mount_point') and p.get('mount_point') != '/'}


def _make_store(pool_id):
    """The "VMs" shared folder on a pool, only the admin's: (path, error)."""
    import vm_manager
    from api_auth import load_users_state
    from shares_manager import load_shares_state
    existing = next((s for s in load_shares_state().values() if isinstance(s, dict) and s.get('vm_store')), None)
    if existing:
        return str(existing.get('path') or ''), ''
    if pool_id not in _data_pools():
        return '', 'Choose a pool for the virtual machines.'
    import api_shares
    payload, status = api_shares.create_share(
        {'name': vm_manager.STORE_SHARE, 'protocol': 'smb', 'pool_id': pool_id, 'folder': vm_manager.STORE_SHARE,
         'new_folder': True, 'smb_permissions': {}}, set(load_users_state()), extra={'vm_store': True})
    if status != 200:
        return '', str(payload.get('error') or 'The VMs folder could not be made.')
    share = load_shares_state().get(payload.get('share_id'), {})
    return str(share.get('path') or ''), ''


def _state():
    from app_services import vms
    return {'success': True, **vms.status(),
            'pools': [{'id': pid, 'name': p.get('name') or pid} for pid, p in sorted(_data_pools().items())]}


@bp.route('/api/v1/vms', methods=['GET'])
@require_auth(require_admin=True)
def vms_status():
    return jsonify(_state())


@bp.route('/api/v1/vms/setup', methods=['POST'])
@require_auth(require_admin=True)
def vms_setup():
    """Install what virtual machines need and make the VMs folder on a pool."""
    from app_services import vms
    pool_id = str((request.get_json(silent=True) or {}).get('pool_id') or '')
    ok, message = vms.setup(lambda: _make_store(pool_id))
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({**_state(), 'message': message})


@bp.route('/api/v1/vms', methods=['POST'])
@require_auth(require_admin=True)
def vms_create():
    from app_services import vms
    vm, problem = vms.create(request.get_json(silent=True) or {})
    if problem:
        return jsonify({'error': problem}), 400
    return jsonify({**_state(), 'vm': vm})


@bp.route('/api/v1/vms/isos', methods=['GET'])
@require_auth(require_admin=True)
def vms_isos():
    from app_services import vms
    isos, problem = vms.isos()
    if problem:
        return jsonify({'error': problem}), 409
    return jsonify({'success': True, 'isos': isos})


@bp.route('/api/v1/vms/devices', methods=['GET'])
@require_auth(require_admin=True)
def vms_devices():
    """USB devices and graphics cards that can be handed to a machine."""
    from app_services import vms
    return jsonify({'success': True, **vms.devices()})


@bp.route('/api/v1/vms/<vm_id>', methods=['POST'])
@require_auth(require_admin=True)
def vms_update(vm_id):
    from app_services import vms
    vm, problem = vms.update(vm_id, request.get_json(silent=True) or {})
    if problem:
        return jsonify({'error': problem}), 400
    return jsonify({**_state(), 'vm': vm})


@bp.route('/api/v1/vms/<vm_id>', methods=['DELETE'])
@require_auth(require_admin=True)
def vms_delete(vm_id):
    from app_services import vms
    ok, message = vms.delete(vm_id)
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({**_state(), 'message': message})


@bp.route('/api/v1/vms/<vm_id>/action', methods=['POST'])
@require_auth(require_admin=True)
def vms_action(vm_id):
    """start, stop (asks the guest to shut down), restart, force (switch off)."""
    from app_services import vms
    ok, message = vms.action(vm_id, str((request.get_json(silent=True) or {}).get('action') or ''))
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({**_state(), 'message': message})


@bp.route('/api/v1/vms/<vm_id>/console', methods=['POST'])
@require_auth(require_admin=True)
def vms_console(vm_id):
    """A one-time ticket to open the screen of a running machine (vm_console.py)."""
    from app_services import vms
    ticket, problem = vms.console(vm_id)
    if problem:
        return jsonify({'error': problem}), 409
    response = jsonify({'success': True, **(ticket or {})})
    response.headers['Cache-Control'] = 'no-store'
    return response
