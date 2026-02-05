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

app = Flask(__name__, static_folder=None) # Disable default static serving to force version replacement
# Fallback to current directory for dev if /opt doesn't exist
WEBUI_ROOT = '/opt/alvaos/webui'
if not os.path.exists(WEBUI_ROOT):
    WEBUI_ROOT = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app.static_folder = WEBUI_ROOT
CORS(app)

# Cache for storage information (TTL in seconds)
STORAGE_CACHE = {
    'disks': {'data': None, 'expires': 0},
    'pools': {'data': None, 'expires': 0}
}
CACHE_TTL = 5

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
            final_cmd = build_privileged_cmd(cmd[1:])
        else:
            final_cmd = cmd
        
        result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout, env=custom_env)
        
        if result.returncode != 0 and 'password is required' in result.stderr:
            return None, "System permission error: Passwordless sudo is not configured for this command. Please check the AlvaOS documentation for sudoers setup."
            
        return result, None
    except subprocess.TimeoutExpired:
        return None, "Command timed out"
    except Exception as e:
        return None, str(e)

def sync_samba_password(username, password):
    """Synchronize a system user's password with the Samba database"""
    try:
        process = subprocess.Popen(
            build_privileged_cmd(['smbpasswd', '-a', '-s', username]),
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
        result = subprocess.run(['id', '-u', username], capture_output=True, text=True)
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
            cmd = build_privileged_cmd(['tee', '/etc/samba/smb.conf'])
            subprocess.run(cmd, input=base_conf, text=True, check=True, env={'LC_ALL': 'C'})
    except Exception as e:
        print(f"Error ensuring smb.conf exists: {e}")

def ensure_samba_global_settings(guest_access):
    """Ensure global Samba settings needed for guest access."""
    if platform.system() != 'Linux' or not guest_access:
        return
    try:
        res, err = run_sudo_command(['sudo', 'cat', '/etc/samba/smb.conf'])
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
            build_privileged_cmd(['tee', '/etc/samba/smb.conf']),
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
        res, err = run_sudo_command(['sudo', 'cat', '/etc/samba/smb.conf'])
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
            build_privileged_cmd(['tee', '/etc/samba/smb.conf']),
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
        result = subprocess.run(['getent', 'group', group_name], capture_output=True, text=True)
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
        run_sudo_command(['sudo', 'groupadd', '-f', group_name])
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
            run_sudo_command(['sudo', 'gpasswd', '-a', user, group_name])

        # Remove users that are no longer allowed
        current_members = get_group_members(group_name)
        for user in current_members:
            if user not in allowed_users:
                run_sudo_command(['sudo', 'gpasswd', '-d', user, group_name])

        # Set group ownership and permissions on path
        run_sudo_command(['sudo', 'chgrp', '-R', group_name, share_path])

        # Determine permission mode
        if guest_access and not smb_permissions:
            # Guest-only: open permissions
            mode = '0777'
        else:
            # Setgid for group inheritance, grant write if any write users
            mode = '2770' if write_users else '2750'
        run_sudo_command(['sudo', 'chmod', '-R', mode, share_path])
    except Exception as e:
        print(f"Error applying SMB permissions to filesystem: {e}")

def disable_samba_homes_share():
    """Remove the default [homes] share if present to avoid user-named shares."""
    if platform.system() != 'Linux':
        return
    try:
        res, err = run_sudo_command(['sudo', 'cat', '/etc/samba/smb.conf'])
        if err or not res:
            return
        content = res.stdout
        pattern = r'\[homes\].*?(?=\n\[|\Z)'
        new_content = re.sub(pattern, '', content, flags=re.DOTALL)
        if new_content != content:
            process = subprocess.Popen(
                build_privileged_cmd(['tee', '/etc/samba/smb.conf']),
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
        res, err = run_sudo_command(['sudo', 'cat', '/etc/samba/smb.conf'])
        if err or not res:
            raise Exception(err or "Could not read smb.conf")
        content = res.stdout
        pattern = rf'# AlvaOS Share: {re.escape(share_name)}\n\[{re.escape(share_name)}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        content = content.rstrip() + "\n" + new_config
        process = subprocess.Popen(
            build_privileged_cmd(['tee', '/etc/samba/smb.conf']),
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
                build_privileged_cmd(['chpasswd']),
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
                run_sudo_command(['sudo', 'systemctl', 'restart', 'ssh'])
        except Exception as e:
            # Don't fail setup if SSH config update fails
            print(f"Warning: Could not update SSH config: {e}")
        
        # Sync to Samba
        sync_samba_password('root', password)
        
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
            res, err = run_sudo_command(['sudo', 'useradd', '-m', '-s', '/bin/bash', username])
            if err:
                return jsonify({'error': f'Failed to create user: {err}'}), 500

            # Set password
            process = subprocess.Popen(
                build_privileged_cmd(['chpasswd']),
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
            sync_samba_password(username, password)

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
            run_sudo_command(['sudo', 'smbpasswd', '-x', username])
            # Delete system user (keep home to avoid data loss)
            run_sudo_command(['sudo', 'userdel', username])

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
                build_privileged_cmd(['chpasswd']),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={'LC_ALL': 'C'}
            )
            stdout, stderr = process.communicate(input=f'{username}:{password}\n', timeout=5)
            if process.returncode != 0:
                return jsonify({'error': f'Failed to set password: {stderr}'}), 500
            sync_samba_password(username, password)
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
    
    # Network Information
    hostname = 'unknown'
    try:
        if platform.system() == 'Linux':
            res = subprocess.run(['hostnamectl', 'hostname'], capture_output=True, text=True, timeout=2)
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
                tz_result = subprocess.run(['timedatectl', 'show', '--property=Timezone', '--value'], 
                                         capture_output=True, text=True)
                if tz_result.returncode == 0:
                    timezone = tz_result.stdout.strip()
                
                # Get NTP status
                ntp_result = subprocess.run(['timedatectl', 'show', '--property=NTP', '--value'], 
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
        data = request.get_json()
        if platform.system() == 'Linux':
            try:
                if 'timezone' in data:
                    res, err = run_sudo_command(['sudo', 'timedatectl', 'set-timezone', data['timezone']])
                    if err: raise Exception(err)
                if 'ntp' in data:
                    ntp_val = 'true' if data['ntp'] else 'false'
                    res, err = run_sudo_command(['sudo', 'timedatectl', 'set-ntp', ntp_val])
                    if err: raise Exception(err)
                
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
            cmd = '/usr/sbin/reboot' if action == 'reboot' else '/usr/sbin/poweroff'
            subprocess.Popen(build_privileged_cmd([cmd]))
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
            res = subprocess.run(['hostnamectl', 'hostname'], capture_output=True, text=True, timeout=2)
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
                result = subprocess.run(['ip', 'route', 'show', 'default'], 
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
        # 1. Update hostname via hostnamectl
        res, err = run_sudo_command(['sudo', 'hostnamectl', 'set-hostname', new_hostname])
        if err:
             return jsonify({'error': f'Failed to set hostname: {err}'}), 500
             
        # 2. Update /etc/hosts to prevent "unable to resolve host" errors
        try:
            # Simple strategy: replace occurrences of the old hostname with the new one
            old_hostname = socket.gethostname()
            hosts_file = '/etc/hosts'
            
            # Read current hosts file
            res, err = run_sudo_command(['sudo', 'cat', hosts_file])
            if res and res.returncode == 0:
                content = res.stdout
                new_content = content.replace(old_hostname, new_hostname)
                
                # Write back with tee
                process = subprocess.Popen(
                    build_privileged_cmd(['tee', hosts_file]), 
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
                cmd = ['sudo', 'tail', '-n', '50', log_file]
                res, err = run_sudo_command(cmd)
                if res and res.returncode == 0:
                    logs = res.stdout.splitlines()
                    return jsonify({'logs': logs})
            except Exception as e:
                print(f"Reading syslog failed: {e}")
                
        # Strategy 2: Use journalctl (systemd systems)
        try:
            cmd = ['sudo', '/usr/bin/journalctl', '-n', '50', '--no-pager', '--output=short']
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
                ['env', 'LC_ALL=C', 'lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,TRAN,RM'],
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
                        
                        # Get SMART data
                        smart_status = 'unknown'
                        temp = None
                        power_on_hours = None
                        
                        try:
                            # Try to get detailed SMART info in JSON format
                            res, err = run_sudo_command(['sudo', 'smartctl', '-H', '-A', '-j', f'/dev/{device["name"]}'], timeout=5)
                            
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
            cmd = ['sudo', 'smartctl', '-a', '-j', f'/dev/{disk_name}']
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
        
    try:
        if platform.system() == 'Linux':
            # 1. Unmount any Partitions
            # Get list of partitions for the disk
            try:
                lsblk_res = subprocess.run(['lsblk', '-nr', '-o', 'NAME', f'/dev/{disk_name}'], capture_output=True, text=True)
                if lsblk_res.returncode == 0:
                    for line in lsblk_res.stdout.splitlines():
                        dev_path = f'/dev/{line.split()[0]}'
                        run_sudo_command(['sudo', 'umount', '-l', dev_path])
            except:
                pass
            
            # 2. Wipe file system signatures
            res, err = run_sudo_command(['sudo', 'wipefs', '-a', f'/dev/{disk_name}'])
            if err:
                return jsonify({'error': f'Wipe failed: {err}'}), 500
                
            # 3. Inform kernel of changes
            run_sudo_command(['sudo', 'partprobe', f'/dev/{disk_name}'])
            
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
                # Get list of Btrfs filesystems
                result, err = run_sudo_command(['sudo', 'btrfs', 'filesystem', 'show'])
                
                if result and result.returncode == 0:
                    output = result.stdout
                    # Split into filesystem blocks
                    fs_blocks = re.split(r'Label:', output)
                    
                    for block in fs_blocks:
                        if not block.strip(): continue
                        
                        uuid_match = re.search(r"uuid:\s+([a-f0-9-]+)", block)
                        if not uuid_match: continue
                        
                        uuid_val = uuid_match.group(1)
                        # Extract label
                        label_match = re.match(r"\s*('(.*?)'|\S+)", block)
                        label = 'none'
                        if label_match:
                            label = (label_match.group(2) or label_match.group(1)).strip("'")
                            if label == 'none': label = 'Unlabeled'
                        
                        pool = {
                            'id': uuid_val,
                            'name': label,
                            'uuid': uuid_val,
                            'devices': [],
                            'device_sizes_bytes': [],
                            'total_size': 'Unknown',
                            'used_size': 'Unknown',
                            'raid_level': 'Single',
                            'status': 'healthy'
                        }
                        
                        # Extract paths
                        dev_lines = re.findall(r"path\s+(\S+)", block)
                        pool['devices'] = [d.strip() for d in dev_lines]

                        # Extract device sizes
                        size_matches = re.findall(r"devid\s+\d+\s+size\s+(\d+\.?\d*[TiGkMBP]i?B)", block)
                        for sm in size_matches:
                            b = parse_size_to_bytes(sm)
                            if b:
                                pool['device_sizes_bytes'].append(b)
                        
                        if 'missing' in block.lower():
                            pool['status'] = 'degraded'
                        
                        pools.append(pool)
                    
                    # Load pools state to get mount points
                    pools_state = load_pools_state()
                    
                    # Get usage information for each pool
                    for pool in pools:
                        # Get mount point from state
                        pool_state = pools_state.get(pool['id'], {})
                        mount_point = pool_state.get('mount_point', '')
                        if mount_point:
                            pool['mount_point'] = mount_point
                        
                        if pool['devices']:
                            try:
                                usage_res, _ = run_sudo_command(['sudo', 'btrfs', 'filesystem', 'usage', pool['devices'][0]], timeout=5)
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

                            # Fallback to df if usage failed or didn't provide good info
                            if (pool['used_size'] == 'Unknown' or 'Estimated' not in pool['total_size']) and mount_point:
                                try:
                                    df_res = subprocess.run(['df', '-h', mount_point], capture_output=True, text=True, timeout=2)
                                    if df_res.returncode == 0:
                                        p_lines = df_res.stdout.strip().split('\n')
                                        if len(p_lines) >= 2:
                                            p_parts = p_lines[1].split()
                                            if len(p_parts) >= 4:
                                                pool['total_size'] = p_parts[1]
                                                pool['used_size'] = p_parts[2]
                                except: pass
            else:
                pools = [
                    {
                        'id': 'mock-pool-1', 'name': 'storage-pool', 'uuid': 'abc-123',
                        'devices': ['/dev/sdb'], 'total_size': '4.0TiB', 'used_size': '1.2TiB',
                        'raid_level': 'RAID1', 'status': 'healthy'
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
        
        try:
            if platform.system() == 'Linux':
                # Build mkfs.btrfs command
                cmd = ['sudo', 'mkfs.btrfs', '-f', '-L', pool_name]
                
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
                res, err = run_sudo_command(['sudo', 'mkdir', '-p', mount_point])
                if err:
                     return jsonify({'error': f'Failed to create mount point: {err}'}), 500
                
                # Mount the pool
                res, err = run_sudo_command(['sudo', 'mount', devices[0], mount_point])
                if err:
                    return jsonify({'error': f'Pool created but failed to mount: {err}'}), 500
                
                # Save pool state
                pools_state = load_pools_state()
                
                # Get real BTRFS UUID to use as ID (matches get_pools logic)
                import uuid
                pool_id = str(uuid.uuid4()) # Fallback
                try:
                    # blkid returns just the UUID value
                    blkid_res, _ = run_sudo_command(['sudo', 'blkid', '-s', 'UUID', '-o', 'value', devices[0]])
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
                    run_sudo_command(['sudo', 'umount', mount_point], timeout=5)
                    
                    try:
                        run_sudo_command(['sudo', 'rmdir', mount_point])
                    except:
                        pass
                
                devices = pool_info.get('devices', [])
                for device in devices:
                    try:
                        run_sudo_command(['sudo', 'wipefs', '-a', device])
                    except Exception as e:
                         print(f"Warning: Failed to wipe device {device}: {e}")
            
            del pools_state[pool_id]
            save_pools_state(pools_state)
            
            return jsonify({'success': True, 'message': 'Pool deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete pool: {str(e)}'}), 500

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
                # Fix: Ensure LC_ALL=C and non-interactive sudo
                result = subprocess.run(
                    ['sudo', '-n', 'env', 'LC_ALL=C', 'btrfs', 'subvolume', 'list', mount_point],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
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
                
                res, err = run_sudo_command(['sudo', 'btrfs', 'subvolume', 'create', subvol_path])
                
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
                
                res, err = run_sudo_command(['sudo', 'btrfs', 'subvolume', 'delete', subvol_path])
                
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
            cmd = ['sudo', 'btrfs', 'device', 'add'] + devices + [mount_point]
            res, err = run_sudo_command(cmd, timeout=60)
            
            if err:
                return jsonify({'error': f'Failed to add devices: {err}'}), 500
                
            # Start a balance in background to redistribute data
            subprocess.Popen(
                build_privileged_cmd(['btrfs', 'balance', 'start', mount_point]),
                env={'LC_ALL': 'C'}
            )
            
            # Update state
            pool_info['devices'].extend(devices)
            pools_state[pool_id] = pool_info
            save_pools_state(pools_state)
            
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
                        ['sudo', 'btrfs', 'subvolume', 'list', pool['mount_point']], timeout=3
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
                    cmd = build_privileged_cmd(['tee', '-a', '/etc/exports'])
                    subprocess.run(cmd, input=export_data, text=True, check=True, env={'LC_ALL': 'C'})
                    
                    # Reload NFS exports
                    res, err = run_sudo_command(['sudo', 'exportfs', '-ra'])
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
                    cmd = build_privileged_cmd(['tee', '-a', '/etc/samba/smb.conf'])
                    subprocess.run(cmd, input=smb_config, text=True, check=True, env={'LC_ALL': 'C'})
                    
                    # Restart Samba
                    res, err = run_sudo_command(['sudo', 'systemctl', 'restart', 'smbd'])
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
                        res, err = run_sudo_command(['sudo', 'cat', '/etc/exports'])
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
                        
                        # Write back using sudo tee
                        content = '\n'.join(new_lines) + '\n'
                        process = subprocess.Popen(build_privileged_cmd(['tee', '/etc/exports']), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={'LC_ALL': 'C'})
                        process.communicate(input=content)
                        
                        # Reload NFS exports
                        run_sudo_command(['sudo', 'exportfs', '-ra'])
                    except Exception as e:
                        print(f"Error removing NFS export: {e}")
                
                elif protocol == 'smb':
                    # Remove from /etc/samba/smb.conf using sudo
                    try:
                        # Read the file content via sudo
                        res, err = run_sudo_command(['sudo', 'cat', '/etc/samba/smb.conf'])
                        if err or not res:
                            raise Exception(f"Could not read /etc/samba/smb.conf: {err}")
                        
                        content = res.stdout
                        
                        # Find and remove the share section
                        import re
                        pattern = rf'# AlvaOS Share: {share_name}\n\[{share_name}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
                        content = re.sub(pattern, '', content, flags=re.DOTALL)
                        
                        # Write back using sudo tee
                        process = subprocess.Popen(build_privileged_cmd(['tee', '/etc/samba/smb.conf']), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={'LC_ALL': 'C'})
                        process.communicate(input=content)
                        
                        # Restart Samba
                        run_sudo_command(['sudo', 'systemctl', 'restart', 'smbd'])
                    except Exception as e:
                        print(f"Error removing SMB share: {e}")
                    # Remove share group
                    try:
                        group_name = share_info.get('smb_group')
                        if group_name:
                            run_sudo_command(['sudo', 'groupdel', group_name])
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
        run_sudo_command(['sudo', 'systemctl', 'restart', 'smbd'])

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
                run_sudo_command(['sudo', 'mkdir', '-p', mount_point])
            
            # 2. Check if already mounted
            is_mounted = subprocess.run(['mountpoint', '-q', mount_point], check=False).returncode == 0
            
            if not is_mounted:
                print(f"Mounting pool {name}...")
                mounted = False
                
                # STRATEGY 1: Mount by UUID (Robust against device changes)
                # The pool_id key is stored as the UUID during creation
                if pool_id and len(pool_id) > 20:
                    res, err = run_sudo_command(['sudo', 'mount', '-U', pool_id, mount_point])
                    if res and res.returncode == 0:
                        print(f"Successfully mounted {name} using UUID: {pool_id}")
                        mounted = True
                
                # STRATEGY 2: Fallback to device path
                if not mounted and devices:
                    print(f"UUID mount not possible/failed for {name}, trying device path: {devices[0]}")
                    res, err = run_sudo_command(['sudo', 'mount', devices[0], mount_point])
                    if err:
                        print(f"Error mounting {name}: {err}")
                    else:
                        print(f"Successfully mounted {name} using device path")
            else:
                print(f"Pool {name} is already mounted.")
                
        except Exception as e:
            print(f"Failed to process pool {name}: {e}")

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

