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

app = Flask(__name__, static_folder='/opt/alvaos/webui', static_url_path='')
CORS(app)

# Configuration
SETUP_STATUS_FILE = '/var/lib/alvaos/setup_complete.json'
CONFIG_DIR = '/etc/alvaos'

def ensure_directories():
    """Ensure necessary directories exist"""
    Path('/var/lib/alvaos').mkdir(parents=True, exist_ok=True)
    Path('/var/log/alvaos').mkdir(parents=True, exist_ok=True)

def is_setup_complete():
    """Check if initial setup has been completed"""
    return os.path.exists(SETUP_STATUS_FILE)

def mark_setup_complete():
    """Mark the initial setup as complete"""
    ensure_directories()
    setup_data = {
        'setup_completed': True,
        'completed_at': datetime.now().isoformat(),
        'version': '0.1.0'
    }
    with open(SETUP_STATUS_FILE, 'w') as f:
        json.dump(setup_data, f, indent=2)

@app.route('/')
def index():
    """Serve the Web UI"""
    return send_from_directory('/opt/alvaos/webui', 'index.html')

@app.route('/api/v1/setup/status', methods=['GET'])
def get_setup_status():
    """Check if initial setup is required"""
    return jsonify({
        'setup_complete': is_setup_complete(),
        'version': '0.1.0'
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
        mark_setup_complete()
        
        return jsonify({
            'success': True,
            'message': 'Setup completed successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/system/info', methods=['GET'])
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
        'version': '0.1.0',
        'timestamp': datetime.now().isoformat(),
        'cpu': cpu_info,
        'memory': memory_info,
        'disk': disk_info,
        'network': network_info,
        'system': system_info,
    })

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '0.1.0',
        'setup_complete': is_setup_complete()
    })

if __name__ == '__main__':
    print("Starting AlvaOS Backend v0.1...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    
    # Ensure directories exist
    ensure_directories()
    
    if not is_setup_complete():
        print("\n⚠️  SETUP REQUIRED: Access the Web UI to complete initial setup")
    
    app.run(host='0.0.0.0', port=8080, debug=False)

