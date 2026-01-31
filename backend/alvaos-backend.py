#!/usr/bin/env python3
"""
AlvaOS Backend v0.1
Simple REST API for system information and setup
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

app = Flask(__name__, static_folder='/opt/alvaos/webui', static_url_path='')
CORS(app)

# Configuration
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
AUTH_FILE = '/var/lib/alvaos/auth.json'
CONFIG_DIR = '/etc/alvaos'
SESSIONS = {} # Token -> Username (In-memory for 0.1)

def ensure_directories():
    """Ensure necessary directories exist"""
    Path('/var/lib/alvaos').mkdir(parents=True, exist_ok=True)
    Path('/var/log/alvaos').mkdir(parents=True, exist_ok=True)

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
        'version': '0.1.5'
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
    """Serve the Web UI"""
    return send_from_directory('/opt/alvaos/webui', 'index.html')

@app.route('/api/v1/setup/status', methods=['GET'])
def get_setup_status():
    """Check if initial setup is required"""
    return jsonify({
        'setup_complete': is_setup_complete(),
        'version': '0.1.5'
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
                ['sudo', 'chpasswd'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
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
                subprocess.run(['sudo', 'systemctl', 'restart', 'ssh'], check=False)
        except Exception as e:
            # Don't fail setup if SSH config update fails
            print(f"Warning: Could not update SSH config: {e}")
        
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

@app.route('/api/v1/system/info', methods=['GET'])
@require_auth
def get_system_info():
    """Get comprehensive system information"""
    
    # CPU Information
    cpu_freq = psutil.cpu_freq()
    cpu_info = {
        'cores': psutil.cpu_count(logical=False),
        'threads': psutil.cpu_count(logical=True),
        'usage_percent': psutil.cpu_percent(interval=1),
        'frequency_mhz': round(cpu_freq.current, 2) if cpu_freq else 0,
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
    hostname = socket.gethostname()
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.error:
        ip_address = '127.0.0.1'
    
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
    }
    
    return jsonify({
        'version': '0.1.5',
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
        # Mocking time zone and NTP status
        return jsonify({
            'timezone': 'UTC' if platform.system() == 'Windows' else subprocess.getoutput("cat /etc/timezone"),
            'ntp_enabled': True,
            'current_time': datetime.now().isoformat()
        })
    
    if request.method == 'POST':
        data = request.get_json()
        # Mock implementation
        if 'timezone' in data:
            # On Linux: subprocess.run(['sudo', 'timedatectl', 'set-timezone', data['timezone']])
            pass
        if 'ntp' in data:
            # On Linux: subprocess.run(['sudo', 'timedatectl', 'set-ntp', 'true' if data['ntp'] else 'false'])
            pass
            
        return jsonify({'success': True, 'message': 'Time settings updated'})

@app.route('/api/v1/system/power', methods=['POST'])
@require_auth
def system_power():
    """Handle Shutdown/Reboot"""
    data = request.get_json()
    action = data.get('action')
    
    if action not in ['reboot', 'shutdown']:
        return jsonify({'error': 'Invalid action'}), 400
        
    # Linux implementation
    if platform.system() == 'Linux':
        try:
            cmd = 'reboot' if action == 'reboot' else 'poweroff'
            # Execute the command in background to allow response to be sent
            subprocess.Popen(['sudo', cmd])
            return jsonify({'success': True, 'message': f'System {action} initiated'})
        except Exception as e:
            return jsonify({'error': f'Failed to {action}: {str(e)}'}), 500
    else:
        # For non-Linux systems (dev/testing)
        return jsonify({'success': False, 'message': f'System {action} not supported on {platform.system()}'}), 400

@app.route('/api/v1/system/network', methods=['GET'])
@require_auth
def get_network_details():
    """Get detailed network configuration"""
    hostname = socket.gethostname()
    ip_address = "127.0.0.1"
    interface = "lo"
    subnet_mask = "255.255.255.0"
    gateway = "N/A"
    dns_servers = []
    
    # Try to get real network information
    try:
        # Get all network interfaces
        net_if_addrs = psutil.net_if_addrs()
        
        # Find the first non-loopback interface with an IPv4 address
        for iface_name, iface_addresses in net_if_addrs.items():
            if iface_name.startswith('lo'):
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
                if result.returncode == 0:
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

    # Execute change (Linux only)
    if platform.system() == 'Linux':
        try:
            subprocess.run(['sudo', 'hostnamectl', 'set-hostname', new_hostname], check=True)
            # Update /etc/hosts as well usually needed
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        # Windows/Dev simulation
        print(f"SIMULATION: Setting hostname to {new_hostname}")

    return jsonify({'success': True, 'hostname': new_hostname})

@app.route('/api/v1/system/logs', methods=['GET'])
@require_auth
def get_system_logs():
    """Get system logs"""
    logs = []
    
    # Try reading syslog on Linux
    log_file = '/var/log/syslog'
    if not os.path.exists(log_file):
        # Fallback for dev/windows
        log_file = 'backend.log'
        if not os.path.exists(log_file):
             # Create dummy logs
             return jsonify({'logs': [
                 f"[{datetime.now().isoformat()}] INFO: System running in DEV mode",
                 f"[{datetime.now().isoformat()}] WARN: Real syslog not found at {log_file}",
                 "--- Mock Logs ---",
                 "Oct 27 10:00:01 alva-nas systemd[1]: Started AlvaOS Backend.",
                 "Oct 27 10:05:23 alva-nas sshd[123]: Accepted password for root from 192.168.1.50"
             ]})

    try:
        # Read last 50 lines
        # Simple implementation
        with open(log_file, 'r') as f:
             lines = f.readlines()
             logs = [l.strip() for l in lines[-50:]]
    except Exception as e:
        logs = [f"Error reading logs: {str(e)}"]

    return jsonify({'logs': logs})

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '0.1.5',
        'setup_complete': is_setup_complete()
    })

if __name__ == '__main__':
    print("Starting AlvaOS Backend v0.1.5...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    
    # Ensure directories exist
    ensure_directories()
    
    if not is_setup_complete():
        print("\n⚠️  SETUP REQUIRED: Access the Web UI to complete initial setup")
    
    app.run(host='0.0.0.0', port=8080, debug=False)

