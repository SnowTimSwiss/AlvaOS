#!/usr/bin/env python3
"""
AlvaOS Shares Manager
Samba/NFS share configuration, user helpers, and filesystem permissions.
"""

import json
import os
import platform
import re
import subprocess

from common import CMD, run_sudo_command, build_privileged_cmd, ensure_directories

# ── State file ────────────────────────────────────────────────────────────────
SHARES_STATE_FILE = '/var/lib/alvaos/shares.json'


# ── User helpers ──────────────────────────────────────────────────────────────

def is_valid_username(username):
    return bool(re.match(r'^[a-z_][a-z0-9_-]{1,31}$', username))


def system_user_exists(username):
    try:
        result = subprocess.run([CMD['ID'], '-u', username], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def sync_samba_password(username, password):
    """Synchronize a system user's password with the Samba database"""
    try:
        process = subprocess.Popen(
            build_privileged_cmd([CMD['SMBPASSWD'], '-a', '-s', username]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={'LC_ALL': 'C'}
        )
        stdout, stderr = process.communicate(input=f"{password}\n{password}\n", timeout=5)

        if process.returncode != 0:
            print(f"Samba sync warning: {stderr}")
            return False, stderr

        return True, None
    except Exception as e:
        print(f"Samba sync error: {e}")
        return False, str(e)


# ── Samba configuration ───────────────────────────────────────────────────────

def ensure_samba_conf_exists():
    """Ensure /etc/samba/smb.conf exists with a basic [global] section."""
    if platform.system() != 'Linux':
        return
    try:
        if not os.path.exists('/etc/samba/smb.conf'):
            base_conf = "[global]\n   workgroup = WORKGROUP\n   server string = AlvaOS\n   security = user\n"
            cmd = build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf'])
            subprocess.run(cmd, input=base_conf, text=True, check=True, env={'LC_ALL': 'C'})
    except Exception as e:
        print(f"Error ensuring smb.conf exists: {e}")


def ensure_samba_global_settings(guest_access):
    """Ensure global Samba settings needed for guest access."""
    if platform.system() != 'Linux' or not guest_access:
        return
    try:
        res, err = run_sudo_command([CMD['CAT'], '/etc/samba/smb.conf'])
        if err or not res:
            return
        content = res.stdout
        if '[global]' not in content:
            content = "[global]\n   workgroup = WORKGROUP\n   server string = AlvaOS\n   security = user\n\n" + content
        if 'map to guest = Bad User' not in content:
            content = re.sub(r'\[global\]\n', '[global]\n   map to guest = Bad User\n', content, count=1)
        if 'guest account = nobody' not in content:
            content = re.sub(r'\[global\]\n', '[global]\n   guest account = nobody\n', content, count=1)
        process = subprocess.Popen(
            build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf']),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={'LC_ALL': 'C'}
        )
        process.communicate(input=content)
    except Exception as e:
        print(f"Error ensuring global Samba settings: {e}")


def reconcile_samba_guest_settings(shares_state):
    """Ensure global guest settings are present only when needed."""
    if platform.system() != 'Linux':
        return
    try:
        guest_needed = any(
            s.get('protocol') == 'smb' and s.get('guest_access', False)
            for s in shares_state.values()
        )
        res, err = run_sudo_command([CMD['CAT'], '/etc/samba/smb.conf'])
        if err or not res:
            return
        content = res.stdout
        if '[global]' not in content:
            content = "[global]\n   workgroup = WORKGROUP\n   server string = AlvaOS\n   security = user\n\n" + content
        if guest_needed:
            if 'map to guest = Bad User' not in content:
                content = re.sub(r'\[global\]\n', '[global]\n   map to guest = Bad User\n', content, count=1)
            if 'guest account = nobody' not in content:
                content = re.sub(r'\[global\]\n', '[global]\n   guest account = nobody\n', content, count=1)
        else:
            content = re.sub(r'^\s*map to guest\s*=.*$\n?', '', content, flags=re.MULTILINE)
            content = re.sub(r'^\s*guest account\s*=.*$\n?', '', content, flags=re.MULTILINE)

        process = subprocess.Popen(
            build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf']),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={'LC_ALL': 'C'}
        )
        process.communicate(input=content)
    except Exception as e:
        print(f"Error reconciling Samba guest settings: {e}")


def normalize_smb_permissions(permissions):
    """Normalize SMB permissions dict -> {user: role} with role in read/write/deny."""
    if not isinstance(permissions, dict):
        return {}
    normalized = {}
    for user, role in permissions.items():
        if not isinstance(user, str) or not user:
            continue
        role_val = str(role).lower().strip()
        if role_val not in ('read', 'write', 'deny'):
            continue
        normalized[user] = role_val
    return normalized


def get_group_members(group_name):
    try:
        result = subprocess.run([CMD['GETENT'], 'group', group_name], capture_output=True, text=True)
        if result.returncode != 0:
            return []
        parts = result.stdout.strip().split(':')
        if len(parts) < 4:
            return []
        members = parts[3].strip()
        if not members:
            return []
        return [m for m in members.split(',') if m]
    except Exception:
        return []


def ensure_group_exists(group_name):
    try:
        run_sudo_command([CMD['GROUPADD'], '-f', group_name])
    except Exception as e:
        print(f"Error ensuring group exists: {e}")


def apply_smb_permissions_to_fs(share_path, group_name, smb_permissions, guest_access):
    """Apply filesystem permissions for SMB share."""
    if platform.system() != 'Linux':
        return
    try:
        ensure_group_exists(group_name)
        allowed_users = [u for u, r in smb_permissions.items() if r in ('read', 'write')]
        write_users = [u for u, r in smb_permissions.items() if r == 'write']

        # Include guest account if enabled
        if guest_access:
            allowed_users.append('nobody')

        # Add allowed users to group
        for user in sorted(set(allowed_users)):
            if not user:
                continue
            run_sudo_command([CMD['GPASSWD'], '-a', user, group_name])

        # Remove users that are no longer allowed
        current_members = get_group_members(group_name)
        for user in current_members:
            if user not in allowed_users:
                run_sudo_command([CMD['GPASSWD'], '-d', user, group_name])

        # Set group ownership and permissions on path
        run_sudo_command([CMD['CHGRP'], '-R', group_name, share_path])

        # Determine permission mode
        if guest_access and not smb_permissions:
            # Guest-only: open permissions
            mode = '0777'
        else:
            # Setgid for group inheritance, grant write if any write users
            mode = '2770' if write_users else '2750'
        run_sudo_command([CMD['CHMOD'], '-R', mode, share_path])
    except Exception as e:
        print(f"Error applying SMB permissions to filesystem: {e}")


def disable_samba_homes_share():
    """Remove the default [homes] share if present to avoid user-named shares."""
    if platform.system() != 'Linux':
        return
    try:
        res, err = run_sudo_command([CMD['CAT'], '/etc/samba/smb.conf'])
        if err or not res:
            return
        content = res.stdout
        pattern = r'\[homes\].*?(?=\n\[|\Z)'
        new_content = re.sub(pattern, '', content, flags=re.DOTALL)
        if new_content != content:
            process = subprocess.Popen(
                build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf']),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={'LC_ALL': 'C'}
            )
            process.communicate(input=new_content)
    except Exception as e:
        print(f"Error disabling Samba homes share: {e}")


def render_smb_share_config(share_name, share_path, read_only, guest_access, permissions):
    """Render Samba share config with optional per-user permissions."""
    permissions = normalize_smb_permissions(permissions)
    allowed_users = [u for u, r in permissions.items() if r in ('read', 'write')]
    write_users = [u for u, r in permissions.items() if r == 'write']
    has_read_only = any(r == 'read' for r in permissions.values())

    # If permissions are provided, enforce them via valid users/write list
    use_permissions = len(permissions) > 0

    # Determine read only behavior
    if use_permissions:
        read_only = True if (read_only or has_read_only) else False

    lines = [
        f'# AlvaOS Share: {share_name}',
        f'[{share_name}]',
        f'    path = {share_path}',
        '    browseable = yes',
        f'    read only = {"yes" if read_only else "no"}',
        f'    guest ok = {"yes" if guest_access else "no"}',
        '    create mask = 0644',
        '    directory mask = 0755'
    ]

    if use_permissions:
        # Add guest account when enabled
        if guest_access:
            allowed_users.append('nobody')
        if allowed_users:
            lines.append(f'    valid users = {" ".join(sorted(set(allowed_users)))}')
        if read_only and write_users:
            lines.append(f'    write list = {" ".join(sorted(set(write_users)))}')

    return "\n" + "\n".join(lines) + "\n"


def update_samba_share_section(share_name, new_config):
    """Replace an existing share section with new config."""
    if platform.system() != 'Linux':
        return
    ensure_samba_conf_exists()
    try:
        res, err = run_sudo_command([CMD['CAT'], '/etc/samba/smb.conf'])
        if err or not res:
            raise Exception(err or "Could not read smb.conf")
        content = res.stdout
        pattern = rf'# AlvaOS Share: {re.escape(share_name)}\n\[{re.escape(share_name)}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        content = content.rstrip() + "\n" + new_config
        process = subprocess.Popen(
            build_privileged_cmd([CMD['TEE'], '/etc/samba/smb.conf']),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={'LC_ALL': 'C'}
        )
        process.communicate(input=content)
    except Exception as e:
        print(f"Error updating SMB share section: {e}")


# ── Shares state ──────────────────────────────────────────────────────────────

def load_shares_state():
    """Load shares state from file"""
    try:
        if os.path.exists(SHARES_STATE_FILE):
            with open(SHARES_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_shares_state(state):
    """Save shares state to file"""
    try:
        ensure_directories()
        with open(SHARES_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving shares state: {e}")
