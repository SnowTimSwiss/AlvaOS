#!/usr/bin/env python3
"""App store and container management."""

# ── Standard library ──────────────────────────────────────────────────────────

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from auth_manager import (
    require_auth,
)

from app_store import normalize_port_mappings, validate_install_paths
from app_services import (
    docker_manager, app_store,
)

bp = Blueprint('apps', __name__)

@bp.route('/api/v1/apps/available', methods=['GET'])
@require_auth
def get_available_apps():
    """Get list of available apps from catalog"""
    apps, error = app_store.get_available_apps()
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'apps': apps})

@bp.route('/api/v1/apps/available/<app_id>', methods=['GET'])
@require_auth
def get_app_details_endpoint(app_id):
    """Get details for a specific app"""
    details, error = app_store.get_app_details(app_id)
    if error:
        return jsonify({'error': error}), 404
    return jsonify(details)

@bp.route('/api/v1/apps/install', methods=['POST'])
@require_auth(require_admin=True)
def install_app():
    """Install an app"""
    data = request.get_json() or {}
    
    app_id = data.get('app_id')
    pool_path = data.get('pool_path')
    parent_subvolume = data.get('parent_subvolume')
    port_mappings = data.get('port_mappings', {})
    volume_mappings = data.get('volume_mappings', {})
    environment_vars = data.get('environment_vars', {})
    
    if not app_id:
        return jsonify({'error': 'app_id is required'}), 400
    if not pool_path:
        return jsonify({'error': 'pool_path is required'}), 400
    
    port_mappings, port_error = normalize_port_mappings(port_mappings)
    if port_error:
        return jsonify({'error': port_error}), 400
    
    from storage_manager import load_pools_state
    problem = validate_install_paths(pool_path, volume_mappings, port_mappings, load_pools_state())
    if problem:
        return jsonify({'error': problem}), 400

    success, error = app_store.install_app(
        app_id=app_id,
        pool_path=pool_path,
        parent_subvolume=parent_subvolume,
        port_mappings=port_mappings,
        volume_mappings=volume_mappings,
        environment_vars=environment_vars
    )
    
    if not success:
        return jsonify({'error': error}), 500
    
    return jsonify({'success': True, 'message': f'Installation of "{app_id}" started'})

@bp.route('/api/v1/apps/install/compose', methods=['POST'])
@require_auth(require_admin=True)
def install_compose_app():
    """Install a custom app from a Docker Compose definition."""
    data = request.get_json() or {}

    app_name = str(data.get('app_name') or '').strip()
    app_id = str(data.get('app_id') or '').strip()
    pool_path = str(data.get('pool_path') or '').strip()
    parent_subvolume = data.get('parent_subvolume')
    compose_yaml = data.get('compose_yaml')
    compose_object = data.get('compose')

    if not app_name:
        return jsonify({'error': 'app_name is required'}), 400
    if not pool_path:
        return jsonify({'error': 'pool_path is required'}), 400
    if parent_subvolume is not None and not isinstance(parent_subvolume, str):
        return jsonify({'error': 'parent_subvolume must be a string when provided'}), 400

    compose_config = None
    if compose_object is not None:
        if not isinstance(compose_object, dict):
            return jsonify({'error': 'compose must be an object'}), 400
        compose_config = compose_object
    elif isinstance(compose_yaml, str):
        import yaml
        try:
            compose_config = yaml.safe_load(compose_yaml)
        except Exception as e:
            return jsonify({'error': f'Invalid compose_yaml: {str(e)}'}), 400
    else:
        return jsonify({'error': 'Either compose_yaml or compose is required'}), 400

    if not isinstance(compose_config, dict):
        return jsonify({'error': 'Compose content must evaluate to an object'}), 400
    services = compose_config.get('services')
    if not isinstance(services, dict) or not services:
        return jsonify({'error': 'Compose config must contain at least one service in services'}), 400

    success, error, resolved_app_id = app_store.install_compose_app(
        app_name=app_name,
        pool_path=pool_path,
        compose_config=compose_config,
        parent_subvolume=(str(parent_subvolume).strip() if isinstance(parent_subvolume, str) else None),
        app_id=(app_id if app_id else None)
    )
    if not success:
        error_text = str(error or 'Failed to start compose installation')
        if (
            'already installed' in error_text.lower()
            or 'required' in error_text.lower()
            or 'must be' in error_text.lower()
            or 'another app operation is already in progress' in error_text.lower()
            or 'operation for' in error_text.lower()
        ):
            return jsonify({'error': error_text}), 400
        return jsonify({'error': error_text}), 500

    return jsonify({
        'success': True,
        'app_id': resolved_app_id,
        'message': f'Installation of "{app_name}" started'
    })

@bp.route('/api/v1/apps/install/status', methods=['GET'])
@require_auth
def get_app_install_status():
    """Get the current app installation status"""
    return jsonify(app_store.get_install_status())

@bp.route('/api/v1/apps/<app_id>/update', methods=['POST'])
@require_auth(require_admin=True)
def update_app(app_id):
    """Update an installed app"""
    success, error = app_store.update_app(app_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': f'Update of "{app_id}" started'})

@bp.route('/api/v1/apps/<app_id>/settings', methods=['POST'])
@require_auth(require_admin=True)
def reconfigure_app(app_id):
    """Change the folders and ports of an installed app."""
    data = request.get_json(silent=True) or {}
    port_mappings, port_error = normalize_port_mappings(data.get('port_mappings') or {})
    if port_error:
        return jsonify({'error': port_error}), 400
    volume_mappings = data.get('volume_mappings') or {}
    installed = {a.get('app_id'): a for a in app_store.get_installed_apps()}
    if app_id not in installed:
        return jsonify({'error': f'App "{app_id}" is not installed'}), 404

    from storage_manager import load_pools_state
    problem = validate_install_paths(installed[app_id].get('pool_path'), volume_mappings, port_mappings,
                                     load_pools_state())
    if problem:
        return jsonify({'error': problem}), 400

    success, error = app_store.reconfigure_app(app_id, port_mappings, volume_mappings)
    if not success:
        return jsonify({'error': error}), 409
    return jsonify({'success': True, 'message': f'New settings for "{app_id}" are being applied'})

@bp.route('/api/v1/apps/<app_id>/gpu', methods=['POST'])
@require_auth(require_admin=True)
def app_gpu(app_id):
    """Give an installed app the graphics card ({"on": true}) or take it away."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('on'), bool):
        return jsonify({'error': 'Say whether the app should use the graphics card.'}), 400
    success, error = app_store.set_gpu(app_id, data['on'])
    if not success:
        return jsonify({'error': error}), 409
    return jsonify({'success': True})

@bp.route('/api/v1/apps/<app_id>', methods=['DELETE'])
@require_auth(require_admin=True)
def uninstall_app(app_id):
    """Uninstall an app"""
    data = request.get_json() or {}
    keep_data = data.get('keep_data', False)
    
    success, error = app_store.uninstall_app(app_id, keep_data=keep_data)
    if not success:
        return jsonify({'error': error}), 500
    
    return jsonify({'success': True, 'message': f'App "{app_id}" uninstalled successfully'})

@bp.route('/api/v1/apps/installed', methods=['GET'])
@require_auth
def get_installed_apps():
    """Get list of installed apps"""
    apps = app_store.get_installed_apps()
    return jsonify({'apps': apps})

@bp.route('/api/v1/apps/<app_id>/update-status', methods=['GET'])
@require_auth
def get_app_update_status(app_id):
    """Get update availability for a single installed app."""
    force_refresh = request.args.get('force_refresh', '').strip().lower() in ('1', 'true', 'yes')
    status, error = app_store.get_app_update_status(app_id, force_refresh=force_refresh)
    if error:
        return jsonify({'error': error}), 500
    return jsonify(status or {})

# Container Management
@bp.route('/api/v1/containers', methods=['GET'])
@require_auth
def list_containers():
    """List all Docker containers"""
    containers, error = docker_manager.list_containers(all_containers=True)
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'containers': containers})

@bp.route('/api/v1/containers/<container_id>', methods=['GET'])
@require_auth
def get_container_details_endpoint(container_id):
    """Get details for a specific container"""
    details, error = docker_manager.get_container_details(container_id)
    if error:
        return jsonify({'error': error}), 404
    return jsonify(details)

@bp.route('/api/v1/containers/<container_id>/start', methods=['POST'])
@require_auth(require_admin=True)
def start_container(container_id):
    """Start a container"""
    success, error = docker_manager.start_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container started'})

@bp.route('/api/v1/containers/<container_id>/stop', methods=['POST'])
@require_auth(require_admin=True)
def stop_container(container_id):
    """Stop a container"""
    success, error = docker_manager.stop_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container stopped'})

@bp.route('/api/v1/containers/<container_id>/restart', methods=['POST'])
@require_auth(require_admin=True)
def restart_container(container_id):
    """Restart a container"""
    success, error = docker_manager.restart_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container restarted'})

@bp.route('/api/v1/containers/<container_id>/logs', methods=['GET'])
@require_auth
def get_container_logs(container_id):
    """Get container logs"""
    lines = request.args.get('lines', 100, type=int)
    logs, error = docker_manager.get_container_logs(container_id, lines=lines)
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'logs': logs})

@bp.route('/api/v1/containers/<container_id>/exec', methods=['POST'])
@require_auth(require_admin=True)
def exec_container_command(container_id):
    """Execute a command inside a container shell."""
    data = request.get_json() or {}
    command = str(data.get('command', '')).strip()
    timeout = data.get('timeout', 60)
    user = data.get('user')
    workdir = data.get('workdir')

    if not command:
        return jsonify({'error': 'Command is required'}), 400

    try:
        timeout = int(timeout)
    except Exception:
        return jsonify({'error': 'timeout must be an integer'}), 400

    if timeout < 1 or timeout > 300:
        return jsonify({'error': 'timeout must be between 1 and 300 seconds'}), 400

    result, error = docker_manager.exec_in_container(
        container_id=container_id,
        command=command,
        timeout=timeout,
        user=(str(user).strip() if user is not None else None),
        workdir=(str(workdir).strip() if workdir is not None else None)
    )
    if error:
        return jsonify({'error': error}), 500

    return jsonify(result)

@bp.route('/api/v1/containers/<container_id>', methods=['DELETE'])
@require_auth(require_admin=True)
def delete_container(container_id):
    """Delete a container"""
    force = request.args.get('force', 'false').lower() == 'true'
    success, error = docker_manager.remove_container(container_id, force=force)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container deleted'})

@bp.route('/api/v1/docker/status', methods=['GET'])
@require_auth
def get_docker_status():
    """Check if Docker is running"""
    is_running, error = docker_manager.check_docker_running()
    return jsonify({
        'running': is_running,
        'error': error
    })
