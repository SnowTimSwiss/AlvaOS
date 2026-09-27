#!/usr/bin/env python3
"""First-run setup, login, two-factor authentication and user management."""

# ── Standard library ──────────────────────────────────────────────────────────
import base64
import importlib
import io
import json
import os
import platform
import secrets
import socket
import subprocess
from datetime import datetime, timedelta

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from common import (
    CMD, run_sudo_command, build_privileged_cmd, ensure_directories, _utc_now,
)
from auth_manager import (
    AUTH_FILE,
    SESSIONS, TEMP_2FA_TOKENS, TEMP_2FA_TTL_SECONDS,
    is_setup_complete, mark_setup_complete,
    _create_session, _get_current_session, _purge_expired_sessions,
    _destroy_session, _destroy_all_sessions, require_auth,
    _check_rate_limit, _reset_rate_limit,
)
from password_utils import hash_password, verify_password
from shares_manager import (
    is_valid_username, system_user_exists, sync_samba_password,
    reconcile_samba_guest_settings, apply_smb_permissions_to_fs,
    render_smb_share_config, update_samba_share_section,
    load_shares_state, save_shares_state,
)

from app_services import VERSION

try:
    import pyotp
    import qrcode
    import qrcode.image.pil
    TOTP_AVAILABLE = True
except ImportError:
    TOTP_AVAILABLE = False
    print("Warning: pyotp/qrcode not installed. 2FA will be unavailable.")

bp = Blueprint('auth', __name__)

# ── Users state ───────────────────────────────────────────────────────────────
USERS_STATE_FILE = '/var/lib/alvaos/users.json'

def load_users_state():
    try:
        if os.path.exists(USERS_STATE_FILE):
            with open(USERS_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading users state: {e}")
    return {}

def save_users_state(state):
    try:
        ensure_directories()
        with open(USERS_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving users state: {e}")

# ── 2FA helpers ───────────────────────────────────────────────────────────────
def _load_totp_secret():
    try:
        with open(AUTH_FILE, 'r') as f:
            data = json.load(f)
        return data.get('totp_secret') or None
    except Exception:
        return None

def _save_totp_secret(secret):
    try:
        with open(AUTH_FILE, 'r') as f:
            data = json.load(f)
        if secret is None:
            data.pop('totp_secret', None)
        else:
            data['totp_secret'] = secret
        with open(AUTH_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving TOTP secret: {e}")
        return False

def _verify_totp(secret, code):
    if not TOTP_AVAILABLE or not secret or not code:
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(str(code).strip(), valid_window=1)
    except Exception:
        return False

def _refresh_totp_runtime():
    global pyotp, qrcode, base64, TOTP_AVAILABLE
    try:
        pyotp = importlib.import_module('pyotp')
        qrcode = importlib.import_module('qrcode')
        importlib.import_module('qrcode.image.pil')
        base64 = importlib.import_module('base64')
        TOTP_AVAILABLE = True
        return True, None
    except Exception as e:
        TOTP_AVAILABLE = False
        return False, str(e)


@bp.route('/api/v1/setup/status', methods=['GET'])
def get_setup_status():
    """Check if initial setup is required"""
    return jsonify({
        'setup_complete': is_setup_complete(),
        'version': VERSION
    })

@bp.route('/api/v1/setup/complete', methods=['POST'])
def complete_setup():
    """Complete initial setup with root password change"""
    if is_setup_complete():
        return jsonify({'error': 'Setup already completed'}), 409

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
        
        # SSH root login stays disabled after setup. It is opt-in through
        # System → Remote Access (/api/v1/system/ssh), so a fresh box never ends
        # up reachable over SSH without the admin explicitly asking for it.

        # Sync to Samba
        sync_samba_password('root', password)

        # Set Timezone if provided
        if 'timezone' in data and platform.system() == 'Linux':
            try:
                run_sudo_command([CMD['TIMEDATECTL'], 'set-timezone', data['timezone']])
            except Exception as e:
                print(f"Warning: Could not set timezone during setup: {e}")
        
        # Mark setup as complete
        mark_setup_complete(password, VERSION)

        # A freshly provisioned box must not carry any session from before it
        # had an owner.
        _destroy_all_sessions()

        # Auto-login for the setup session
        token = _create_session('root', role='admin')
        
        return jsonify({
            'success': True,
            'message': 'Setup completed successfully',
            'token': token,
            'csrf_token': SESSIONS.get(token, {}).get('csrf_token', '')
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API paths reachable before first-run setup is complete. Everything else is
# refused until an admin password exists, so an unconfigured box on the network
# cannot be inventoried or taken over through the browser of anyone on the LAN.

@bp.route('/api/v1/auth/login', methods=['POST'])
def login():
    """Login with root password. Returns full token or temp_token when 2FA is required."""
    if not is_setup_complete():
        return jsonify({'error': 'Setup not complete'}), 400

    # Rate limiting
    client_ip = request.remote_addr or 'unknown'
    allowed, retry_after = _check_rate_limit(client_ip)
    if not allowed:
        resp = jsonify({'error': f'Too many login attempts. Try again in {retry_after}s.'})
        resp.headers['Retry-After'] = str(retry_after)
        return resp, 429

    data = request.get_json()
    if not data or 'password' not in data:
        return jsonify({'error': 'Password required'}), 400

    password = data['password']

    try:
        with open(AUTH_FILE, 'r') as f:
            auth_data = json.load(f)

        is_valid, needs_rehash = verify_password(password, auth_data)
        if not is_valid:
            return jsonify({'error': 'Invalid password'}), 401

        # Transparently upgrade legacy/weaker hashes on successful login,
        # preserving any existing fields such as the 2FA secret.
        if needs_rehash:
            try:
                auth_data.update(hash_password(password))
                with open(AUTH_FILE, 'w') as f:
                    json.dump(auth_data, f)
            except Exception as e:
                print(f"Warning: could not upgrade password hash: {e}")

        _reset_rate_limit(client_ip)

        # Check 2FA
        totp_secret = auth_data.get('totp_secret')
        if totp_secret:
            # Issue short-lived temp token; client must call /api/v1/auth/2fa/complete
            temp_token = secrets.token_hex(24)
            expires_at = (_utc_now() + timedelta(seconds=TEMP_2FA_TTL_SECONDS)).isoformat()
            TEMP_2FA_TOKENS[temp_token] = {'username': 'root', 'role': 'admin', 'expires_at': expires_at}
            return jsonify({'require_2fa': True, 'temp_token': temp_token})

        token = _create_session('root', role='admin')
        return jsonify({
            'token': token,
            'csrf_token': SESSIONS.get(token, {}).get('csrf_token', ''),
            'success': True
        })

    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Authentication failed'}), 500

@bp.route('/api/v1/auth/2fa/complete', methods=['POST'])
def complete_2fa_login():
    """Complete login by verifying a TOTP code against a temp token."""
    data = request.get_json() or {}
    temp_token = str(data.get('temp_token', '')).strip()
    code = str(data.get('code', '')).strip()

    if not temp_token or not code:
        return jsonify({'error': 'temp_token and code are required'}), 400

    _purge_expired_sessions()
    pending = TEMP_2FA_TOKENS.get(temp_token)
    if not pending:
        return jsonify({'error': 'Invalid or expired token'}), 401

    totp_secret = _load_totp_secret()
    if not totp_secret:
        return jsonify({'error': '2FA not configured on server'}), 400

    if not _verify_totp(totp_secret, code):
        return jsonify({'error': 'Invalid 2FA code'}), 401

    del TEMP_2FA_TOKENS[temp_token]
    token = _create_session(pending['username'], role=pending.get('role', 'admin'))
    return jsonify({
        'token': token,
        'csrf_token': SESSIONS.get(token, {}).get('csrf_token', ''),
        'success': True
    })

@bp.route('/api/v1/auth/logout', methods=['POST'])
@require_auth
def logout():
    """Revoke the current session token.

    Sessions survive a backend restart, so clearing the browser's copy of the
    token is no longer enough to end a session - the server has to forget it.
    """
    token = request.headers.get('Authorization', '').strip()
    _destroy_session(token)
    return jsonify({'success': True})

@bp.route('/api/v1/auth/2fa/status', methods=['GET'])
@require_auth
def get_2fa_status():
    """Return whether 2FA is currently enabled."""
    secret = _load_totp_secret()
    return jsonify({'enabled': bool(secret), 'totp_available': TOTP_AVAILABLE})

@bp.route('/api/v1/auth/csrf-token', methods=['GET'])
@require_auth
def get_csrf_token():
    """Return the CSRF token for the current session."""
    session = _get_current_session()
    if not session:
        return jsonify({'error': 'Invalid session'}), 401
    return jsonify({'csrf_token': session.get('csrf_token', '')})

@bp.route('/api/v1/auth/2fa/setup', methods=['POST'])
@require_auth
def setup_2fa():
    """Generate a new TOTP secret and return a QR code data-URI."""
    if not TOTP_AVAILABLE:
        return jsonify({'error': '2FA library not installed on server'}), 501
    try:
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        hostname = socket.gethostname() or 'AlvaOS'
        provisioning_uri = totp.provisioning_uri(name='admin', issuer_name=f'AlvaOS ({hostname})')
        # Build QR code as a base64 PNG data-URI
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return jsonify({
            'secret': secret,
            'qr_code': f'data:image/png;base64,{qr_b64}',
            'provisioning_uri': provisioning_uri,
        })
    except Exception as e:
        print(f"2FA setup error: {e}")
        return jsonify({'error': 'Failed to generate 2FA setup'}), 500

@bp.route('/api/v1/auth/2fa/install', methods=['POST'])
@require_auth(require_admin=True)
def install_2fa_library():
    """Repair/install missing 2FA system packages and reload them without a full backend restart."""
    ready, _err = _refresh_totp_runtime()
    if ready:
        return jsonify({
            'success': True,
            'message': '2FA library is already installed.',
            'totp_available': True,
            'restart_required': False
        })

    install_cmd = [
        CMD['APT_GET'],
        'install',
        '-y',
        '--no-install-recommends',
        'python3-pyotp',
        'python3-qrcode',
        'python3-pil'
    ]
    _result, install_err = run_sudo_command(install_cmd, timeout=300, extra_env={
        'DEBIAN_FRONTEND': 'noninteractive'
    })
    if install_err:
        return jsonify({'error': f'Failed to install 2FA system packages: {install_err}'}), 500

    ready, reload_err = _refresh_totp_runtime()
    if not ready:
        return jsonify({'error': f'2FA packages installed but failed to load: {reload_err}'}), 500

    return jsonify({
        'success': True,
        'message': '2FA packages installed successfully. You can now enable 2FA.',
        'totp_available': True,
        'restart_required': False
    })

@bp.route('/api/v1/auth/2fa/verify-setup', methods=['POST'])
@require_auth
def verify_2fa_setup():
    """Confirm first TOTP code to activate 2FA."""
    if not TOTP_AVAILABLE:
        return jsonify({'error': '2FA library not installed'}), 501
    data = request.get_json() or {}
    secret = str(data.get('secret', '')).strip()
    code = str(data.get('code', '')).strip()
    if not secret or not code:
        return jsonify({'error': 'secret and code are required'}), 400
    if not _verify_totp(secret, code):
        return jsonify({'error': 'Invalid code — please check your authenticator app'}), 400
    if not _save_totp_secret(secret):
        return jsonify({'error': 'Failed to save 2FA secret'}), 500
    return jsonify({'success': True, 'message': '2FA enabled successfully'})

@bp.route('/api/v1/auth/2fa/disable', methods=['POST'])
@require_auth
def disable_2fa():
    """Disable 2FA. Requires current password confirmation."""
    data = request.get_json() or {}
    password = str(data.get('password', '')).strip()
    if not password:
        return jsonify({'error': 'Current password is required'}), 400
    try:
        with open(AUTH_FILE, 'r') as f:
            auth_data = json.load(f)
        is_valid, _ = verify_password(password, auth_data)
        if not is_valid:
            return jsonify({'error': 'Incorrect password'}), 401
    except Exception:
        return jsonify({'error': 'Authentication failed'}), 500
    if not _save_totp_secret(None):
        return jsonify({'error': 'Failed to disable 2FA'}), 500
    return jsonify({'success': True, 'message': '2FA disabled'})

@bp.route('/api/v1/users', methods=['GET', 'POST', 'DELETE'])
@require_auth(require_admin=True)
def manage_users():
    """Manage system users and Samba users"""
    if request.method == 'GET':
        users_state = load_users_state()
        users = []
        for username, info in users_state.items():
            users.append({
                'username': username,
                'created_at': info.get('created_at'),
                'system_exists': system_user_exists(username)
            })
        users.sort(key=lambda u: u['username'])
        return jsonify({'users': users})

    # CSRF required for state-changing operations
    session = _get_current_session()
    csrf_token = request.headers.get('X-CSRF-Token', '').strip()
    if not csrf_token or csrf_token != (session or {}).get('csrf_token', ''):
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

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
            _, smb_err = run_sudo_command([CMD['SMBPASSWD'], '-x', username])
            if smb_err:
                smb_detail = str(smb_err).lower()
                # Ignore if SMB account does not exist yet.
                if 'failed to find entry for user' not in smb_detail:
                    return jsonify({'error': f'Failed to remove SMB user: {smb_err}'}), 500
            # Delete system user (keep home to avoid data loss)
            userdel_res, userdel_err = run_sudo_command([CMD['USERDEL'], username])
            if userdel_err or not userdel_res or userdel_res.returncode != 0:
                userdel_detail = str(userdel_err or '').lower()
                if (
                    'does not exist' not in userdel_detail
                    and 'no such user' not in userdel_detail
                ):
                    return jsonify({'error': f'Failed to delete user: {userdel_err or "unknown error"}'}), 500

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

@bp.route('/api/v1/users/<username>', methods=['PATCH'])
@require_auth(require_admin=True)
def update_user(username):
    """Update a share user's password"""
    username = username.strip()
    if not username or not is_valid_username(username):
        return jsonify({'error': 'Invalid username'}), 400
    if username in ('root', 'alvaos'):
        return jsonify({'error': 'Reserved username'}), 400

    data = request.get_json() or {}
    users_state = load_users_state()
    if username not in users_state and not system_user_exists(username):
        return jsonify({'error': 'User not found'}), 404

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
