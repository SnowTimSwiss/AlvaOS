#!/usr/bin/env python3
"""SMB and NFS shares."""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import platform
import secrets
import subprocess
from datetime import datetime

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from common import (
    CMD, run_sudo_command, build_privileged_cmd,
)
from auth_manager import (
    require_auth,
)
from storage_manager import (
    is_path_on_system_disk,
)
from shares_manager import (
    ensure_samba_conf_exists, ensure_samba_global_settings, reconcile_samba_guest_settings,
    normalize_smb_permissions, apply_smb_permissions_to_fs,
    disable_samba_homes_share, render_smb_share_config,
    update_samba_share_section, load_shares_state, save_shares_state,
    validate_share_request, render_nfs_export,
)
from storage_manager import load_pools_state

from app_services import VERSION

bp = Blueprint('shares', __name__)

@bp.route('/api/v1/storage/shares', methods=['GET', 'POST', 'DELETE'])
@require_auth(require_admin=True)
def manage_shares():
    """Manage network shares (NFS and SMB)"""
    
    if request.method == 'GET':
        # Load shares from state file
        shares_state = load_shares_state()
        shares = list(shares_state.values())
        return jsonify({'shares': shares})
    
    elif request.method == 'POST':
        from api_auth import load_users_state  # avoids an import cycle at module load
        shares_state = load_shares_state()
        share, problem = validate_share_request(request.get_json(silent=True), load_pools_state(),
                                                shares_state, set(load_users_state()))
        if problem:
            return jsonify({'error': problem}), 400
        share_name, share_path, protocol = share['name'], share['path'], share['protocol']
        read_only, guest_access = share['read_only'], share['guest_access']
        smb_permissions = share['smb_permissions']

        try:
            share_id = f'share-{secrets.token_hex(4)}'

            if platform.system() == 'Linux':
                if share['new_folder'] and not os.path.exists(share_path):
                    _, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'create', share_path])
                    if err:
                        return jsonify({'error': f'Could not create the folder {share_path}: {err}'}), 500
                if not os.path.isdir(share_path):
                    return jsonify({'error': f'Folder does not exist: {share_path}'}), 400
                if is_path_on_system_disk(share_path):
                    return jsonify({'error': f'Share path is on the system disk and is not allowed: {share_path}'}), 400

                if protocol == 'nfs':
                    export_data = render_nfs_export(share_name, share_path, share['allowed_hosts'], read_only)
                    cmd = build_privileged_cmd([CMD['TEE'], '-a', '/etc/exports'])
                    subprocess.run(cmd, input=export_data, text=True, check=True, env={'LC_ALL': 'C'})
                    res, err = run_sudo_command([CMD['EXPORTFS'], '-ra'])
                    if err:
                        return jsonify({'error': f'Failed to reload NFS: {err}'}), 500

                elif protocol == 'smb':
                    ensure_samba_conf_exists()
                    ensure_samba_global_settings(guest_access)
                    disable_samba_homes_share()
                    smb_config = render_smb_share_config(
                        share_name, share_path, read_only, guest_access, smb_permissions)
                    cmd = build_privileged_cmd([CMD['TEE'], '-a', '/etc/samba/smb.conf'])
                    subprocess.run(cmd, input=smb_config, text=True, check=True, env={'LC_ALL': 'C'})
                    res, err = run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'smbd'])
                    if err:
                        return jsonify({'error': f'Failed to restart Samba: {err}'}), 500

            shares_state[share_id] = {
                'id': share_id,
                'name': share_name,
                'path': share_path,
                'protocol': protocol,
                'read_only': read_only,
                'guest_access': guest_access,
                'allowed_hosts': share['allowed_hosts'],
                'smb_permissions': smb_permissions,
                'smb_group': f'alvaos_{share_id}',
                'created_at': datetime.now().isoformat(),
                'status': 'active'
            }
            save_shares_state(shares_state)

            if platform.system() == 'Linux' and protocol == 'smb':
                apply_smb_permissions_to_fs(share_path, f'alvaos_{share_id}', smb_permissions, guest_access)
                reconcile_samba_guest_settings(shares_state)

            return jsonify({
                'success': True,
                'message': f'"{share_name}" is now shared on your network.',
                'share_id': share_id
            })

        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Share creation timed out'}), 500
        except PermissionError:
            return jsonify({'error': 'Permission denied. Backend needs sudo access.'}), 500
        except Exception as e:
            return jsonify({'error': f'Failed to create share: {str(e)}'}), 500

    elif request.method == 'DELETE':
        # Delete share
        data = request.get_json()
        share_id = data.get('share_id')
        
        if not share_id:
            return jsonify({'error': 'Share ID is required'}), 400
        
        try:
            shares_state = load_shares_state()
            
            if share_id not in shares_state:
                return jsonify({'error': 'Share not found'}), 404
            
            share_info = shares_state[share_id]
            protocol = share_info['protocol']
            share_name = share_info['name']
            share_path = share_info['path']
            
            if platform.system() == 'Linux':
                if protocol == 'nfs':
                    # Remove from /etc/exports using sudo
                    try:
                        # Read the file content via sudo
                        res, err = run_sudo_command([CMD['CAT'], '/etc/exports'])
                        if err or not res:
                            raise Exception(f"Could not read /etc/exports: {err}")
                        
                        lines = res.stdout.splitlines()
                        
                        # Filter out the share (comment line and the siguiente line)
                        new_lines = []
                        skip_next = False
                        for line in lines:
                            if f'# AlvaOS Share: {share_name}' in line:
                                skip_next = True
                                continue
                            if skip_next and share_path in line:
                                skip_next = False
                                continue
                            new_lines.append(line)
                        content = "\n".join(new_lines)
                        if content and not content.endswith('\n'):
                            content += '\n'
                        
                        # Write back using sudo tee
                        process = subprocess.Popen(build_privileged_cmd([CMD['TEE'], '/etc/exports']), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={'LC_ALL': 'C'})
                        _, tee_err = process.communicate(input=content)
                        if process.returncode != 0:
                            raise Exception(f"Could not write /etc/exports: {tee_err}")
                        
                        # Reload NFS exports
                        run_sudo_command([CMD['EXPORTFS'], '-ra'])
                    except Exception as e:
                        print(f"Error removing NFS export: {e}")
                
                elif protocol == 'smb':
                    # Remove from /etc/samba/smb.conf using sudo
                    try:
                        # Read the file content via sudo
                        res, err = run_sudo_command([CMD['CAT'], '/etc/samba/smb.conf'])
                        if err or not res:
                            raise Exception(f"Could not read /etc/samba/smb.conf: {err}")
                        
                        content = res.stdout
                        
                        # Find and remove the share section
                        import re
                        pattern = rf'# AlvaOS Share: {re.escape(share_name)}\n\[{re.escape(share_name)}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
                        content = re.sub(pattern, '', content, flags=re.DOTALL)
                        
                        # Write back using sudo tee
                        process = subprocess.Popen(build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf']), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={'LC_ALL': 'C'})
                        _, tee_err = process.communicate(input=content)
                        if process.returncode != 0:
                            raise Exception(f"Could not write /etc/samba/smb.conf: {tee_err}")
                        
                        # Restart Samba
                        run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'smbd'])
                    except Exception as e:
                        print(f"Error removing SMB share: {e}")
                    # Remove share group
                    try:
                        group_name = share_info.get('smb_group')
                        if group_name:
                            run_sudo_command([CMD['GROUPDEL'], group_name])
                    except Exception as e:
                        print(f"Error removing SMB group: {e}")
            
            # Remove from state
            del shares_state[share_id]
            save_shares_state(shares_state)
            if platform.system() == 'Linux':
                reconcile_samba_guest_settings(shares_state)
            
            return jsonify({'success': True, 'message': f'Share "{share_name}" deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete share: {str(e)}'}), 500

    return jsonify({
        'status': 'healthy',
        'version': VERSION,
    })

@bp.route('/api/v1/storage/shares/permissions', methods=['PUT'])
@require_auth(require_admin=True)
def update_share_permissions():
    """Update SMB permissions for a share"""
    data = request.get_json() or {}
    share_id = data.get('share_id', '').strip()
    if not share_id:
        return jsonify({'error': 'Share ID is required'}), 400

    smb_permissions = normalize_smb_permissions(data.get('smb_permissions', {}))
    shares_state = load_shares_state()
    if share_id not in shares_state:
        return jsonify({'error': 'Share not found'}), 404

    share_info = shares_state[share_id]
    if share_info.get('protocol') != 'smb':
        return jsonify({'error': 'Permissions apply to SMB shares only'}), 400

    from api_auth import load_users_state  # avoids an import cycle at module load
    unknown = sorted(u for u in smb_permissions if u not in load_users_state())
    if unknown:
        return jsonify({'error': f'Unknown user(s): {", ".join(unknown)}'}), 400
    guest_access = data.get('guest_access', share_info.get('guest_access', False)) is True
    if not guest_access and not any(r in ('read', 'write') for r in smb_permissions.values()):
        return jsonify({'error': 'Choose who can open this share: at least one person, '
                                 'or everyone on the network.'}), 400

    share_info['smb_permissions'] = smb_permissions
    share_info['guest_access'] = guest_access
    if 'read_only' in data:
        share_info['read_only'] = data.get('read_only') is True
    if not share_info.get('smb_group'):
        share_info['smb_group'] = f'alvaos_{share_id}'
    shares_state[share_id] = share_info
    save_shares_state(shares_state)

    if platform.system() == 'Linux':
        ensure_samba_conf_exists()
        ensure_samba_global_settings(share_info.get('guest_access', False))
        disable_samba_homes_share()
        new_config = render_smb_share_config(
            share_info.get('name'),
            share_info.get('path'),
            share_info.get('read_only', False),
            share_info.get('guest_access', False),
            smb_permissions
        )
        update_samba_share_section(share_info.get('name'), new_config)
        apply_smb_permissions_to_fs(
            share_info.get('path'),
            share_info.get('smb_group'),
            smb_permissions,
            share_info.get('guest_access', False)
        )
        reconcile_samba_guest_settings(shares_state)
        run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'smbd'])

    return jsonify({'success': True, 'message': f'Access to "{share_info.get("name")}" was updated.'})
