#!/usr/bin/env python3
"""AlvaOS, Debian and offline updates."""

# ── Standard library ──────────────────────────────────────────────────────────

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from auth_manager import (
    require_auth,
)

from app_services import (
    update_manager,
)

bp = Blueprint('updates', __name__)

@bp.route('/api/v1/updates/alvaos/check', methods=['GET'])
@require_auth
def check_alvaos_updates():
    channel = request.args.get('channel', 'stable')
    force_raw = str(request.args.get('force', '') or '').strip().lower()
    force_refresh = force_raw in ('1', 'true', 'yes', 'on')
    result = update_manager.check_alvaos_updates(channel, force_refresh=force_refresh)
    return jsonify(result), 200

@bp.route('/api/v1/updates/alvaos/apply', methods=['POST'])
@require_auth(require_admin=True)
def apply_alvaos_update():
    data = request.get_json() or {}
    url = data.get('url')
    version = data.get('version', '')
    package_path = data.get('package_path')

    try:
        if url:
            download = update_manager.download_update(version, url)
            package_path = download.get('path')

        if not package_path:
            return jsonify({'success': False, 'error': 'package_path or url required'}), 400

        result = update_manager.apply_alvaos_update(package_path)
        status = 200 if result.get('success') else 500
        return jsonify(result), status
    except Exception as e:
        update_manager.set_update_state("error", "Install failed", {"error": str(e)})
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/v1/updates/debian/check', methods=['GET'])
@require_auth
def check_debian_updates():
    result = update_manager.check_debian_updates()
    status = 200 if 'error' not in result else 500
    return jsonify(result), status

@bp.route('/api/v1/updates/debian/apply', methods=['POST'])
@require_auth(require_admin=True)
def apply_debian_updates():
    data = request.get_json() or {}
    packages = data.get('packages')
    result = update_manager.apply_debian_updates(packages)
    status = 200 if result.get('success') else 500
    return jsonify(result), status

@bp.route('/api/v1/updates/debian/os-upgrade', methods=['POST'])
@require_auth(require_admin=True)
def apply_debian_os_upgrade():
    data = request.get_json() or {}
    target_codename = data.get('target_codename')
    result = update_manager.apply_debian_os_upgrade(target_codename=target_codename)
    status = 200 if result.get('success') else 500
    return jsonify(result), status

@bp.route('/api/v1/updates/offline/scan', methods=['POST'])
@require_auth
def scan_offline_updates():
    data = request.get_json() or {}
    path = data.get('path')
    packages = []
    packages = update_manager.scan_offline_packages(path).get('packages', [])
    return jsonify({'packages': packages})

@bp.route('/api/v1/updates/offline/apply', methods=['POST'])
@require_auth(require_admin=True)
def apply_offline_update():
    data = request.get_json() or {}
    package_path = data.get('path')
    if not package_path:
        return jsonify({'success': False, 'error': 'path required'}), 400
    if update_manager.classify_offline_package(package_path) == 'alvaos':
        result = update_manager.apply_alvaos_update(package_path)
    else:
        result = update_manager.apply_offline_system_package(package_path)
    status = 200 if result.get('success') else 500
    return jsonify(result), status

@bp.route('/api/v1/updates/rollback', methods=['GET'])
@require_auth
def list_rollback_versions():
    return jsonify({
        'current_version': update_manager.get_current_version(),
        'versions': update_manager.list_rollback_candidates(),
    })

@bp.route('/api/v1/updates/rollback', methods=['POST'])
@require_auth(require_admin=True)
def rollback_alvaos():
    data = request.get_json(silent=True) or {}
    result = update_manager.rollback_to(data.get('version'))
    if result.get('success'):
        return jsonify(result), 200
    status = 404 if 'not stored' in str(result.get('error', '')) else 500
    return jsonify(result), status

@bp.route('/api/v1/updates/status', methods=['GET'])
@require_auth
def get_update_status():
    return jsonify(update_manager.get_update_state())

@bp.route('/api/v1/updates/history', methods=['GET'])
@require_auth
def get_update_history():
    return jsonify({'history': update_manager.get_update_history()})

@bp.route('/api/v1/updates/settings', methods=['GET'])
@require_auth
def get_update_settings():
    return jsonify(update_manager.get_settings())

@bp.route('/api/v1/updates/settings', methods=['POST'])
@require_auth(require_admin=True)
def save_update_settings():
    data = request.get_json() or {}
    settings = {
        "auto_check": bool(data.get("auto_check", True)),
        "auto_apply": bool(data.get("auto_apply", False)),
        "auto_apply_debian": bool(data.get("auto_apply_debian", False)),
        "channel": data.get("channel", "stable")
    }
    saved = update_manager.save_settings(settings)
    return jsonify(saved)

# ============================================================================
# STORAGE MANAGEMENT ENDPOINTS (v0.2.0)
# ============================================================================
