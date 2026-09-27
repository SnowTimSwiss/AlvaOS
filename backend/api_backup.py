#!/usr/bin/env python3
"""Local snapshots and Buddy Backup."""

# ── Standard library ──────────────────────────────────────────────────────────
import os

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request, send_from_directory

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from auth_manager import (
    require_auth,
)

from app_services import (
    backup_manager, buddy_backup_manager,
)
from buddy_vault import VaultError

bp = Blueprint('backup', __name__)


def _authenticated_buddy():
    """Identify the buddy behind a peer-to-peer request, or return an error response.

    Two checks: the request carries this NAS's buddy secret, and it arrives
    through the WireGuard tunnel from the tunnel address of a paired buddy.
    The secret is shared with every buddy, so on its own it cannot tell them
    apart; the tunnel address can, because WireGuard only accepts traffic
    from it when it is signed with that buddy's key.
    """
    if buddy_backup_manager is None:
        return None, (jsonify({'error': 'Buddy backup manager not initialized'}), 500)
    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return None, (jsonify({'error': 'Unauthorized buddy request'}), 403)
    peer = buddy_backup_manager.peer_for_tunnel_ip(request.remote_addr or '')
    if peer is None:
        return None, (jsonify({'error': 'Buddy requests are only accepted through the buddy tunnel'}), 403)
    return peer, None


def _own_node_id(peer, claimed):
    """A buddy may only act on its own snapshots."""
    node_id = str(peer.get('node_id') or '')
    claimed = (claimed or '').strip()
    if claimed and claimed != node_id:
        return None
    return node_id

@bp.route('/api/v1/backup/sources', methods=['GET'])
@require_auth
def get_backup_sources():
    """Get available backup sources from known Btrfs pools/subvolumes."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'sources': backup_manager.get_sources()})

@bp.route('/api/v1/backup/targets', methods=['GET'])
@require_auth
def get_backup_targets():
    """Get available pool/subvolume target locations for snapshot storage."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'targets': backup_manager.get_target_locations()})

@bp.route('/api/v1/backup/snapshots', methods=['GET', 'POST', 'DELETE'])
@require_auth(require_admin=True)
def backup_snapshots():
    """List snapshots or create a new snapshot."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    if request.method == 'GET':
        source_path = (request.args.get('source_path') or '').strip() or None
        snapshot_class = (request.args.get('snapshot_class') or '').strip().lower() or None
        if snapshot_class and snapshot_class not in ('data', 'system', 'full_data'):
            return jsonify({'error': 'Invalid snapshot_class'}), 400
        snapshots = backup_manager.list_snapshots(source_path=source_path, snapshot_class=snapshot_class)
        return jsonify({'snapshots': snapshots})

    if request.method == 'DELETE':
        data = request.get_json(silent=True) or {}
        snapshot_path = (data.get('snapshot_path') or request.args.get('snapshot_path') or '').strip()
        if not snapshot_path:
            return jsonify({'error': 'snapshot_path is required'}), 400

        success, payload = backup_manager.delete_snapshot(snapshot_path=snapshot_path)
        if not success:
            return jsonify({'error': payload.get('error', 'Failed to delete snapshot')}), 400
        return jsonify({'success': True, 'result': payload})

    data = request.get_json() or {}
    source_path = (data.get('source_path') or '').strip()
    label = (data.get('label') or '').strip() or None
    target_path = (data.get('target_path') or '').strip() or None
    if not source_path:
        return jsonify({'error': 'source_path is required'}), 400

    success, payload = backup_manager.create_snapshot(
        source_path=source_path,
        label=label,
        trigger='manual',
        target_path=target_path
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to create snapshot')}), 500

    return jsonify({'success': True, 'snapshot': payload})

@bp.route('/api/v1/backup/system/snapshots', methods=['GET'])
@require_auth
def backup_system_snapshots():
    """List full system snapshots."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'snapshots': backup_manager.list_system_snapshots()})

@bp.route('/api/v1/backup/system/snapshot', methods=['POST'])
@require_auth(require_admin=True)
def backup_system_snapshot():
    """Create full system snapshot."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    data = request.get_json() or {}
    label = (data.get('label') or '').strip() or None
    target_path = (data.get('target_path') or '').strip() or None

    success, payload = backup_manager.create_system_snapshot(
        label=label,
        trigger='manual',
        target_path=target_path
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to create system snapshot')}), 500
    return jsonify({'success': True, 'snapshot': payload})

@bp.route('/api/v1/backup/system/rollback', methods=['POST'])
@require_auth(require_admin=True)
def backup_system_rollback():
    """Prepare full system rollback by switching Btrfs default subvolume."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    data = request.get_json() or {}
    snapshot_path = (data.get('snapshot_path') or '').strip()
    if not snapshot_path:
        return jsonify({'error': 'snapshot_path is required'}), 400
    if buddy_backup_manager is not None and buddy_backup_manager.requires_encryption_passphrase():
        passphrase = str(data.get('encryption_passphrase') or '')
        if not passphrase:
            return jsonify({'error': 'Encryption password is required for rollback'}), 400
        if not buddy_backup_manager.verify_encryption_passphrase(passphrase):
            return jsonify({'error': 'Invalid encryption password'}), 403

    success, payload = backup_manager.rollback_system_snapshot(snapshot_path=snapshot_path)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to prepare system rollback')}), 500
    return jsonify({'success': True, 'result': payload})

@bp.route('/api/v1/backup/restore', methods=['POST'])
@require_auth(require_admin=True)
def backup_restore():
    """Restore (rollback) a source from a selected snapshot."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    data = request.get_json() or {}
    snapshot_path = (data.get('snapshot_path') or '').strip()
    source_path = (data.get('source_path') or '').strip() or None
    if not snapshot_path:
        return jsonify({'error': 'snapshot_path is required'}), 400
    if buddy_backup_manager is not None and buddy_backup_manager.requires_encryption_passphrase():
        passphrase = str(data.get('encryption_passphrase') or '')
        if not passphrase:
            return jsonify({'error': 'Encryption password is required for rollback'}), 400
        if not buddy_backup_manager.verify_encryption_passphrase(passphrase):
            return jsonify({'error': 'Invalid encryption password'}), 403

    success, payload = backup_manager.restore_snapshot(snapshot_path=snapshot_path, source_path=source_path)
    if not success:
        return jsonify({'error': payload.get('error', 'Restore failed')}), 500
    return jsonify({'success': True, 'result': payload})

@bp.route('/api/v1/backup/settings', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def backup_settings():
    """Get or save backup schedule settings."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    if request.method == 'GET':
        return jsonify({
            'settings': backup_manager.get_settings(),
            'status': backup_manager.get_status()
        })

    data = request.get_json() or {}
    settings = backup_manager.save_settings(data)
    return jsonify({
        'success': True,
        'settings': settings,
        'status': backup_manager.get_status()
    })

@bp.route('/api/v1/backup/run', methods=['POST'])
@require_auth(require_admin=True)
def backup_run_now():
    """Trigger immediate snapshot run for configured or provided sources."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    data = request.get_json() or {}
    backup_type = data.get('backup_type', 'pool')
    sources = data.get('sources')
    target_path = (data.get('target_path') or '').strip() or None
    
    result = backup_manager.run_backup_now(
        backup_type=backup_type,
        sources=sources,
        trigger='manual',
        target_path=target_path
    )
    if not result.get('success') and not result.get('created'):
        return jsonify({'error': result.get('error', 'Backup run failed'), 'result': result}), 500
    return jsonify(result)

@bp.route('/api/v1/backup/status', methods=['GET'])
@require_auth
def backup_status():
    """Get last run / next run backup status."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({
        'status': backup_manager.get_status(),
        'system': backup_manager.get_system_state()
    })

@bp.route('/api/v1/backup/pairing/status', methods=['GET'])
@require_auth
def buddy_pairing_status():
    """Get Buddy Backup pairing and tunnel status."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500
    return jsonify(buddy_backup_manager.get_status())

@bp.route('/api/v1/backup/buddy/settings', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def buddy_settings():
    """Get or update Buddy Backup configuration (pairing + scheduling metadata only)."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    if request.method == 'GET':
        return jsonify({'success': True, 'settings': buddy_backup_manager.get_settings()})

    payload = request.get_json() or {}
    success, result = buddy_backup_manager.save_settings(payload)
    if not success:
        return jsonify({'error': result.get('error', 'Failed to save buddy settings')}), 400
    return jsonify({'success': True, 'settings': result})

@bp.route('/api/v1/backup/buddy/peers/<node_id>/policy', methods=['GET', 'POST'])
@require_auth
def buddy_peer_policy(node_id):
    """Get or update send schedule/quota policy for a specific buddy peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    if request.method == 'GET':
        success, payload = buddy_backup_manager.get_peer_policy(node_id=node_id)
        if not success:
            return jsonify({'error': payload.get('error', 'Failed to load peer policy')}), 404
        return jsonify({'success': True, 'policy': payload})

    data = request.get_json() or {}
    success, payload = buddy_backup_manager.save_peer_policy(node_id=node_id, payload=data)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to save peer policy')}), 400
    return jsonify({'success': True, 'policy': payload})

@bp.route('/api/v1/backup/pairing/generate', methods=['POST'])
@require_auth(require_admin=True)
def buddy_pairing_generate():
    """Generate a short-lived buddy pairing token."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    endpoint = (data.get('endpoint') or '').strip()
    expires_minutes = data.get('expires_minutes', 20)
    try:
        identity = buddy_backup_manager._identity_public()
        listen_port = int(identity.get('listen_port') or 51820)
    except Exception:
        listen_port = 51820
    listen_port = max(1024, min(65535, listen_port))

    host_header = (request.headers.get('X-Forwarded-Host') or request.host or '').split(',')[0].strip()
    api_endpoint = host_header
    if not endpoint:
        host = host_header.split(':', 1)[0].strip()
        if host and host not in ('localhost', '127.0.0.1', '::1'):
            endpoint = f"{host}:{listen_port}"

    success, payload = buddy_backup_manager.generate_pairing_token(
        endpoint=endpoint,
        expires_minutes=expires_minutes,
        api_endpoint=api_endpoint,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to generate pairing token')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/validate', methods=['POST'])
@require_auth(require_admin=True)
def buddy_pairing_validate():
    """Validate and store a buddy pairing token."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    endpoint_override = (data.get('endpoint_override') or '').strip()
    name_override = (data.get('name_override') or '').strip()
    try:
        identity = buddy_backup_manager._identity_public()
        listen_port = int(identity.get('listen_port') or 51820)
    except Exception:
        listen_port = 51820
    listen_port = max(1024, min(65535, listen_port))
    host_header = (request.headers.get('X-Forwarded-Host') or request.host or '').split(',')[0].strip()
    local_api_endpoint = host_header
    local_host = host_header.split(':', 1)[0].strip()
    local_wg_endpoint = ''
    if local_host and local_host not in ('localhost', '127.0.0.1', '::1'):
        local_wg_endpoint = f'{local_host}:{listen_port}'

    success, payload = buddy_backup_manager.validate_pairing_token(
        token=token,
        endpoint_override=endpoint_override,
        name_override=name_override,
        auto_reciprocal=True,
        local_wg_endpoint=local_wg_endpoint,
        local_api_endpoint=local_api_endpoint,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Pairing failed')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/accept', methods=['POST'])
def buddy_pairing_accept():
    """Accept reciprocal buddy pairing without interactive login."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    # The remote proves it received this NAS's pairing code: that code
    # carries the buddy secret. Without this, anyone who can reach the port
    # could register themselves as a buddy.
    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return jsonify({'error': 'Unauthorized pairing request'}), 403

    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'token is required'}), 400

    success, payload = buddy_backup_manager.validate_pairing_token(
        token=token,
        auto_reciprocal=False
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Pairing accept failed')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/remove', methods=['POST'])
@require_auth(require_admin=True)
def buddy_pairing_remove():
    """Remove an existing buddy peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    node_id = (data.get('node_id') or '').strip()
    success, payload = buddy_backup_manager.remove_peer(node_id=node_id)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to remove peer')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/remove/accept', methods=['POST'])
def buddy_pairing_remove_accept():
    """Accept reciprocal buddy unpair request without interactive login."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response

    data = request.get_json() or {}
    node_id = _own_node_id(peer, data.get('node_id'))
    if not node_id:
        return jsonify({'error': 'A buddy can only remove its own pairing'}), 403

    success, payload = buddy_backup_manager.remove_peer(
        node_id=node_id,
        reciprocal=False,
        allow_missing=True,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to remove peer')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/restart', methods=['POST'])
@require_auth(require_admin=True)
def buddy_pairing_restart():
    """Apply buddy tunnel configuration and restart tunnel."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    success, payload = buddy_backup_manager.restart_tunnel()
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to restart tunnel')}), 500
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/pairing/test', methods=['POST'])
@require_auth
def buddy_pairing_test():
    """Test connectivity for a configured buddy peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    node_id = (data.get('node_id') or '').strip()
    success, payload = buddy_backup_manager.test_peer_connection(node_id=node_id)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to test buddy connection')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/sync', methods=['POST'])
@require_auth(require_admin=True)
def buddy_sync_now():
    """Run buddy transfer sync for one peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    node_id = (data.get('node_id') or '').strip()
    sources = data.get('sources')
    include_system = bool(data.get('system'))
    if sources is not None and not isinstance(sources, list):
        return jsonify({'error': 'sources must be a list'}), 400

    success, payload = buddy_backup_manager.sync_to_peer(
        node_id=node_id,
        sources=sources,
        include_system=include_system,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Buddy sync failed')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/remote/snapshots', methods=['GET', 'POST'])
@require_auth
def buddy_remote_snapshots():
    """List this NAS's snapshots on a buddy.

    GET shows what was saved at the last sync. POST opens the vault on the
    buddy to look now; a replacement NAS passes the encryption password once.
    """
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = (request.get_json(silent=True) or {}) if request.method == 'POST' else {}
    node_id = (data.get('node_id') or request.args.get('node_id') or '').strip()
    success, payload = buddy_backup_manager.fetch_remote_snapshots(
        node_id=node_id, limit=200, refresh=request.method == 'POST',
        passphrase=str(data.get('encryption_passphrase') or ''),
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to load remote snapshots')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/remote/snapshot', methods=['DELETE'])
@require_auth(require_admin=True)
def buddy_remote_snapshot_delete():
    """Delete one snapshot stream stored on a remote buddy for this node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json(silent=True) or {}
    node_id = (data.get('node_id') or request.args.get('node_id') or '').strip()
    stream_id = (data.get('stream_id') or request.args.get('stream_id') or '').strip()
    if not node_id:
        return jsonify({'error': 'node_id is required'}), 400
    if not stream_id:
        return jsonify({'error': 'stream_id is required'}), 400

    success, payload = buddy_backup_manager.delete_remote_snapshot(
        node_id=node_id, stream_id=stream_id, passphrase=str(data.get('encryption_passphrase') or ''),
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to delete remote snapshot')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/recovery-kit', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def buddy_recovery_kit():
    """GET: whether a current recovery kit exists. POST: create one."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500
    if request.method == 'GET':
        return jsonify(buddy_backup_manager.recovery_kit_status())
    data = request.get_json() or {}
    success, payload = buddy_backup_manager.export_recovery_kit(str(data.get('passphrase') or ''))
    if not success:
        return jsonify({'error': payload.get('error', 'Could not create the recovery kit')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/recovery-kit/import', methods=['POST'])
@require_auth(require_admin=True)
def buddy_recovery_kit_import():
    """Make this NAS the node described by a recovery kit (disaster recovery)."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500
    data = request.get_json() or {}
    success, payload = buddy_backup_manager.import_recovery_kit(
        kit=str(data.get('kit') or ''),
        passphrase=str(data.get('passphrase') or ''),
        replace=bool(data.get('replace')),
    )
    if not success:
        status = 409 if payload.get('needs_confirmation') else 400
        return jsonify(payload), status
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/restore/remote', methods=['POST'])
@require_auth(require_admin=True)
def buddy_remote_restore():
    """Restore local data from a remote buddy snapshot stream."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    node_id = (data.get('node_id') or '').strip()
    stream_id = (data.get('stream_id') or '').strip()
    source_path = (data.get('source_path') or '').strip()
    encryption_passphrase = str(data.get('encryption_passphrase') or '')

    success, payload = buddy_backup_manager.restore_from_remote_snapshot(
        node_id=node_id,
        stream_id=stream_id,
        source_path=source_path,
        encryption_passphrase=encryption_passphrase,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Remote restore failed')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/peer/vault', methods=['GET', 'POST', 'DELETE'])
def buddy_peer_vault():
    """The encrypted vault a paired buddy keeps on this NAS.

    POST creates it (or grows it after the quota was raised), GET describes
    it, DELETE removes it. The buddy attaches it over NBD through the tunnel;
    this NAS only ever sees encrypted blocks.
    """
    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response
    owner = str(peer.get('node_id') or '')
    try:
        if request.method == 'POST':
            return jsonify({'success': True, **buddy_backup_manager.vault_store.ensure(owner)})
        if request.method == 'DELETE':
            return jsonify({'success': True, 'deleted': buddy_backup_manager.vault_store.delete(owner)})
        return jsonify({'success': True, **buddy_backup_manager.vault_store.info(owner)})
    except (VaultError, OSError) as exc:
        return jsonify({'error': str(exc)}), 507 if getattr(exc, 'errno', None) == 28 else 400


@bp.route('/api/v1/backup/buddy/peer/vault/key', methods=['PUT'])
def buddy_peer_vault_key():
    """Keep the buddy's sealed vault key (useless without its encryption password)."""
    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response
    data = request.get_json(silent=True) or {}
    try:
        buddy_backup_manager.vault_store.store_key(str(peer.get('node_id') or ''), str(data.get('key_blob') or ''))
    except (VaultError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})

@bp.route('/api/v1/backup/buddy/peer/list', methods=['GET'])
def buddy_peer_list():
    """List snapshot streams stored for a specific owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response

    owner_node_id = _own_node_id(peer, request.args.get('owner_node_id'))
    if not owner_node_id:
        return jsonify({'error': "A buddy can only access its own snapshots"}), 403
    limit = request.args.get('limit', 100)
    try:
        limit_int = int(limit)
    except Exception:
        limit_int = 100
    success, payload = buddy_backup_manager.list_peer_streams(owner_node_id=owner_node_id, limit=limit_int)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to list streams')}), 400
    return jsonify({'success': True, **payload})

@bp.route('/api/v1/backup/buddy/peer/download/<stream_id>', methods=['GET'])
def buddy_peer_download(stream_id):
    """Download one snapshot stream stored for an owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response

    owner_node_id = _own_node_id(peer, request.args.get('owner_node_id'))
    if not owner_node_id:
        return jsonify({'error': "A buddy can only access its own snapshots"}), 403
    success, payload = buddy_backup_manager.get_peer_stream_payload(
        owner_node_id=owner_node_id,
        stream_id=stream_id,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to resolve stream payload')}), 404

    file_path = payload.get('payload_path')
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Stream payload not found'}), 404

    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    return send_from_directory(directory, filename, as_attachment=True)

@bp.route('/api/v1/backup/buddy/peer/delete/<stream_id>', methods=['DELETE'])
def buddy_peer_delete(stream_id):
    """Delete one snapshot stream payload stored for an owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    peer, error_response = _authenticated_buddy()
    if error_response:
        return error_response

    owner_node_id = _own_node_id(peer, request.args.get('owner_node_id'))
    if not owner_node_id:
        return jsonify({'error': "A buddy can only access its own snapshots"}), 403
    success, payload = buddy_backup_manager.delete_peer_stream(
        owner_node_id=owner_node_id,
        stream_id=stream_id,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to delete stream')}), 404
    return jsonify({'success': True, 'deleted': payload.get('stream', {}),
                    'payload_removed': payload.get('payload_removed', False),
                    'removed_ids': payload.get('removed_ids', [])})

# ============================================================================
# APP STORE & CONTAINER MANAGEMENT API
# ============================================================================
