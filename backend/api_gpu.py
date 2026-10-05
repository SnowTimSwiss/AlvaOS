"""Graphics cards and their drivers (Settings › Graphics). See gpu_manager.py."""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth

bp = Blueprint('gpu', __name__)


@bp.route('/api/v1/system/gpu', methods=['GET'])
@require_auth(require_admin=True)
def gpu_status():
    from app_services import gpu
    return jsonify({'success': True, **gpu.status()})


@bp.route('/api/v1/system/gpu/install', methods=['POST'])
@require_auth(require_admin=True)
def gpu_install():
    """Install the driver and firmware for one maker's cards (in the background)."""
    from app_services import gpu
    data = request.get_json(silent=True) or {}
    if data.get('repair') is True:
        ok, message = gpu.repair()
    else:
        ok, message = gpu.install(str(data.get('vendor') or ''))
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({'success': True, 'message': message})
