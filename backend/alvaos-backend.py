#!/usr/bin/env python3
"""
AlvaOS Backend 0.2.0
Comprehensive Storage Management Engine
"""

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import psutil
import platform
import socket
import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
import hashlib
import hmac
import secrets
import functools
import re
import time
import subprocess
from update_manager import UpdateManager
from docker_manager import DockerManager
from app_store import AppStore
from backup_manager import BackupManager
from buddy_backup_manager import BuddyBackupManager


def is_secure_system_device(device_name):
    """
    Check if a device (e.g. 'sda', 'nvme0n1') holds the root filesystem.
    This uses /proc/mounts to find the root device and checks if the target
    device is the same or a parent of the root device.
    """
    if platform.system() != 'Linux':
        return False # Mock environment safety

    try:
        # 1. Provide a direct lookup for common root partition names if they match
        # standardized collected info, but rely on /proc/mounts for truth.
        root_device = None
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == '/':
                    root_device = parts[0] # e.g. /dev/sda2 or /dev/nvme0n1p3
                    break
        
        if not root_device:
            return False

        # Resolve symlinks (e.g. /dev/root -> /dev/sda1)
        if os.path.exists(root_device):
            root_device = os.path.realpath(root_device)

        # Check if the target device name is part of the root device path
        # e.g. target='sda', root='/dev/sda2' -> match
        # e.g. target='nvme0n1', root='/dev/nvme0n1p3' -> match
        base_root = os.path.basename(root_device)
        
        # Exact match
        if base_root == device_name:
            return True
            
        # Partition match (startswith check is usually enough for sda1 vs sda, but be careful with sdaa vs sda)
        # For standard sdX: sda is prefix of sda1. sda is NOT prefix of sdb.
        # For nvme: nvme0n1 is prefix of nvme0n1p1.
        if base_root.startswith(device_name):
            # Verify it's actually a partition convention
            suffix = base_root[len(device_name):]
            # sda1 -> suffix '1' (digit)
            # nvme0n1p1 -> suffix 'p1'
            if suffix and (suffix[0].isdigit() or suffix.startswith('p')):
                 return True
                 
        return False

    except Exception as e:
        print(f"Error checking system device: {e}")
        # Fail safe: if we can't determine, assume it MIGHT be system to be safe? 
        # Or returns false and rely on lsblk? Let's return False to avoid blocking everything if this fails.
        return False
# System Commands Paths for Sudo (must match sudoers configuration in install-system.sh)
CMD = {
    'GETENT': '/usr/bin/getent',
    'CHPASSWD': '/usr/sbin/chpasswd',
    'USERADD': '/usr/sbin/useradd',
    'USERDEL': '/usr/sbin/userdel',
    'SMBPASSWD': '/usr/bin/smbpasswd',
    'GROUPADD': '/usr/sbin/groupadd',
    'GROUPDEL': '/usr/sbin/groupdel',
    'GPASSWD': '/usr/bin/gpasswd',
    'CHGRP': '/usr/bin/chgrp',
    'CHMOD': '/usr/bin/chmod',
    'SYSTEMCTL': '/usr/bin/systemctl',
    'SYSTEMD_RUN': '/usr/bin/systemd-run',
    'REBOOT': '/usr/sbin/reboot',
    'POWEROFF': '/usr/sbin/poweroff',
    'TIMEDATECTL': '/usr/bin/timedatectl',
    'HOSTNAMECTL': '/usr/bin/hostnamectl',
    'APT_GET': '/usr/bin/apt-get',
    'APT': '/usr/bin/apt',
    'DPKG': '/usr/bin/dpkg',
    'TAIL': '/usr/bin/tail',
    'JOURNALCTL': '/usr/bin/journalctl',
    'SMARTCTL': '/usr/sbin/smartctl',
    'LSBLK': '/usr/bin/lsblk',
    'WIPEFS': '/usr/sbin/wipefs',
    'PARTPROBE': '/usr/sbin/partprobe',
    'BTRFS': '/usr/bin/btrfs',
    'MKFS_BTRFS': '/usr/sbin/mkfs.btrfs',
    'MKDIR': '/usr/bin/mkdir',
    'MOUNT': '/usr/bin/mount',
    'UMOUNT': '/usr/bin/umount',
    'RMDIR': '/usr/bin/rmdir',
    'BLKID': '/usr/sbin/blkid',
    'EXPORTFS': '/usr/sbin/exportfs',
    'TEE': '/usr/bin/tee',
    'CAT': '/usr/bin/cat',
    'IP': '/usr/sbin/ip',
    'ID': '/usr/bin/id',
    'DF': '/usr/bin/df',
    'MOUNTPOINT': '/usr/bin/mountpoint'
}

app = Flask(__name__, static_folder=None) # Disable default static serving to force version replacement
# Fallback to current directory for dev if /opt doesn't exist
WEBUI_ROOT = '/opt/alvaos/webui'
if not os.path.exists(WEBUI_ROOT):
    WEBUI_ROOT = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app.static_folder = WEBUI_ROOT
CORS(app)
update_manager = UpdateManager()
docker_manager = DockerManager()
app_store = AppStore()
backup_manager = None
buddy_backup_manager = None

# Cache for storage information (TTL in seconds)
STORAGE_CACHE = {
    'disks': {'data': None, 'expires': 0},
    'pools': {'data': None, 'expires': 0}
}
CACHE_TTL = 5

def invalidate_storage_cache(*sections):
    """Invalidate selected storage cache sections."""
    target_sections = sections or ('disks', 'pools')
    for section in target_sections:
        if section in STORAGE_CACHE:
            STORAGE_CACHE[section]['expires'] = 0

# Version Management
def get_version():
    """Read version from VERSION file"""
    # Production path
    prod_path = '/etc/alvaos/VERSION'
    # Development path (relative to backend script)
    dev_path = os.path.join(os.path.dirname(__file__), '..', 'VERSION')
    
    try:
        if os.path.exists(prod_path):
            with open(prod_path, 'r') as f:
                return f.read().strip()
        elif os.path.exists(dev_path):
            with open(dev_path, 'r') as f:
                return f.read().strip()
    except Exception as e:
        print(f"Error reading version file: {e}")
    
    return "unknown"

VERSION = get_version()

# Configuration
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
AUTH_FILE = '/var/lib/alvaos/auth.json'
USERS_STATE_FILE = '/var/lib/alvaos/users.json'
CONFIG_DIR = '/etc/alvaos'
SESSIONS = {} # Token -> Username (In-memory for 0.1)

def is_root_user():
    try:
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    except Exception:
        return False

def build_privileged_cmd(cmd):
    """Build a command that runs as root when needed, without requiring sudo if already root."""
    if is_root_user():
        return cmd
    return ['sudo', '-n'] + cmd

def run_sudo_command(cmd, timeout=30):
    """Helper to run a command with sudo and handle password prompts gracefully"""
    try:
        # Prepare the env with LC_ALL=C to ensure English output
        custom_env = os.environ.copy()
        custom_env['LC_ALL'] = 'C'
        
        final_cmd = []
        if cmd and cmd[0] == 'sudo':
            # Remove redundant 'sudo' if present in the cmd list passed to us
            # build_privileged_cmd will add 'sudo -n' if needed
            final_cmd = build_privileged_cmd(cmd[1:])
        else:
            final_cmd = build_privileged_cmd(cmd)
        
        result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout, env=custom_env)
        
        if result.returncode != 0:
            stderr_text = (result.stderr or '').strip()
            stdout_text = (result.stdout or '').strip()
            combined_low = f"{stderr_text}\n{stdout_text}".lower()
            if '/etc/sudoers.d/alvaos' in combined_low and (
                'is owned by uid' in combined_low
                or 'is world writable' in combined_low
                or 'bad permissions' in combined_low
            ):
                return result, (
                    "System permission error: /etc/sudoers.d/alvaos has invalid ownership or permissions. "
                    "Run as root: chown root:root /etc/sudoers.d/alvaos && chmod 440 /etc/sudoers.d/alvaos"
                )
            if 'password is required' in combined_low or 'a password is required' in combined_low:
                cmd_str = " ".join(final_cmd)
                return None, f"System permission error: Passwordless sudo is not configured for command: {cmd_str}. Please check the AlvaOS documentation for sudoers setup."
            cmd_str = " ".join(final_cmd)
            detail = stderr_text or stdout_text or f"exit code {result.returncode}"
            return result, f"Command failed ({result.returncode}): {cmd_str}: {detail}"
            
        return result, None
    except subprocess.TimeoutExpired:
        return None, "Command timed out"
    except Exception as e:
        return None, str(e)

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

def ensure_directories():
    """Ensure necessary directories exist"""
    Path('/var/lib/alvaos').mkdir(parents=True, exist_ok=True)
    Path('/var/log/alvaos').mkdir(parents=True, exist_ok=True)

def parse_size_to_bytes(size_str):
    """Parse size strings like '8.00GiB' into bytes."""
    try:
        s = size_str.strip()
        m = re.match(r'^([\d\.]+)\s*([KMGTP]i?B)$', s)
        if not m:
            return None
        value = float(m.group(1))
        unit = m.group(2)
        multipliers = {
            'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4, 'PB': 1000**5,
            'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4, 'PiB': 1024**5
        }
        return int(value * multipliers.get(unit, 1))
    except Exception:
        return None

def format_bytes_gib(byte_val):
    try:
        gib = byte_val / (1024**3)
        return f"{gib:.2f}GiB"
    except Exception:
        return "Unknown"

def load_users_state():
    """Load users state from file"""
    try:
        if os.path.exists(USERS_STATE_FILE):
            with open(USERS_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading users state: {e}")
    return {}

def save_users_state(state):
    """Save users state to file"""
    try:
        ensure_directories()
        with open(USERS_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving users state: {e}")

def is_valid_username(username):
    return bool(re.match(r'^[a-z_][a-z0-9_-]{1,31}$', username))

def system_user_exists(username):
    try:
        result = subprocess.run([CMD['ID'], '-u', username], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

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
        res, err = run_sudo_command(['sudo', CMD['CAT'], '/etc/samba/smb.conf'])
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
        res, err = run_sudo_command(['sudo', CMD['CAT'], '/etc/samba/smb.conf'])
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

def is_setup_complete():
    """Check if initial setup has been completed"""
    return os.path.exists(SETUP_STATUS_FILE)

def mark_setup_complete(password):
    """Mark the initial setup as complete and store password hash"""
    ensure_directories()
    
    # Simple hash for 0.1
    salt = secrets.token_hex(8)
    h = hashlib.sha256((password + salt).encode()).hexdigest()
    
    auth_data = {
        'password_hash': h,
        'salt': salt
    }
    
    with open(AUTH_FILE, 'w') as f:
        json.dump(auth_data, f)

    setup_data = {
        'setup_completed': True,
        'completed_at': datetime.now().isoformat(),
        'version': VERSION
    }
    with open(SETUP_STATUS_FILE, 'w') as f:
        json.dump(setup_data, f, indent=2)

def require_auth(f):
    """Decorator to require authentication if setup is complete"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_setup_complete():
            return f(*args, **kwargs)
            
        token = request.headers.get('Authorization')
        if not token or token not in SESSIONS:
            return jsonify({'error': 'Authentication required'}), 401
            
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Serve the Web UI with version replacement"""
    return serve_frontend('index.html')

def serve_frontend(filename):
    """Helper to serve frontend files with version replacement"""
    try:
        # Check if it's an HTML, CSS, or JS file that might need replacement
        if filename.endswith(('.html', '.css', '.js')):
            content = ""
            with open(os.path.join(app.static_folder, filename), 'r') as f:
                content = f.read()
            
            # Replace placeholder
            clean_version = VERSION.strip('() ')
            content = content.replace('{{VERSION}}', clean_version)
            
            # Create a response with correct mimetype
            from flask import Response
            if filename.endswith('.html'):
                mimetype = 'text/html'
            elif filename.endswith('.css'):
                mimetype = 'text/css'
            else:  # .js
                mimetype = 'application/javascript'
            return Response(content, mimetype=mimetype)
        
        return send_from_directory(app.static_folder, filename)
    except Exception as e:
        print(f"Error serving {filename}: {e}")
        return f"File not found: {filename}", 404
        
    return send_from_directory(app.static_folder, filename)

@app.route('/<path:filename>')
def serve_static_files(filename):
    """Catch-all for static files to ensure version replacement"""
    return serve_frontend(filename)

@app.route('/<path:path>')
def serve_static(path):
    """Serve static files"""
    if path.endswith(('.html', '.css')):
        return serve_frontend(path)
    return send_from_directory(app.static_folder, path)

@app.route('/api/v1/setup/status', methods=['GET'])
def get_setup_status():
    """Check if initial setup is required"""
    return jsonify({
        'setup_complete': is_setup_complete(),
        'version': VERSION
    })

@app.route('/api/v1/setup/complete', methods=['POST'])
def complete_setup():
    """Complete initial setup with root password change"""
    try:
        data = request.get_json()
        
        if not data or 'password' not in data:
            return jsonify({'error': 'Password is required'}), 400
        
        password = data['password']
        
        # Validate password strength
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        
        # Change root password using subprocess with sudo
        try:
            process = subprocess.Popen(
                build_privileged_cmd([CMD['CHPASSWD']]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={'LC_ALL': 'C'}
            )
            stdout, stderr = process.communicate(input=f'root:{password}\n', timeout=5)
            
            if process.returncode != 0:
                return jsonify({'error': f'Failed to set password: {stderr}'}), 500
                
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Password change timed out'}), 500
        except Exception as e:
            return jsonify({'error': f'Password change failed: {str(e)}'}), 500
        
        # Re-enable SSH root login now that password is set
        try:
            ssh_config_file = '/etc/ssh/sshd_config.d/00-alvaos-security.conf'
            if os.path.exists(ssh_config_file):
                # Update SSH config to allow root login with password
                with open(ssh_config_file, 'w') as f:
                    f.write('# AlvaOS Security Configuration\n')
                    f.write('# Root login enabled after setup completion\n')
                    f.write('PermitRootLogin yes\n')
                    f.write('PasswordAuthentication yes\n')
                    f.write('PermitEmptyPasswords no\n')
                
                # Restart SSH service
                run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'ssh'])
        except Exception as e:
            # Don't fail setup if SSH config update fails
            print(f"Warning: Could not update SSH config: {e}")
        
        # Sync to Samba
        sync_samba_password('root', password)

        # Set Timezone if provided
        if 'timezone' in data and platform.system() == 'Linux':
            try:
                run_sudo_command([CMD['TIMEDATECTL'], 'set-timezone', data['timezone']])
            except Exception as e:
                print(f"Warning: Could not set timezone during setup: {e}")
        
        # Mark setup as complete
        mark_setup_complete(password)
        
        # Auto-login for the setup session
        token = secrets.token_hex(24)
        SESSIONS[token] = 'root'
        
        return jsonify({
            'success': True,
            'message': 'Setup completed successfully',
            'token': token
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/auth/login', methods=['POST'])
def login():
    """Login with root password"""
    if not is_setup_complete():
        return jsonify({'error': 'Setup not complete'}), 400
        
    data = request.get_json()
    if not data or 'password' not in data:
        return jsonify({'error': 'Password required'}), 400
        
    password = data['password']
    
    try:
        with open(AUTH_FILE, 'r') as f:
            auth_data = json.load(f)
            
        h = hashlib.sha256((password + auth_data['salt']).encode()).hexdigest()
        
        if h == auth_data['password_hash']:
            token = secrets.token_hex(24)
            SESSIONS[token] = 'root'
            return jsonify({'token': token, 'success': True})
        else:
            return jsonify({'error': 'Invalid password'}), 401
            
    except Exception as e:
        return jsonify({'error': 'Authentication failed'}), 500

@app.route('/api/v1/users', methods=['GET', 'POST', 'DELETE'])
@require_auth
def manage_users():
    """Manage system users and Samba users"""
    if request.method == 'GET':
        users_state = load_users_state()
        users = []
        for username, info in users_state.items():
            users.append({
                'username': username,
                'role': info.get('role', 'user'),
                'created_at': info.get('created_at'),
                'system_exists': system_user_exists(username)
            })
        users.sort(key=lambda u: u['username'])
        return jsonify({'users': users})

    data = request.get_json() or {}
    username = data.get('username', '').strip()
    if not username:
        return jsonify({'error': 'Username is required'}), 400
    if not is_valid_username(username):
        return jsonify({'error': 'Invalid username. Use lowercase letters, numbers, _ or -.'}), 400
    if username in ('root', 'alvaos'):
        return jsonify({'error': 'Reserved username'}), 400

    users_state = load_users_state()

    if request.method == 'POST':
        password = data.get('password', '')
        role = data.get('role', 'user').strip().lower()
        if role not in ('admin', 'user'):
            role = 'user'
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        if username in users_state or system_user_exists(username):
            return jsonify({'error': 'User already exists'}), 400

        try:
            # Create system user
            res, err = run_sudo_command([CMD['USERADD'], '-m', '-s', '/bin/bash', username])
            if err:
                return jsonify({'error': f'Failed to create user: {err}'}), 500

            # Set password
            process = subprocess.Popen(
                build_privileged_cmd([CMD['CHPASSWD']]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={'LC_ALL': 'C'}
            )
            stdout, stderr = process.communicate(input=f'{username}:{password}\n', timeout=5)
            if process.returncode != 0:
                return jsonify({'error': f'Failed to set password: {stderr}'}), 500

            # Add/Update Samba password
            ok, smb_err = sync_samba_password(username, password)
            if not ok:
                # Rollback system user creation
                run_sudo_command([CMD['USERDEL'], username])
                return jsonify({'error': f'Failed to sync Samba password: {smb_err}'}), 500

            users_state[username] = {
                'username': username,
                'role': role,
                'created_at': datetime.now().isoformat()
            }
            save_users_state(users_state)

            return jsonify({'success': True, 'message': f'User "{username}" created'})
        except Exception as e:
            return jsonify({'error': f'Failed to create user: {str(e)}'}), 500

    if request.method == 'DELETE':
        if username not in users_state and not system_user_exists(username):
            return jsonify({'error': 'User not found'}), 404
        try:
            # Remove SMB user
            run_sudo_command([CMD['SMBPASSWD'], '-x', username])
            # Delete system user (keep home to avoid data loss)
            run_sudo_command([CMD['USERDEL'], username])

            # Remove user from share permissions
            shares_state = load_shares_state()
            updated = False
            for share_id, share in shares_state.items():
                perms = share.get('smb_permissions', {})
                if isinstance(perms, dict) and username in perms:
                    del perms[username]
                    share['smb_permissions'] = perms
                    if share.get('protocol') == 'smb' and platform.system() == 'Linux':
                        new_config = render_smb_share_config(
                            share.get('name'),
                            share.get('path'),
                            share.get('read_only', False),
                            share.get('guest_access', False),
                            perms
                        )
                        update_samba_share_section(share.get('name'), new_config)
                        apply_smb_permissions_to_fs(
                            share.get('path'),
                            share.get('smb_group', f'alvaos_{share_id}'),
                            perms,
                            share.get('guest_access', False)
                        )
                    updated = True
            if updated:
                save_shares_state(shares_state)
                if platform.system() == 'Linux':
                    reconcile_samba_guest_settings(shares_state)

            if username in users_state:
                del users_state[username]
                save_users_state(users_state)

            return jsonify({'success': True, 'message': f'User "{username}" deleted'})
        except Exception as e:
            return jsonify({'error': f'Failed to delete user: {str(e)}'}), 500

@app.route('/api/v1/users/<username>', methods=['PATCH'])
@require_auth
def update_user(username):
    """Update user password or role"""
    username = username.strip()
    if not username or not is_valid_username(username):
        return jsonify({'error': 'Invalid username'}), 400
    if username in ('root', 'alvaos'):
        return jsonify({'error': 'Reserved username'}), 400

    data = request.get_json() or {}
    users_state = load_users_state()
    if username not in users_state and not system_user_exists(username):
        return jsonify({'error': 'User not found'}), 404

    if 'role' in data:
        role = str(data.get('role', 'user')).lower()
        if role not in ('admin', 'user'):
            role = 'user'
        if username in users_state:
            users_state[username]['role'] = role
            save_users_state(users_state)

    if 'password' in data:
        password = data.get('password', '')
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        try:
            process = subprocess.Popen(
                build_privileged_cmd([CMD['CHPASSWD']]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={'LC_ALL': 'C'}
            )
            stdout, stderr = process.communicate(input=f'{username}:{password}\n', timeout=5)
            if process.returncode != 0:
                return jsonify({'error': f'Failed to set password: {stderr}'}), 500
            ok, smb_err = sync_samba_password(username, password)
            if not ok:
                 return jsonify({'error': f'Failed to update Samba password: {smb_err}'}), 500
        except Exception as e:
            return jsonify({'error': f'Failed to update password: {str(e)}'}), 500

    return jsonify({'success': True, 'message': f'User "{username}" updated'})

@app.route('/api/v1/system/info', methods=['GET'])
@require_auth
def get_system_info():
    """Get comprehensive system information"""
    
    # CPU Information
    cpu_freq = psutil.cpu_freq()
    cpu_model = platform.processor() or "Unknown"
    cpu_temp = None
    try:
        if platform.system() == 'Linux' and os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.lower().startswith('model name'):
                        cpu_model = line.split(':', 1)[1].strip()
                        break
    except Exception:
        pass
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
        # Prefer common sensor keys when available
        for key in ('coretemp', 'k10temp', 'cpu-thermal', 'soc_thermal'):
            if key in temps and temps[key]:
                cpu_temp = temps[key][0].current
                break
        if cpu_temp is None:
            # Fallback to first available temperature
            for entries in temps.values():
                if entries:
                    cpu_temp = entries[0].current
                    break
    except Exception:
        pass
    cpu_info = {
        'cores': psutil.cpu_count(logical=False),
        'threads': psutil.cpu_count(logical=True),
        'usage_percent': psutil.cpu_percent(interval=1),
        'frequency_mhz': round(cpu_freq.current, 2) if cpu_freq else 0,
        'model': cpu_model,
        'temperature_c': round(cpu_temp, 1) if isinstance(cpu_temp, (int, float)) else None,
    }
    
    # Memory Information
    mem = psutil.virtual_memory()
    memory_info = {
        'total_gb': round(mem.total / (1024**3), 2),
        'used_gb': round(mem.used / (1024**3), 2),
        'available_gb': round(mem.available / (1024**3), 2),
        'percent': mem.percent,
    }
    
    # Disk Information
    disk = psutil.disk_usage('/')
    disk_info = {
        'total_gb': round(disk.total / (1024**3), 2),
        'used_gb': round(disk.used / (1024**3), 2),
        'free_gb': round(disk.free / (1024**3), 2),
        'percent': disk.percent,
    }

    # Pool-based storage information (for dashboard breakdown)
    pool_storage_info = []
    try:
        pools_state = load_pools_state()
        for pool_id, pool_data in pools_state.items():
            mount_point = pool_data.get('mount_point')
            if not mount_point:
                continue

            pool_entry = {
                'id': pool_id,
                'name': pool_data.get('name', pool_id),
                'mount_point': mount_point,
                'mounted': bool(os.path.ismount(mount_point)),
            }

            if pool_entry['mounted']:
                try:
                    pool_usage = psutil.disk_usage(mount_point)
                    pool_entry.update({
                        'total_gb': round(pool_usage.total / (1024**3), 2),
                        'used_gb': round(pool_usage.used / (1024**3), 2),
                        'free_gb': round(pool_usage.free / (1024**3), 2),
                        'percent': pool_usage.percent,
                    })
                except Exception as pool_usage_error:
                    pool_entry['error'] = str(pool_usage_error)
            else:
                pool_entry['error'] = 'Pool is not mounted'

            pool_storage_info.append(pool_entry)
    except Exception:
        pass
    
    # Network Information
    hostname = 'unknown'
    try:
        if platform.system() == 'Linux':
            res = subprocess.run([CMD['HOSTNAMECTL'], 'hostname'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                hostname = res.stdout.strip()
            else:
                hostname = socket.gethostname() or 'unknown'
        else:
            hostname = socket.gethostname() or 'unknown'
    except:
        hostname = 'unknown'
        
    ip_address = '127.0.0.1'
    
    try:
        # Better IP detection: find first non-loopback IPv4
        addrs = psutil.net_if_addrs()
        for iface, iface_addrs in addrs.items():
            if iface.startswith('lo'): continue
            for addr in iface_addrs:
                if addr.family == socket.AF_INET:
                    ip_address = addr.address
                    break
            if ip_address != '127.0.0.1': break
    except:
        pass
    
    network_info = {
        'hostname': hostname,
        'ip_address': ip_address,
    }
    
    # System Information
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_seconds = (datetime.now() - boot_time).total_seconds()
    
    system_info = {
        'os': platform.system(),
        'os_version': platform.release(),
        'architecture': platform.machine(),
        'python_version': platform.python_version(),
        'uptime_hours': round(uptime_seconds / 3600, 1),
        'boot_time': boot_time.strftime('%Y-%m-%d %H:%M:%S'),
        'effective_user': os.getenv('USER') or os.getenv('USERNAME') or 'unknown',
        'is_root': is_root_user(),
    }
    
    return jsonify({
        'version': VERSION,
        'timestamp': datetime.now().isoformat(),
        'cpu': cpu_info,
        'memory': memory_info,
        'disk': disk_info,
        'storage_pools': pool_storage_info,
        'network': network_info,
        'system': system_info,
    })

@app.route('/api/v1/system/time', methods=['GET', 'POST'])
@require_auth
def system_time():
    """Get or Set system time settings"""
    if request.method == 'GET':
        timezone = 'UTC'
        ntp_enabled = True
        
        if platform.system() == 'Linux':
            try:
                # Get current timezone
                tz_result = subprocess.run([CMD['TIMEDATECTL'], 'show', '--property=Timezone', '--value'], 
                                         capture_output=True, text=True)
                if tz_result.returncode == 0:
                    timezone = tz_result.stdout.strip()
                
                # Get NTP status
                ntp_result = subprocess.run([CMD['TIMEDATECTL'], 'show', '--property=NTP', '--value'], 
                                          capture_output=True, text=True)
                if ntp_result.returncode == 0:
                    ntp_enabled = ntp_result.stdout.strip() == 'yes'
            except:
                pass
                
        return jsonify({
            'timezone': timezone,
            'ntp_enabled': ntp_enabled,
            'current_time': datetime.now().isoformat()
        })
    
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if platform.system() == 'Linux':
            try:
                warnings = []
                if 'timezone' in data:
                    res, err = run_sudo_command([CMD['TIMEDATECTL'], 'set-timezone', data['timezone']])
                    if err: raise Exception(err)
                if 'ntp' in data:
                    ntp_val = 'true' if data['ntp'] else 'false'
                    res, err = run_sudo_command([CMD['TIMEDATECTL'], 'set-ntp', ntp_val])
                    if err:
                        warnings.append(err)

                if warnings:
                    return jsonify({
                        'success': True,
                        'message': 'Time settings updated with warnings',
                        'warnings': warnings
                    })

                return jsonify({'success': True, 'message': 'Time settings updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        else:
            return jsonify({'success': True, 'message': 'Mock: Time settings updated'})

@app.route('/api/v1/system/power', methods=['POST'])
@require_auth
def system_power():
    """Handle Shutdown/Reboot"""
    data = request.get_json()
    action = data.get('action')
    
    if action not in ['reboot', 'shutdown']:
        return jsonify({'error': 'Invalid action'}), 400
        
    if platform.system() == 'Linux':
        try:
            cmd_path = CMD['REBOOT'] if action == 'reboot' else CMD['POWEROFF']
            subprocess.Popen(build_privileged_cmd([cmd_path]))
            return jsonify({'success': True, 'message': f'System {action} initiated'})
        except Exception as e:
            return jsonify({'error': f'Failed to {action}: {str(e)}'}), 500
    else:
        return jsonify({'success': False, 'message': f'System {action} not supported on {platform.system()}'}), 400

@app.route('/api/v1/system/network', methods=['GET'])
@require_auth
def get_network_details():
    """Get detailed network configuration"""
    hostname = "unknown"
    try:
        if platform.system() == 'Linux':
            res = subprocess.run([CMD['HOSTNAMECTL'], 'hostname'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                hostname = res.stdout.strip()
            else:
                hostname = socket.gethostname() or "unknown"
        else:
            hostname = socket.gethostname() or "unknown"
    except:
        pass
        
    ip_address = "127.0.0.1"
    interface = "lo"
    subnet_mask = "255.255.255.0"
    gateway = "N/A"
    dns_servers = []
    
    # Try to get real network information
    try:
        # Get all network interfaces
        import psutil
        net_if_addrs = psutil.net_if_addrs()
        
        # Find the first non-loopback interface with an IPv4 address
        for iface_name, iface_addresses in net_if_addrs.items():
            if iface_name.startswith(('lo', 'docker', 'veth', 'br-')):
                continue
            
            for addr in iface_addresses:
                if addr.family == socket.AF_INET:  # IPv4
                    ip_address = addr.address
                    interface = iface_name
                    if addr.netmask:
                        subnet_mask = addr.netmask
                    break
            
            if ip_address != "127.0.0.1":
                break
        
        # Try to get gateway on Linux
        if platform.system() == 'Linux':
            try:
                result = subprocess.run([CMD['IP'], 'route', 'show', 'default'], 
                                      capture_output=True, text=True, timeout=2)
                if result.returncode == 0 and result.stdout:
                    parts = result.stdout.split()
                    if len(parts) >= 3 and parts[0] == 'default':
                        gateway = parts[2]
            except:
                pass
            
            # Try to get DNS servers
            try:
                if os.path.exists('/etc/resolv.conf'):
                    with open('/etc/resolv.conf', 'r') as f:
                        for line in f:
                            if line.strip().startswith('nameserver'):
                                dns = line.split()[1]
                                if dns not in dns_servers:
                                    dns_servers.append(dns)
            except:
                pass
        
        # Fallback DNS if none found
        if not dns_servers:
            dns_servers = ['1.1.1.1', '8.8.8.8']
            
    except Exception as e:
        print(f"Error getting network details: {e}")
        # Use fallback values
        pass

    return jsonify({
        'interface': interface,
        'hostname': hostname,
        'ip_address': ip_address,
        'subnet_mask': subnet_mask,
        'gateway': gateway,
        'dns': dns_servers
    })

@app.route('/api/v1/system/hostname', methods=['PUT'])
@require_auth
def set_hostname():
    """Set system hostname"""
    data = request.get_json()
    if not data or 'hostname' not in data:
        return jsonify({'error': 'Hostname required'}), 400
    
    new_hostname = data['hostname']
    
    # Validation
    if not new_hostname.replace('-', '').isalnum():
        return jsonify({'error': 'Invalid hostname format'}), 400

    if platform.system() == 'Linux':
        # 0. Capture old hostname BEFORE changing it
        old_hostname = socket.gethostname()
        
        # 1. Update hostname via hostnamectl
        res, err = run_sudo_command([CMD['HOSTNAMECTL'], 'set-hostname', new_hostname])
        if err:
             return jsonify({'error': f'Failed to set hostname: {err}'}), 500
             
        # 2. Update /etc/hosts to prevent "unable to resolve host" errors
        try:
            hosts_file = '/etc/hosts'
            
            # Read current hosts file
            res, err = run_sudo_command([CMD['CAT'], hosts_file])
            if res and res.returncode == 0:
                content = res.stdout
                
                # More robust replacement
                lines = content.splitlines()
                new_lines = []
                found_local_ip = False
                
                for line in lines:
                    if line.strip().startswith('127.0.1.1'):
                        new_lines.append(f'127.0.1.1\t{new_hostname}')
                        found_local_ip = True
                    else:
                        new_lines.append(line.replace(old_hostname, new_hostname))
                
                if not found_local_ip:
                    new_lines.append(f'127.0.1.1\t{new_hostname}')
                
                new_content = "\n".join(new_lines) + "\n"
                
                # Write back with tee
                process = subprocess.Popen(
                    build_privileged_cmd([CMD['TEE'], hosts_file]), 
                    stdin=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True,
                    env={'LC_ALL': 'C'}
                )
                process.communicate(input=new_content)
        except Exception as e:
            print(f"Warning: Failed to update /etc/hosts: {e}")
    else:
        print(f"SIMULATION: Setting hostname to {new_hostname}")

    return jsonify({'success': True, 'hostname': new_hostname})

@app.route('/api/v1/system/logs', methods=['GET'])
@require_auth
def get_system_logs():
    """Get system logs"""
    logs = []
    
    try:
        # Strategy 1: Read syslog file (traditional Linux)
        log_file = '/var/log/syslog'
        if os.path.exists(log_file):
            try:
                cmd = [CMD['TAIL'], '-n', '50', log_file]
                res, err = run_sudo_command(cmd)
                if res and res.returncode == 0:
                    logs = res.stdout.splitlines()
                    return jsonify({'logs': logs})
            except Exception as e:
                print(f"Reading syslog failed: {e}")
                
        # Strategy 2: Use journalctl (systemd systems)
        try:
            cmd = [CMD['JOURNALCTL'], '-n', '50', '--no-pager', '--output=short']
            res, err = run_sudo_command(cmd)
            if res and res.returncode == 0:
                logs = res.stdout.splitlines()
                return jsonify({'logs': logs})
        except Exception:
            pass
            
        # Strategy 3: Mock/Dev
        logs = [
             f"[{datetime.now().isoformat()}] INFO: Could not read system logs",
             "--- Mock Logs (Dev Mode) ---",
             "Oct 27 10:00:01 alva-nas systemd[1]: Started AlvaOS Backend.",
             "Oct 27 10:05:23 alva-nas sshd[123]: Accepted password for root from 192.168.1.50"
        ]

    except Exception as e:
        logs = [f"Error fetching logs: {str(e)}"]

    return jsonify({'logs': logs})

# ============================================================================
# UPDATE MANAGEMENT ENDPOINTS (v0.3.0)
# ============================================================================

@app.route('/api/v1/updates/alvaos/check', methods=['GET'])
@require_auth
def check_alvaos_updates():
    channel = request.args.get('channel', 'stable')
    result = update_manager.check_alvaos_updates(channel)
    return jsonify(result), 200

@app.route('/api/v1/updates/alvaos/apply', methods=['POST'])
@require_auth
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

@app.route('/api/v1/updates/debian/check', methods=['GET'])
@require_auth
def check_debian_updates():
    result = update_manager.check_debian_updates()
    status = 200 if 'error' not in result else 500
    return jsonify(result), status

@app.route('/api/v1/updates/debian/apply', methods=['POST'])
@require_auth
def apply_debian_updates():
    data = request.get_json() or {}
    packages = data.get('packages')
    result = update_manager.apply_debian_updates(packages)
    status = 200 if result.get('success') else 500
    return jsonify(result), status

@app.route('/api/v1/updates/offline/scan', methods=['POST'])
@require_auth
def scan_offline_updates():
    data = request.get_json() or {}
    path = data.get('path')
    packages = []
    packages = update_manager.scan_offline_packages(path).get('packages', [])
    return jsonify({'packages': packages})

@app.route('/api/v1/updates/offline/apply', methods=['POST'])
@require_auth
def apply_offline_update():
    data = request.get_json() or {}
    package_path = data.get('path')
    if not package_path:
        return jsonify({'success': False, 'error': 'path required'}), 400
    result = update_manager.apply_alvaos_update(package_path)
    status = 200 if result.get('success') else 500
    return jsonify(result), status

@app.route('/api/v1/updates/status', methods=['GET'])
@require_auth
def get_update_status():
    return jsonify(update_manager.get_update_state())

@app.route('/api/v1/updates/history', methods=['GET'])
@require_auth
def get_update_history():
    return jsonify({'history': update_manager.get_update_history()})

@app.route('/api/v1/updates/settings', methods=['GET'])
@require_auth
def get_update_settings():
    return jsonify(update_manager.get_settings())

@app.route('/api/v1/updates/settings', methods=['POST'])
@require_auth
def save_update_settings():
    data = request.get_json() or {}
    settings = {
        "auto_check": bool(data.get("auto_check", True)),
        "auto_apply": bool(data.get("auto_apply", False)),
        "channel": data.get("channel", "stable")
    }
    saved = update_manager.save_settings(settings)
    return jsonify(saved)

# ============================================================================
# STORAGE MANAGEMENT ENDPOINTS (v0.2.0)
# ============================================================================

@app.route('/api/v1/storage/disks', methods=['GET'])
@require_auth
def get_disks():
    """Get list of all available disks"""
    disks = []
    
    try:
        if platform.system() == 'Linux':
            # Use lsblk to get disk information
            # LC_ALL=C for consistent parsing
            result = subprocess.run(
                ['env', 'LC_ALL=C', CMD['LSBLK'], '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,TRAN,RM'],
                capture_output=True, text=True, timeout=5
            )
            
            if result.returncode == 0:
                lsblk_data = json.loads(result.stdout)
                
                # Filter for disk devices (not partitions or loops)
                for device in lsblk_data.get('blockdevices', []):
                    if device.get('type') == 'disk':
                        # Skip loop devices and CD-ROMs
                        if device['name'].startswith('loop') or device['name'].startswith('sr'):
                            continue
                        
                        # Check if disk is system disk (has root partition)
                        is_system_disk = False
                        children = device.get('children', [])
                        for child in children:
                            if child.get('mountpoint') == '/':
                                is_system_disk = True
                                break
                        
                        # Strategy 2: Secure check via /proc/mounts logic
                        if not is_system_disk and platform.system() == 'Linux':
                            if is_secure_system_device(device['name']):
                                is_system_disk = True
                        
                        # Get SMART data
                        smart_status = 'unknown'
                        temp = None
                        power_on_hours = None
                        
                        try:
                            # Try to get detailed SMART info in JSON format
                            res, err = run_sudo_command([CMD['SMARTCTL'], '-H', '-A', '-j', f'/dev/{device["name"]}'], timeout=5)
                            
                            if res and (res.returncode == 0 or (res.returncode & 0x1) == 0):
                                smart_data = json.loads(res.stdout)
                                
                                # Status
                                if smart_data.get('smart_status', {}).get('passed'):
                                    smart_status = 'healthy'
                                else:
                                    smart_status = 'failed'
                                
                                # Extract temp and hours from attributes
                                attributes = smart_data.get('ata_smart_attributes', {}).get('table', [])
                                for attr in attributes:
                                    # Temperature (standard ID 194 or 190)
                                    if attr.get('id') in [194, 190]:
                                        temp = attr.get('raw', {}).get('value')
                                    # Power On Hours (standard ID 9)
                                    elif attr.get('id') == 9:
                                        power_on_hours = attr.get('raw', {}).get('value')
                        except:
                            # Fallback if JSON fails or smartctl not found
                            pass
                        
                        # Determine if removable (USB/SD)
                        is_removable = bool(device.get('rm')) or device.get('tran') == 'usb'

                        disk_info = {
                            'name': device['name'],
                            'path': f'/dev/{device["name"]}',
                            'size': device.get('size', 'Unknown'),
                            'model': device.get('model', 'Unknown').strip() if device.get('model') else 'Unknown',
                            'serial': device.get('serial', 'N/A'),
                            'fstype': device.get('fstype') or 'none', # Fix: detection of empty disks
                            'mountpoint': device.get('mountpoint', None),
                            'is_system_disk': is_system_disk,
                            'smart_status': smart_status,
                            'temp': temp,
                            'power_on_hours': power_on_hours,
                            'is_removable': is_removable,
                            'transport': device.get('tran', 'unknown'),
                            'partitions': []
                        }
                        
                        # Add partition information
                        for child in children:
                            partition = {
                                'name': child['name'],
                                'size': child.get('size', 'Unknown'),
                                'fstype': child.get('fstype') or 'none', # Fix here too
                                'mountpoint': child.get('mountpoint', None)
                            }
                            disk_info['partitions'].append(partition)
                        
                        disks.append(disk_info)
        else:
            # Mock data for development on non-Linux systems
            disks = [
                {
                    'name': 'sda',
                    'path': '/dev/sda',
                    'size': '500G',
                    'model': 'Samsung SSD 860',
                    'serial': 'S3Z9NB0K123456',
                    'fstype': 'ext4',
                    'mountpoint': '/',
                    'is_system_disk': True,
                    'is_removable': False,
                    'smart_status': 'healthy',
                    'temp': 32,
                    'power_on_hours': 12450,
                    'partitions': []
                },
                {
                    'name': 'sdb',
                    'path': '/dev/sdb',
                    'size': '2T',
                    'model': 'WDC WD20EFRX',
                    'serial': 'WD-WCC4M123456',
                    'fstype': 'none',
                    'mountpoint': None,
                    'is_system_disk': False,
                    'is_removable': False,
                    'smart_status': 'healthy',
                    'temp': 28,
                    'power_on_hours': 450,
                    'partitions': []
                },
                {
                    'name': 'sdc',
                    'path': '/dev/sdc',
                    'size': '64G',
                    'model': 'SanDisk Ultra',
                    'serial': 'SD-123456789',
                    'fstype': 'none',
                    'mountpoint': None,
                    'is_system_disk': False,
                    'is_removable': True,
                    'smart_status': 'unknown',
                    'temp': None,
                    'power_on_hours': None,
                    'partitions': []
                }
            ]
    
    except Exception as e:
        print(f"Error getting disk info: {e}")
        return jsonify({'error': str(e)}), 500
    
    return jsonify({'disks': disks})

@app.route('/api/v1/storage/disks/<disk_name>/smart', methods=['GET'])
@require_auth
def get_disk_smart(disk_name):
    """Get detailed SMART health attributes for a specific disk"""
    if not disk_name.isalnum() and not all(c in '._-' for c in disk_name if not c.isalnum()):
        return jsonify({'error': 'Invalid disk name'}), 400
        
    try:
        if platform.system() == 'Linux':
            # Try to determine if it's NVMe
            is_nvme = disk_name.startswith('nvme')
            
            # Get detailed SMART info in JSON format
            # For NVMe, smartctl -a is standard, for others we might need specific types
            cmd = [CMD['SMARTCTL'], '-a', '-j', f'/dev/{disk_name}']
            result, err = run_sudo_command(cmd, timeout=5)
            if err:
                return jsonify({'error': err}), 500
            if result and result.stdout:
                data = json.loads(result.stdout)
                # Check if SMART is actually supported/enabled
                if not data.get('smart_support', {}).get('available', True):
                    return jsonify({'error': 'SMART not supported on this device (common for USB sticks)'}), 200
                return jsonify(data)
            if result and result.stderr:
                return jsonify({'error': 'SMART returned no data', 'details': result.stderr}), 500
            return jsonify({'error': 'Device did not return any SMART data'}), 404
        else:
            # Mock data (unchanged)
            return jsonify({
                'json_format_version': [1, 0],
                'smart_status': {'passed': True},
                'temperature': {'current': 30},
                'ata_smart_attributes': {
                    'table': [
                        {'id': 1, 'name': 'Raw_Read_Error_Rate', 'value': 100, 'raw': {'value': 0}},
                        {'id': 5, 'name': 'Reallocated_Sector_Ct', 'value': 100, 'raw': {'value': 0}},
                        {'id': 9, 'name': 'Power_On_Hours', 'value': 98, 'raw': {'value': 1234}},
                        {'id': 194, 'name': 'Temperature_Celsius', 'value': 70, 'raw': {'value': 30}}
                    ]
                }
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/storage/disks/<disk_name>/wipe', methods=['POST'])
@require_auth
def wipe_disk(disk_name):
    """Wipe disk signatures and partition table to make it available for pools"""
    if not disk_name.isalnum() and not all(c in '._-' for c in disk_name if not c.isalnum()):
        return jsonify({'error': 'Invalid disk name'}), 400
    
    # SYSTEM DISK PROTECTION
    if is_secure_system_device(disk_name):
        return jsonify({'error': 'Operation denied: Cannot wipe the system disk.'}), 403
        
    try:
        if platform.system() == 'Linux':
            # 1. Unmount any Partitions
            # Get list of partitions for the disk
            try:
                lsblk_res = subprocess.run([CMD['LSBLK'], '-nr', '-o', 'NAME', f'/dev/{disk_name}'], capture_output=True, text=True)
                if lsblk_res.returncode == 0:
                    for line in lsblk_res.stdout.splitlines():
                        dev_path = f'/dev/{line.split()[0]}'
                        run_sudo_command([CMD['UMOUNT'], '-l', dev_path])
            except:
                pass
            
            # 2. Wipe file system signatures
            res, err = run_sudo_command([CMD['WIPEFS'], '-a', f'/dev/{disk_name}'])
            if err:
                return jsonify({'error': f'Wipe failed: {err}'}), 500
                
            # 3. Inform kernel of changes
            run_sudo_command([CMD['PARTPROBE'], f'/dev/{disk_name}'])
            invalidate_storage_cache('disks', 'pools')
            
            return jsonify({'success': True, 'message': f'Disk /dev/{disk_name} wiped successfully and is now ready for use.'})
        else:
            return jsonify({'success': True, 'message': f'Mock: Disk /dev/{disk_name} wiped.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Pool state file
POOLS_STATE_FILE = '/var/lib/alvaos/pools.json'

def load_pools_state():
    """Load pools state from file"""
    try:
        if os.path.exists(POOLS_STATE_FILE):
            with open(POOLS_STATE_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_pools_state(state):
    """Save pools state to file"""
    try:
        ensure_directories()
        with open(POOLS_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving pools state: {e}")

def sanitize_pool_name(name: str, fallback: str = "") -> str:
    base = re.sub(r'[^a-zA-Z0-9._-]+', '-', (name or '').strip()).strip('-')
    if base:
        return base
    if fallback:
        return f"pool-{fallback[:8]}"
    return "pool-imported"

def detect_btrfs_pools():
    pools = []
    root_btrfs_uuid = None
    if platform.system() != 'Linux':
        return pools, root_btrfs_uuid

    try:
        root_res, root_err = run_sudo_command([CMD['BTRFS'], 'filesystem', 'show', '/'])
        if root_res and root_res.returncode == 0 and not root_err:
            root_match = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", root_res.stdout, re.IGNORECASE)
            if root_match:
                root_btrfs_uuid = root_match.group(1).lower()
    except Exception as e:
        print(f"Warning: Could not detect root Btrfs UUID: {e}")

    result, err = run_sudo_command([CMD['BTRFS'], 'filesystem', 'show'])
    if not result or result.returncode != 0:
        return pools, root_btrfs_uuid

    output = result.stdout or ""
    fs_blocks = re.split(r'Label:', output)
    for block in fs_blocks:
        if not block.strip():
            continue

        uuid_match = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", block, re.IGNORECASE)
        if not uuid_match:
            continue
        uuid_val = uuid_match.group(1)

        label_match = re.match(r"\s*('(.*?)'|\S+)", block)
        label = 'none'
        if label_match:
            label = (label_match.group(2) or label_match.group(1)).strip("'")
            if label == 'none':
                label = 'Unlabeled'

        pool = {
            'id': uuid_val,
            'name': label,
            'uuid': uuid_val,
            'devices': [],
            'device_sizes_bytes': [],
            'total_size': 'Unknown',
            'used_size': 'Unknown',
            'raid_level': 'Single',
            'status': 'healthy',
            'is_system_pool': bool(root_btrfs_uuid and uuid_val.lower() == root_btrfs_uuid)
        }

        dev_lines = re.findall(r"path\s+(\S+)", block)
        pool['devices'] = [d.strip() for d in dev_lines]

        size_matches = re.findall(r"devid\s+\d+\s+size\s+(\d+\.?\d*[TiGkMBP]i?B)", block)
        for sm in size_matches:
            b = parse_size_to_bytes(sm)
            if b:
                pool['device_sizes_bytes'].append(b)

        if 'missing' in block.lower():
            pool['status'] = 'degraded'

        pools.append(pool)

    return pools, root_btrfs_uuid

# Initialize backup manager after pool helpers are available
if backup_manager is None:
    backup_manager = BackupManager(run_sudo_command, load_pools_state)
if buddy_backup_manager is None:
    buddy_backup_manager = BuddyBackupManager(run_sudo_command)

@app.route('/api/v1/storage/pools', methods=['GET', 'POST', 'DELETE'])
@require_auth
def manage_pools():
    """Manage Btrfs pools"""
    
    if request.method == 'GET':
        # Check cache
        current_time = time.time()
        if STORAGE_CACHE['pools']['expires'] > current_time:
            return jsonify({'pools': STORAGE_CACHE['pools']['data']})

        pools = []
        
        try:
            if platform.system() == 'Linux':
                pools, _ = detect_btrfs_pools()

                # Load pools state to get mount points
                pools_state = load_pools_state()
                state_key_by_lower = {str(key).lower(): key for key in pools_state.keys()}
                detected_ids = set()

                # Get usage information for each pool
                for pool in pools:
                    pool_id = str(pool.get('id', ''))
                    detected_ids.add(pool_id.lower())
                    state_key = pool_id if pool_id in pools_state else state_key_by_lower.get(pool_id.lower())
                    pool_state = pools_state.get(state_key, {}) if state_key else {}
                    mount_point = pool_state.get('mount_point', '')
                    pool['is_managed'] = bool(pool_state and mount_point)
                    if mount_point:
                        pool['mount_point'] = mount_point
                        if mount_point == '/':
                            pool['is_system_pool'] = True

                    if pool['devices']:
                        try:
                            usage_res, _ = run_sudo_command([CMD['BTRFS'], 'filesystem', 'usage', pool['devices'][0]], timeout=5)
                            if usage_res and usage_res.returncode == 0:
                                u_out = usage_res.stdout
                                if 'RAID1' in u_out: pool['raid_level'] = 'RAID1'
                                elif 'RAID10' in u_out: pool['raid_level'] = 'RAID10'
                                elif 'RAID0' in u_out: pool['raid_level'] = 'RAID0'

                                # Prefer explicit sizes when present
                                used_match = re.search(r"Used:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                dev_match = re.search(r"Device size:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                fs_match = re.search(r"Filesystem size:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                free_match = re.search(r"Free \(estimated\):\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)

                                if used_match:
                                    pool['used_size'] = used_match.group(1)
                                if dev_match:
                                    pool['total_size'] = dev_match.group(1)
                                elif fs_match:
                                    pool['total_size'] = fs_match.group(1)
                                if free_match:
                                    pool['free_size'] = free_match.group(1)
                        except Exception as e:
                            print(f"Error getting pool usage: {e}")

                        # Adjust usable size for mirror-like RAID
                        try:
                            if pool['raid_level'] == 'RAID1' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                max_bytes = max(pool['device_sizes_bytes'])
                                usable_bytes = max(0, total_bytes - max_bytes)
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID10' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                usable_bytes = total_bytes // 2
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                        except Exception as e:
                            print(f"Error adjusting usable size: {e}")

                        if (pool['used_size'] == 'Unknown' or 'Estimated' not in pool['total_size']) and mount_point:
                            try:
                                df_res = subprocess.run([CMD['DF'], '-h', mount_point], capture_output=True, text=True, timeout=2)
                                if df_res.returncode == 0:
                                    p_lines = df_res.stdout.strip().split('\n')
                                    if len(p_lines) >= 2:
                                        p_parts = p_lines[1].split()
                                        if len(p_parts) >= 4:
                                            pool['total_size'] = p_parts[1]
                                            pool['used_size'] = p_parts[2]
                            except:
                                pass

                # Fallback: include managed pools that were not returned by btrfs detection.
                for state_key, pool_state in pools_state.items():
                    state_id = str(state_key)
                    if state_id.lower() in detected_ids:
                        continue

                    mount_point = (pool_state.get('mount_point') or '').strip()
                    raid_level = str(pool_state.get('raid_level', 'single')).strip().lower()
                    pool_entry = {
                        'id': state_id,
                        'name': pool_state.get('name') or state_id,
                        'uuid': state_id,
                        'devices': pool_state.get('devices', []) if isinstance(pool_state.get('devices', []), list) else [],
                        'device_sizes_bytes': [],
                        'total_size': 'Unknown',
                        'used_size': 'Unknown',
                        'raid_level': 'Single' if raid_level == 'single' else raid_level.upper(),
                        'status': 'healthy',
                        'is_system_pool': mount_point == '/',
                        'is_managed': bool(mount_point)
                    }
                    if mount_point:
                        pool_entry['mount_point'] = mount_point
                        try:
                            df_res = subprocess.run([CMD['DF'], '-h', mount_point], capture_output=True, text=True, timeout=2)
                            if df_res.returncode == 0:
                                p_lines = df_res.stdout.strip().split('\n')
                                if len(p_lines) >= 2:
                                    p_parts = p_lines[1].split()
                                    if len(p_parts) >= 4:
                                        pool_entry['total_size'] = p_parts[1]
                                        pool_entry['used_size'] = p_parts[2]
                        except:
                            pass
                    pools.append(pool_entry)
            else:
                pools = [
                    {
                        'id': 'mock-pool-1', 'name': 'storage-pool', 'uuid': 'abc-123',
                        'devices': ['/dev/sdb'], 'total_size': '4.0TiB', 'used_size': '1.2TiB',
                        'raid_level': 'RAID1', 'status': 'healthy', 'is_managed': True
                    }
                ]
        except Exception as e:
            print(f"Error in manage_pools GET: {e}")
            
        STORAGE_CACHE['pools'] = {'data': pools, 'expires': current_time + CACHE_TTL}
        return jsonify({'pools': pools})
    
    elif request.method == 'POST':
        # Create new pool
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        pool_name = data.get('name', '').strip()
        devices = data.get('devices', [])
        raid_level = data.get('raid_level', 'single')
        
        # Validation
        if not pool_name:
            return jsonify({'error': 'Pool name is required'}), 400
        
        if not devices or len(devices) == 0:
            return jsonify({'error': 'At least one device is required'}), 400
        
        # Validate RAID level requirements
        if raid_level == 'raid1' and len(devices) < 2:
            return jsonify({'error': 'RAID1 requires at least 2 devices'}), 400
        
        if raid_level == 'raid10' and len(devices) < 4:
            return jsonify({'error': 'RAID10 requires at least 4 devices'}), 400
        
        # SYSTEM DISK PROTECTION
        for dev_path in devices:
            # dev_path is like /dev/sda
            dev_name = os.path.basename(dev_path)
            if is_secure_system_device(dev_name):
                return jsonify({'error': f'Operation denied: Device {dev_name} is the system disk.'}), 403
        
        try:
            if platform.system() == 'Linux':
                # Build mkfs.btrfs command
                cmd = [CMD['MKFS_BTRFS'], '-f', '-L', pool_name]
                
                # Add RAID level
                if raid_level != 'single':
                    cmd.extend(['-d', raid_level, '-m', raid_level])
                
                # Add devices
                cmd.extend(devices)
                
                # Execute pool creation
                res, err = run_sudo_command(cmd, timeout=60)
                
                if err:
                    return jsonify({'error': f'Failed to create pool: {err}'}), 500
                
                # Create mount point
                mount_point = f'/mnt/alvaos/{pool_name}'
                # Use sudo to create directory as we might not have permission in /mnt/alvaos
                res, err = run_sudo_command([CMD['MKDIR'], '-p', mount_point])
                if err:
                     return jsonify({'error': f'Failed to create mount point: {err}'}), 500
                
                # Mount the pool
                res, err = run_sudo_command([CMD['MOUNT'], devices[0], mount_point])
                if err:
                    return jsonify({'error': f'Pool created but failed to mount: {err}'}), 500
                
                # Save pool state
                pools_state = load_pools_state()
                
                # Get real BTRFS UUID to use as ID (matches get_pools logic)
                import uuid
                pool_id = str(uuid.uuid4()) # Fallback
                try:
                    # blkid returns just the UUID value
                    blkid_res, _ = run_sudo_command([CMD['BLKID'], '-s', 'UUID', '-o', 'value', devices[0]])
                    if blkid_res and blkid_res.returncode == 0:
                         real_uuid = blkid_res.stdout.strip()
                         if real_uuid:
                             pool_id = real_uuid
                except:
                    pass

                pools_state[pool_id] = {
                    'name': pool_name,
                    'devices': devices,
                    'raid_level': raid_level,
                    'mount_point': mount_point,
                    'created_at': datetime.now().isoformat()
                }
                save_pools_state(pools_state)
                invalidate_storage_cache('pools', 'disks')
                
                return jsonify({
                    'success': True,
                    'message': f'Pool "{pool_name}" created successfully',
                    'pool_id': pool_id,
                    'mount_point': mount_point
                })
            else:
                # Mock response for development
                return jsonify({
                    'success': True,
                    'message': f'Mock: Pool "{pool_name}" would be created with {len(devices)} devices in {raid_level} mode',
                    'pool_id': 'mock-pool-new'
                })
        
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Pool creation timed out'}), 500
        except Exception as e:
            return jsonify({'error': f'Pool creation failed: {str(e)}'}), 500
    
    elif request.method == 'DELETE':
        # Delete pool
        data = request.get_json()
        pool_id = data.get('pool_id')
        
        if not pool_id:
            return jsonify({'error': 'Pool ID is required'}), 400
        
        try:
            pools_state = load_pools_state()
            
            if pool_id not in pools_state:
                return jsonify({'error': 'Pool not found'}), 404
            
            pool_info = pools_state[pool_id]
            mount_point = pool_info.get('mount_point')
            
            if platform.system() == 'Linux':
                if mount_point:
                    run_sudo_command([CMD['UMOUNT'], mount_point], timeout=5)
                    
                    try:
                        run_sudo_command(['sudo', CMD['RMDIR'], mount_point])
                    except:
                        pass
                
                devices = pool_info.get('devices', [])
                for device in devices:
                    try:
                        run_sudo_command([CMD['WIPEFS'], '-a', device])
                    except Exception as e:
                         print(f"Warning: Failed to wipe device {device}: {e}")
            
            del pools_state[pool_id]
            save_pools_state(pools_state)
            invalidate_storage_cache('pools', 'disks')
            
            return jsonify({'success': True, 'message': 'Pool deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete pool: {str(e)}'}), 500

@app.route('/api/v1/storage/pools/import', methods=['POST'])
@require_auth
def import_pool():
    """Import an existing detected Btrfs pool into managed state."""
    data = request.get_json() or {}
    pool_id = (data.get('pool_id') or '').strip()
    requested_name = (data.get('pool_name') or '').strip()
    requested_mount = (data.get('mount_point') or '').strip()

    if not pool_id:
        return jsonify({'error': 'pool_id is required'}), 400

    if platform.system() != 'Linux':
        return jsonify({'success': True, 'message': f'Mock: Pool {pool_id} imported'}), 200

    try:
        detected_pools, root_uuid = detect_btrfs_pools()
        detected = None
        for pool in detected_pools:
            if (pool.get('id') or '').lower() == pool_id.lower():
                detected = pool
                break

        if not detected:
            return jsonify({'error': f'Pool not detected: {pool_id}'}), 404

        if root_uuid and pool_id.lower() == root_uuid:
            return jsonify({'error': 'System root pool cannot be imported via this endpoint'}), 400

        pools_state = load_pools_state()
        existing = pools_state.get(pool_id, {})
        pool_name = requested_name or existing.get('name') or detected.get('name') or f'pool-{pool_id[:8]}'
        safe_name = sanitize_pool_name(pool_name, pool_id)

        mount_point = requested_mount or existing.get('mount_point') or f'/mnt/alvaos/{safe_name}'
        used_mounts = {
            (item.get('mount_point') or '').strip()
            for item in pools_state.values()
            if isinstance(item, dict) and (item.get('mount_point') or '').strip()
        }
        if not requested_mount and mount_point in used_mounts and pool_id not in pools_state:
            base_mount = mount_point
            suffix = 2
            while mount_point in used_mounts:
                mount_point = f"{base_mount}-{suffix}"
                suffix += 1

        mk_res, mk_err = run_sudo_command([CMD['MKDIR'], '-p', mount_point])
        if mk_err or not mk_res or mk_res.returncode != 0:
            return jsonify({'error': f'Failed to create mount point: {mk_err or "unknown error"}'}), 500

        is_mounted = subprocess.run([CMD['MOUNTPOINT'], '-q', mount_point], check=False).returncode == 0
        if not is_mounted:
            mount_res, mount_err = run_sudo_command([CMD['MOUNT'], '-U', pool_id, mount_point], timeout=30)
            if mount_err or not mount_res or mount_res.returncode != 0:
                devices = detected.get('devices') or []
                fallback_device = devices[0] if devices else None
                if not fallback_device:
                    return jsonify({'error': f'Failed to mount pool and no fallback device available: {mount_err or "unknown error"}'}), 500
                mount_res, mount_err = run_sudo_command([CMD['MOUNT'], fallback_device, mount_point], timeout=30)
                if mount_err or not mount_res or mount_res.returncode != 0:
                    return jsonify({'error': f'Failed to mount imported pool: {mount_err or "unknown error"}'}), 500

        pools_state[pool_id] = {
            'name': pool_name,
            'devices': detected.get('devices', []),
            'raid_level': str(detected.get('raid_level', 'single')).lower(),
            'mount_point': mount_point,
            'created_at': existing.get('created_at') or datetime.now().isoformat(),
            'imported_at': datetime.now().isoformat(),
        }
        save_pools_state(pools_state)
        invalidate_storage_cache('pools', 'disks')

        return jsonify({
            'success': True,
            'message': f'Pool "{pool_name}" imported successfully',
            'pool_id': pool_id,
            'mount_point': mount_point
        })
    except Exception as e:
        return jsonify({'error': f'Failed to import pool: {str(e)}'}), 500

@app.route('/api/v1/storage/pools/<pool_id>/subvolumes', methods=['GET', 'POST', 'DELETE'])
@require_auth
def manage_subvolumes(pool_id):
    """Manage subvolumes in a pool"""
    
    pools_state = load_pools_state()
    
    if pool_id not in pools_state:
        return jsonify({'error': 'Pool not found'}), 404
    
    pool_info = pools_state[pool_id]
    mount_point = pool_info.get('mount_point')
    
    if request.method == 'GET':
        # List subvolumes
        subvolumes = []
        
        try:
            if platform.system() == 'Linux' and mount_point:
                # Use run_sudo_command instead of direct subprocess.run with 'sudo -n' string
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'list', mount_point], timeout=5)
                
                if res and res.returncode == 0:
                    for line in res.stdout.split('\n'):
                        if line.strip():
                            # Parse: ID 256 gen 7 top level 5 path subvol1
                            parts = line.split()
                            if 'path' in parts:
                                path_idx = parts.index('path')
                                if path_idx + 1 < len(parts):
                                    subvol_name = parts[path_idx + 1]
                                    subvolumes.append({
                                        'name': subvol_name,
                                        'path': f'{mount_point}/{subvol_name}'
                                    })
            else:
                # Mock data
                subvolumes = [
                    {'name': 'data', 'path': f'{mount_point}/data'},
                    {'name': 'backups', 'path': f'{mount_point}/backups'}
                ]
        
        except Exception as e:
            print(f"Error listing subvolumes: {e}")
        
        return jsonify({'subvolumes': subvolumes})
    
    elif request.method == 'POST':
        # Create subvolume
        data = request.get_json()
        subvol_name = data.get('name', '').strip()
        
        if not subvol_name:
            return jsonify({'error': 'Subvolume name is required'}), 400
        
        try:
            if platform.system() == 'Linux' and mount_point:
                subvol_path = f'{mount_point}/{subvol_name}'
                
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'create', subvol_path])
                
                if err:
                    return jsonify({'error': f'Failed to create subvolume: {err}'}), 500
                
                return jsonify({
                    'success': True,
                    'message': f'Subvolume "{subvol_name}" created',
                    'path': subvol_path
                })
            else:
                return jsonify({
                    'success': True,
                    'message': f'Mock: Subvolume "{subvol_name}" would be created'
                })
        
        except Exception as e:
            return jsonify({'error': f'Failed to create subvolume: {str(e)}'}), 500
    
    elif request.method == 'DELETE':
        # Delete subvolume
        data = request.get_json()
        subvol_name = data.get('name', '').strip()
        
        if not subvol_name:
            return jsonify({'error': 'Subvolume name is required'}), 400
        
        try:
            if platform.system() == 'Linux' and mount_point:
                subvol_path = f'{mount_point}/{subvol_name}'
                
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'delete', subvol_path])
                
                if err:
                    return jsonify({'error': f'Failed to delete subvolume: {err}'}), 500
                
                return jsonify({
                    'success': True,
                    'message': f'Subvolume "{subvol_name}" deleted'
                })
            else:
                return jsonify({
                    'success': True,
                    'message': f'Mock: Subvolume "{subvol_name}" would be deleted'
                })
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete subvolume: {str(e)}'}), 500

@app.route('/api/v1/storage/pools/<pool_id>/expand', methods=['POST'])
@require_auth
def expand_pool(pool_id):
    """Add new devices to an existing pool"""
    data = request.get_json()
    devices = data.get('devices', [])
    
    if not devices:
        return jsonify({'error': 'No devices provided'}), 400
    
    # SYSTEM DISK PROTECTION
    for dev_path in devices:
        dev_name = os.path.basename(dev_path)
        if is_secure_system_device(dev_name):
            return jsonify({'error': f'Operation denied: Device {dev_name} is the system disk.'}), 403
        
    pools_state = load_pools_state()
    if pool_id not in pools_state:
        return jsonify({'error': 'Pool not found'}), 404
        
    pool_info = pools_state[pool_id]
    mount_point = pool_info.get('mount_point')
    
    try:
        if platform.system() == 'Linux':
            if not mount_point:
                return jsonify({'error': 'Pool not mounted'}), 400
                
            # Add devices to pool
            # cmd: sudo btrfs device add /dev/sdX /mnt/alvaos/poolname
            cmd = [CMD['BTRFS'], 'device', 'add'] + devices + [mount_point]
            res, err = run_sudo_command(cmd, timeout=60)
            
            if err:
                return jsonify({'error': f'Failed to add devices: {err}'}), 500
                
            # Start a balance in background to redistribute data
            subprocess.Popen(
                build_privileged_cmd([CMD['BTRFS'], 'balance', 'start', mount_point]),
                env={'LC_ALL': 'C'}
            )
            
            # Update state
            pool_info['devices'].extend(devices)
            pools_state[pool_id] = pool_info
            save_pools_state(pools_state)
            invalidate_storage_cache('pools', 'disks')
            
            return jsonify({
                'success': True, 
                'message': f'Added {len(devices)} device(s) to pool "{pool_info["name"]}"'
            })
        else:
            # Mock
            return jsonify({
                'success': True, 
                'message': f'Mock: Added {len(devices)} device(s) to pool "{pool_info["name"]}"'
            })
    except Exception as e:
        return jsonify({'error': f'Failed to expand pool: {str(e)}'}), 500

# ============================================================================
# NETWORK SHARES ENDPOINTS (v0.2.0 Phase 3)
# ============================================================================

# Shares state file
SHARES_STATE_FILE = '/var/lib/alvaos/shares.json'

def load_shares_state():
    """Load shares state from file"""
    try:
        if os.path.exists(SHARES_STATE_FILE):
            with open(SHARES_STATE_FILE, 'r') as f:
                return json.load(f)
    except:
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

@app.route('/api/v1/storage/available-paths', methods=['GET'])
@require_auth
def get_available_paths():
    """Get list of all potential share paths (pools and subvolumes)"""
    paths = []
    
    # Add base mount point (Removed as per user request to only allow pools/subvolumes)
    # base_path = '/mnt/alvaos'
    # if platform.system() == 'Linux':
    #     try:
    #         if not os.path.exists(base_path):
    #             res, err = run_sudo_command(['sudo', 'mkdir', '-p', base_path])
    #             if err:
    #                 raise Exception(err)
    #     except Exception:
    #         # Don't include a non-existent path to avoid share creation failures
    #         base_path = None
    # if base_path:
    #     paths.append({'name': 'Default Storage Root', 'path': base_path})
    
    # Add Pools
    pools = load_pools_state()
    for pid, pool in pools.items():
        if 'mount_point' in pool:
            paths.append({'name': f"Pool: {pool['name']}", 'path': pool['mount_point']})
            
            # Dynamic Subvolume Lookup
            # Instead of looking for a non-existent cache file, we list subvolumes directly
            if platform.system() == 'Linux':
                try:
                    result, err = run_sudo_command(
                        [CMD['BTRFS'], 'subvolume', 'list', pool['mount_point']], timeout=3
                    )
                    if result and result.returncode == 0:
                        for line in result.stdout.split('\n'):
                             if not line.strip(): continue
                             # ID 256 gen 7 top level 5 path subvol1
                             parts = line.split()
                             path_idx = -1
                             try:
                                 path_idx = parts.index('path')
                             except ValueError:
                                 continue
                                 
                             if path_idx + 1 < len(parts):
                                 subvol_name = parts[path_idx + 1]
                                 paths.append({
                                     'name': f"  ↳ Subvolume: {subvol_name}", 
                                     'path': f"{pool['mount_point']}/{subvol_name}"
                                 })
                except Exception as e:
                    print(f"Error listing subvolumes for path: {e}")
            else:
                 # Mock subvolumes for dev
                 paths.append({'name': f"  ↳ Subvolume: mock-subvol", 'path': f"{pool['mount_point']}/mock-subvol"})
                    
    return jsonify({'paths': paths})

@app.route('/api/v1/storage/shares', methods=['GET', 'POST', 'DELETE'])
@require_auth
def manage_shares():
    """Manage network shares (NFS and SMB)"""
    
    if request.method == 'GET':
        # Load shares from state file
        shares_state = load_shares_state()
        shares = list(shares_state.values())
        return jsonify({'shares': shares})
    
    elif request.method == 'POST':
        # Create new share
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        share_name = data.get('name', '').strip()
        share_path = data.get('path', '').strip()
        protocol = data.get('protocol', 'nfs').lower()
        read_only = data.get('read_only', False)
        guest_access = data.get('guest_access', False)
        allowed_hosts = data.get('allowed_hosts', '*')
        smb_permissions = normalize_smb_permissions(data.get('smb_permissions', {}))
        
        # Validation
        if not share_name:
            return jsonify({'error': 'Share name is required'}), 400
        
        if not share_path:
            return jsonify({'error': 'Share path is required'}), 400
        
        if protocol not in ['nfs', 'smb']:
            return jsonify({'error': 'Protocol must be "nfs" or "smb"'}), 400
        
        # Check if path exists
        if platform.system() == 'Linux' and not os.path.exists(share_path):
            return jsonify({'error': f'Path does not exist: {share_path}'}), 400

        if protocol == 'smb' and isinstance(data.get('smb_permissions', {}), dict) and len(data.get('smb_permissions', {})) > 0:
            allowed_users = [u for u, r in smb_permissions.items() if r in ('read', 'write')]
            if not allowed_users and not guest_access:
                return jsonify({'error': 'At least one user must have read or write access'}), 400
        
        try:
            # Generate a unique share ID using hex token
            share_id = f'share-{secrets.token_hex(4)}'
            
            if platform.system() == 'Linux':
                if protocol == 'nfs':
                    # Configure NFS export
                    export_data = f'# AlvaOS Share: {share_name}\n{share_path} {allowed_hosts}({"ro" if read_only else "rw"},sync,no_subtree_check)\n'
                    
                    # Append to /etc/exports
                    cmd = build_privileged_cmd([CMD['TEE'], '-a', '/etc/exports'])
                    subprocess.run(cmd, input=export_data, text=True, check=True, env={'LC_ALL': 'C'})
                    
                    # Reload NFS exports
                    res, err = run_sudo_command([CMD['EXPORTFS'], '-ra'])
                    if err: return jsonify({'error': f'Failed to reload NFS: {err}'}), 500
                    
                elif protocol == 'smb':
                    # Configure Samba share
                    ensure_samba_conf_exists()
                    ensure_samba_global_settings(guest_access)
                    disable_samba_homes_share()
                    smb_config = render_smb_share_config(
                        share_name,
                        share_path,
                        read_only,
                        guest_access,
                        smb_permissions
                    )
                    
                    # Append to /etc/samba/smb.conf
                    cmd = build_privileged_cmd([CMD['TEE'], '-a', '/etc/samba/smb.conf'])
                    subprocess.run(cmd, input=smb_config, text=True, check=True, env={'LC_ALL': 'C'})
                    
                    # Restart Samba
                    res, err = run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'smbd'])
                    if err: return jsonify({'error': f'Failed to restart Samba: {err}'}), 500
                    
                    # Ensure root is in Samba database (for non-guest access)
                    # We look up the current password from AUTH_FILE or just wait for next setup/login
                    # For now, we expect the user to have gone through setup which already synced it.
            
            # Save share state
            shares_state = load_shares_state()
            shares_state[share_id] = {
                'id': share_id,
                'name': share_name,
                'path': share_path,
                'protocol': protocol,
                'read_only': read_only,
                'guest_access': guest_access,
                'allowed_hosts': allowed_hosts,
                'smb_permissions': smb_permissions,
                'smb_group': f'alvaos_{share_id}',
                'created_at': datetime.now().isoformat(),
                'status': 'active'
            }
            save_shares_state(shares_state)

            if platform.system() == 'Linux' and protocol == 'smb':
                apply_smb_permissions_to_fs(
                    share_path,
                    f'alvaos_{share_id}',
                    smb_permissions,
                    guest_access
                )
                reconcile_samba_guest_settings(shares_state)
            
            return jsonify({
                'success': True,
                'message': f'{protocol.upper()} share "{share_name}" created successfully',
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
                        pattern = rf'# AlvaOS Share: {share_name}\n\[{share_name}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
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

@app.route('/api/v1/storage/shares/permissions', methods=['PUT'])
@require_auth
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

    # If permissions provided but empty, allow clearing to default (no restrictions)
    if isinstance(data.get('smb_permissions', {}), dict) and len(data.get('smb_permissions', {})) > 0:
        allowed_users = [u for u, r in smb_permissions.items() if r in ('read', 'write')]
        if not allowed_users and not share_info.get('guest_access', False):
            return jsonify({'error': 'At least one user must have read or write access'}), 400

    share_info['smb_permissions'] = smb_permissions
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

    return jsonify({'success': True, 'message': 'SMB permissions updated'})

def mount_existing_pools():
    """Mount all known pools on startup"""
    if platform.system() != 'Linux':
        return

    print("Checking and mounting storage pools...")
    pools = load_pools_state()
    
    for pool_id, pool_info in pools.items():
        name = pool_info.get('name')
        mount_point = pool_info.get('mount_point')
        devices = pool_info.get('devices', [])
        
        if not name or not mount_point:
            continue
            
        try:
            # 1. Ensure mount point exists
            if not os.path.exists(mount_point):
                print(f"Creating mount point for {name}: {mount_point}")
                run_sudo_command([CMD['MKDIR'], '-p', mount_point])
            
            # 2. Check if already mounted
            is_mounted = subprocess.run([CMD['MOUNTPOINT'], '-q', mount_point], check=False).returncode == 0
            
            if not is_mounted:
                print(f"Mounting pool {name}...")
                mounted = False
                
                # STRATEGY 1: Mount by UUID (Robust against device changes)
                # The pool_id key is stored as the UUID during creation
                if pool_id and len(pool_id) > 20:
                    res, err = run_sudo_command([CMD['MOUNT'], '-U', pool_id, mount_point])
                    if res and res.returncode == 0:
                        print(f"Successfully mounted {name} using UUID: {pool_id}")
                        mounted = True
                
                # STRATEGY 2: Fallback to device path
                if not mounted and devices:
                    print(f"UUID mount not possible/failed for {name}, trying device path: {devices[0]}")
                    res, err = run_sudo_command([CMD['MOUNT'], devices[0], mount_point])
                    if err:
                        print(f"Error mounting {name}: {err}")
                    else:
                        print(f"Successfully mounted {name} using device path")
            else:
                print(f"Pool {name} is already mounted.")
                
        except Exception as e:
            print(f"Failed to process pool {name}: {e}")

# ============================================================================
# LOCAL BACKUP API (v0.6.0)
# ============================================================================

@app.route('/api/v1/backup/sources', methods=['GET'])
@require_auth
def get_backup_sources():
    """Get available backup sources from known Btrfs pools/subvolumes."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'sources': backup_manager.get_sources()})

@app.route('/api/v1/backup/targets', methods=['GET'])
@require_auth
def get_backup_targets():
    """Get available pool/subvolume target locations for snapshot storage."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'targets': backup_manager.get_target_locations()})

@app.route('/api/v1/backup/snapshots', methods=['GET', 'POST', 'DELETE'])
@require_auth
def backup_snapshots():
    """List snapshots or create a new snapshot."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500

    if request.method == 'GET':
        source_path = (request.args.get('source_path') or '').strip() or None
        snapshots = backup_manager.list_snapshots(source_path=source_path)
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

@app.route('/api/v1/backup/system/snapshots', methods=['GET'])
@require_auth
def backup_system_snapshots():
    """List full system snapshots."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({'snapshots': backup_manager.list_system_snapshots()})

@app.route('/api/v1/backup/system/snapshot', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/system/rollback', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/restore', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/settings', methods=['GET', 'POST'])
@require_auth
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

@app.route('/api/v1/backup/run', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/status', methods=['GET'])
@require_auth
def backup_status():
    """Get last run / next run backup status."""
    if backup_manager is None:
        return jsonify({'error': 'Backup manager not initialized'}), 500
    return jsonify({
        'status': backup_manager.get_status(),
        'system': backup_manager.get_system_state()
    })

@app.route('/api/v1/backup/pairing/status', methods=['GET'])
@require_auth
def buddy_pairing_status():
    """Get Buddy Backup pairing and tunnel status."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500
    return jsonify(buddy_backup_manager.get_status())

@app.route('/api/v1/backup/buddy/settings', methods=['GET', 'POST'])
@require_auth
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

@app.route('/api/v1/backup/buddy/peers/<node_id>/policy', methods=['GET', 'POST'])
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

@app.route('/api/v1/backup/pairing/generate', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/pairing/validate', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/pairing/accept', methods=['POST'])
def buddy_pairing_accept():
    """Accept reciprocal buddy pairing without interactive login."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

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

@app.route('/api/v1/backup/pairing/remove', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/pairing/restart', methods=['POST'])
@require_auth
def buddy_pairing_restart():
    """Apply buddy tunnel configuration and restart tunnel."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    success, payload = buddy_backup_manager.restart_tunnel()
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to restart tunnel')}), 500
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/pairing/test', methods=['POST'])
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

@app.route('/api/v1/backup/buddy/sync', methods=['POST'])
@require_auth
def buddy_sync_now():
    """Run buddy transfer sync for one peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    data = request.get_json() or {}
    node_id = (data.get('node_id') or '').strip()
    sources = data.get('sources')
    if sources is not None and not isinstance(sources, list):
        return jsonify({'error': 'sources must be a list'}), 400

    success, payload = buddy_backup_manager.sync_to_peer(node_id=node_id, sources=sources)
    if not success:
        return jsonify({'error': payload.get('error', 'Buddy sync failed')}), 400
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/buddy/remote/snapshots', methods=['GET'])
@require_auth
def buddy_remote_snapshots():
    """List snapshots stored on a remote buddy for this node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    node_id = (request.args.get('node_id') or '').strip()
    success, payload = buddy_backup_manager.fetch_remote_snapshots(node_id=node_id, limit=200)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to load remote snapshots')}), 400
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/buddy/remote/snapshot', methods=['DELETE'])
@require_auth
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

    success, payload = buddy_backup_manager.delete_remote_snapshot(node_id=node_id, stream_id=stream_id)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to delete remote snapshot')}), 400
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/buddy/restore/remote', methods=['POST'])
@require_auth
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

@app.route('/api/v1/backup/buddy/peer/upload', methods=['POST'])
def buddy_peer_upload():
    """Receive a buddy snapshot stream payload from a paired peer."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return jsonify({'error': 'Unauthorized buddy request'}), 403

    owner_node_id = (request.form.get('owner_node_id') or '').strip()
    from_node_id = (request.form.get('from_node_id') or '').strip()
    source_path = (request.form.get('source_path') or '').strip()
    snapshot_name = (request.form.get('snapshot_name') or '').strip()
    created_at = (request.form.get('created_at') or '').strip()
    encrypted = str(request.form.get('encrypted') or '').strip().lower() in ('1', 'true', 'yes')
    payload_file = request.files.get('payload')
    if payload_file is None:
        return jsonify({'error': 'payload file is required'}), 400

    success, payload = buddy_backup_manager.ingest_peer_stream(
        owner_node_id=owner_node_id,
        from_node_id=from_node_id,
        source_path=source_path,
        snapshot_name=snapshot_name,
        created_at=created_at,
        encrypted=encrypted,
        payload_stream=payload_file.stream,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to ingest buddy stream')}), 400
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/buddy/peer/list', methods=['GET'])
def buddy_peer_list():
    """List snapshot streams stored for a specific owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return jsonify({'error': 'Unauthorized buddy request'}), 403

    owner_node_id = (request.args.get('owner_node_id') or '').strip()
    limit = request.args.get('limit', 100)
    try:
        limit_int = int(limit)
    except Exception:
        limit_int = 100
    success, payload = buddy_backup_manager.list_peer_streams(owner_node_id=owner_node_id, limit=limit_int)
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to list streams')}), 400
    return jsonify({'success': True, **payload})

@app.route('/api/v1/backup/buddy/peer/download/<stream_id>', methods=['GET'])
def buddy_peer_download(stream_id):
    """Download one snapshot stream stored for an owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return jsonify({'error': 'Unauthorized buddy request'}), 403

    owner_node_id = (request.args.get('owner_node_id') or '').strip()
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

@app.route('/api/v1/backup/buddy/peer/delete/<stream_id>', methods=['DELETE'])
def buddy_peer_delete(stream_id):
    """Delete one snapshot stream payload stored for an owner node."""
    if buddy_backup_manager is None:
        return jsonify({'error': 'Buddy backup manager not initialized'}), 500

    secret = request.headers.get('X-Buddy-Secret', '')
    if not buddy_backup_manager.verify_buddy_api_secret(secret):
        return jsonify({'error': 'Unauthorized buddy request'}), 403

    owner_node_id = (request.args.get('owner_node_id') or '').strip()
    success, payload = buddy_backup_manager.delete_peer_stream(
        owner_node_id=owner_node_id,
        stream_id=stream_id,
    )
    if not success:
        return jsonify({'error': payload.get('error', 'Failed to delete stream')}), 404
    return jsonify({'success': True, 'deleted': payload.get('stream', {}), 'payload_removed': payload.get('payload_removed', False)})

# ============================================================================
# APP STORE & CONTAINER MANAGEMENT API
# ============================================================================

@app.route('/api/v1/apps/available', methods=['GET'])
@require_auth
def get_available_apps():
    """Get list of available apps from catalog"""
    apps, error = app_store.get_available_apps()
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'apps': apps})

@app.route('/api/v1/apps/available/<app_id>', methods=['GET'])
@require_auth
def get_app_details_endpoint(app_id):
    """Get details for a specific app"""
    details, error = app_store.get_app_details(app_id)
    if error:
        return jsonify({'error': error}), 404
    return jsonify(details)

@app.route('/api/v1/apps/install', methods=['POST'])
@require_auth
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
    
    # Convert port mappings to int keys
    if port_mappings:
        port_mappings = {int(k): int(v) for k, v in port_mappings.items()}
    
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

@app.route('/api/v1/apps/install/status', methods=['GET'])
@require_auth
def get_app_install_status():
    """Get the current app installation status"""
    return jsonify(app_store.get_install_status())

@app.route('/api/v1/apps/<app_id>', methods=['DELETE'])
@require_auth
def uninstall_app(app_id):
    """Uninstall an app"""
    data = request.get_json() or {}
    keep_data = data.get('keep_data', False)
    
    success, error = app_store.uninstall_app(app_id, keep_data=keep_data)
    if not success:
        return jsonify({'error': error}), 500
    
    return jsonify({'success': True, 'message': f'App "{app_id}" uninstalled successfully'})

@app.route('/api/v1/apps/installed', methods=['GET'])
@require_auth
def get_installed_apps():
    """Get list of installed apps"""
    apps = app_store.get_installed_apps()
    return jsonify({'apps': apps})

# Container Management
@app.route('/api/v1/containers', methods=['GET'])
@require_auth
def list_containers():
    """List all Docker containers"""
    containers, error = docker_manager.list_containers(all_containers=True)
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'containers': containers})

@app.route('/api/v1/containers/<container_id>', methods=['GET'])
@require_auth
def get_container_details_endpoint(container_id):
    """Get details for a specific container"""
    details, error = docker_manager.get_container_details(container_id)
    if error:
        return jsonify({'error': error}), 404
    return jsonify(details)

@app.route('/api/v1/containers/<container_id>/start', methods=['POST'])
@require_auth
def start_container(container_id):
    """Start a container"""
    success, error = docker_manager.start_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container started'})

@app.route('/api/v1/containers/<container_id>/stop', methods=['POST'])
@require_auth
def stop_container(container_id):
    """Stop a container"""
    success, error = docker_manager.stop_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container stopped'})

@app.route('/api/v1/containers/<container_id>/restart', methods=['POST'])
@require_auth
def restart_container(container_id):
    """Restart a container"""
    success, error = docker_manager.restart_container(container_id)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container restarted'})

@app.route('/api/v1/containers/<container_id>/logs', methods=['GET'])
@require_auth
def get_container_logs(container_id):
    """Get container logs"""
    lines = request.args.get('lines', 100, type=int)
    logs, error = docker_manager.get_container_logs(container_id, lines=lines)
    if error:
        return jsonify({'error': error}), 500
    return jsonify({'logs': logs})

@app.route('/api/v1/containers/<container_id>', methods=['DELETE'])
@require_auth
def delete_container(container_id):
    """Delete a container"""
    force = request.args.get('force', 'false').lower() == 'true'
    success, error = docker_manager.remove_container(container_id, force=force)
    if not success:
        return jsonify({'error': error}), 500
    return jsonify({'success': True, 'message': 'Container deleted'})

@app.route('/api/v1/docker/status', methods=['GET'])
@require_auth
def get_docker_status():
    """Check if Docker is running"""
    is_running, error = docker_manager.check_docker_running()
    return jsonify({
        'running': is_running,
        'error': error
    })

if __name__ == '__main__':
    print(f"Starting AlvaOS Backend v{VERSION}...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    
    # Ensure directories exist
    ensure_directories()
    
    # Mount existing pools on startup (persistence)
    mount_existing_pools()
    
    if not is_setup_complete():
        print("\n⚠️  SETUP REQUIRED: Access the Web UI to complete initial setup")
    
    app.run(host='0.0.0.0', port=8080, debug=False)

