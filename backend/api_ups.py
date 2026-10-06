"""A UPS on USB (Settings › Power › Battery backup). See ups_nut.py."""

from flask import Blueprint, jsonify, request

from auth_manager import require_auth

bp = Blueprint('ups', __name__)


@bp.route('/api/v1/system/ups', methods=['GET'])
@require_auth(require_admin=True)
def ups_status():
    from app_services import ups
    return jsonify({'success': True, **ups.status()})


@bp.route('/api/v1/system/ups', methods=['POST'])
@require_auth(require_admin=True)
def ups_change():
    """Set up a connected UPS, change when the NAS shuts down, or turn it off."""
    from app_services import ups
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    minutes = data.get('shutdown_after_minutes', 0)
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        return jsonify({'error': 'Choose when the NAS shuts down.'}), 400
    if action == 'set_up':
        ok, message = ups.set_up(str(data.get('vendorid') or ''), str(data.get('productid') or ''), minutes)
    elif action == 'change':
        ok, message = ups.change(minutes)
    elif action == 'off':
        ok, message = ups.turn_off()
    else:
        return jsonify({'error': 'Unknown action.'}), 400
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({'success': True, 'message': message, **ups.status()})
