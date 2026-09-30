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


def lock_share_user_shells(usernames):
    """Share accounts created by older versions got /bin/bash, so with SSH on
    they could log in to the NAS. Give them no login shell; Samba does not need one."""
    if platform.system() != 'Linux':
        return
    import pwd
    for username in usernames:
        try:
            shell = pwd.getpwnam(username).pw_shell
        except KeyError:
            continue
        if shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
            _, err = run_sudo_command([CMD['USERMOD'], '-s', '/usr/sbin/nologin', username])
            print(f"Share user {username}: login shell removed" if not err else f"Share user {username}: {err}")


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


# ── Share requests ────────────────────────────────────────────────────────────
# Names end up as smb.conf section headers, NFS export comments and folder
# names, so they are restricted to characters that are safe in all three.

SHARE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$')
# One NFS client: *, a host name (wildcards allowed), an IPv4/IPv6 address or network.
NFS_CLIENT_RE = re.compile(r'^(\*|[A-Za-z0-9*?_.-]{1,253}|[0-9a-fA-F:.]+(/[0-9]{1,3})?)$')


def _within(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def validate_share_request(data, pools_state, shares_state, known_users):
    """Check a create-share request. Returns (share fields, '') or (None, error).

    The folder is given either as pool_id (+ folder, '' for the whole pool,
    new_folder to create it) or as an absolute path inside a pool."""
    if not isinstance(data, dict):
        return None, 'No data provided'
    name = str(data.get('name') or '').strip()
    if not SHARE_NAME_RE.match(name):
        return None, 'Share names use letters, numbers, "-" and "_" (up to 63 characters), no spaces.'
    if any(str(s.get('name', '')).lower() == name.lower() for s in shares_state.values() if isinstance(s, dict)):
        return None, f'A share named "{name}" already exists.'
    protocol = str(data.get('protocol') or 'smb').lower()
    if protocol not in ('smb', 'nfs'):
        return None, 'Protocol must be "smb" or "nfs"'

    pools = {pid: p for pid, p in pools_state.items()
             if isinstance(p, dict) and p.get('mount_point') and p.get('mount_point') != '/'}
    new_folder = False
    if data.get('pool_id') is not None:
        pool = pools.get(str(data.get('pool_id')))
        if not pool:
            return None, 'Choose a pool for this share.'
        folder = str(data.get('folder') or '').strip().strip('/')
        if folder and not all(SHARE_NAME_RE.match(part) for part in folder.split('/')):
            return None, 'Folder names use letters, numbers, "-" and "_".'
        path = os.path.join(pool['mount_point'], folder) if folder else pool['mount_point']
        new_folder = bool(data.get('new_folder')) and bool(folder)
    else:
        raw = str(data.get('path') or '').strip()
        if not raw.startswith('/') or '..' in raw.split('/'):
            return None, 'Share path must be an absolute path'
        path = os.path.normpath(raw)
        if not any(_within(path, p['mount_point']) for p in pools.values()):
            return None, 'Shares must be inside a storage pool.'

    guest = data.get('guest_access') is True
    permissions = normalize_smb_permissions(data.get('smb_permissions', {}))
    unknown = sorted(u for u in permissions if u not in known_users)
    if unknown:
        return None, f'Unknown user(s): {", ".join(unknown)}'
    if protocol == 'smb' and not guest and not any(r in ('read', 'write') for r in permissions.values()):
        return None, 'Choose who can open this share: at least one person, or everyone on the network.'

    hosts = str(data.get('allowed_hosts') or '*').replace(',', ' ').split()
    if protocol == 'nfs':
        if not hosts or not all(NFS_CLIENT_RE.match(h) for h in hosts):
            return None, 'Allowed computers: use *, an address like 192.168.1.20, or a network like 192.168.1.0/24.'

    return {
        'name': name,
        'path': path,
        'new_folder': new_folder,
        'protocol': protocol,
        'read_only': data.get('read_only') is True,
        'guest_access': guest if protocol == 'smb' else False,
        'allowed_hosts': ' '.join(hosts) if protocol == 'nfs' else '*',
        'smb_permissions': permissions if protocol == 'smb' else {},
    }, ''


def render_nfs_export(name, path, allowed_hosts, read_only):
    """/etc/exports lines for one share: a marker comment and the export."""
    options = f'{"ro" if read_only else "rw"},sync,no_subtree_check,root_squash'
    clients = ' '.join(f'{host}({options})' for host in str(allowed_hosts or '*').split())
    return f'# AlvaOS Share: {name}\n{path} {clients}\n'


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
