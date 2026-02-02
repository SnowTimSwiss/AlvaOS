#!/usr/bin/env python3
"""
AlvaOS Backend {{VERSION}}
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

app = Flask(__name__, static_folder='/opt/alvaos/webui', static_url_path='')
CORS(app)

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
CONFIG_DIR = '/etc/alvaos'
SESSIONS = {} # Token -> Username (In-memory for 0.1)

def run_sudo_command(cmd, timeout=30):
    """Helper to run a command with sudo and handle password prompts gracefully"""
    try:
        # Use -n (non-interactive) to fail quickly if password is required
        if cmd[0] == 'sudo':
            cmd.insert(1, '-n')
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
        if result.returncode != 0 and 'password is required' in result.stderr:
            return None, "System permission error: Passwordless sudo is not configured for this command. Please check the AlvaOS documentation for sudoers setup."
            
        return result, None
    except subprocess.TimeoutExpired:
        return None, "Command timed out"
    except Exception as e:
        return None, str(e)

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
        # Check if it's an HTML or CSS file that might need replacement
        if filename.endswith(('.html', '.css')):
            content = ""
            with open(os.path.join(app.static_folder, filename), 'r') as f:
                content = f.read()
            
            # Replace placeholder
            content = content.replace('{{VERSION}}', VERSION)
            
            # Create a response with correct mimetype
            from flask import Response
            mimetype = 'text/html' if filename.endswith('.html') else 'text/css'
            return Response(content, mimetype=mimetype)
    except Exception as e:
        print(f"Error serving {filename}: {e}")
        
    return send_from_directory(app.static_folder, filename)

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
                    subprocess.run(['sudo', 'timedatectl', 'set-timezone', data['timezone']], check=True)
                if 'ntp' in data:
                    ntp_val = 'true' if data['ntp'] else 'false'
                    subprocess.run(['sudo', 'timedatectl', 'set-ntp', ntp_val], check=True)
                
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
                process = subprocess.Popen(['sudo', 'tee', hosts_file], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
            result = subprocess.run(
                ['lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,TRAN,RM'],
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            
            if result.stdout:
                data = json.loads(result.stdout)
                # Check if SMART is actually supported/enabled
                if not data.get('smart_support', {}).get('available', True):
                    return jsonify({'error': 'SMART not supported on this device (common for USB sticks)'}), 200
                return jsonify(data)
            else:
                return jsonify({'error': 'Device did not return any SMART data', 'details': result.stderr}), 404
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
            subprocess.run(f'sudo umount /dev/{disk_name}*', shell=True, check=False)
            
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
        pools = []
        
        try:
            if platform.system() == 'Linux':
                # Get list of Btrfs filesystems
                result = subprocess.run(
                    ['sudo', 'btrfs', 'filesystem', 'show'],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0:
                    # Parse btrfs filesystem show output
                    current_pool = None
                    for line in result.stdout.split('\n'):
                        line = line.strip()
                        
                        # New filesystem entry
                        # Example lines: 
                        # Label: 'my pool'  uuid: 1234-5678...
                        # Label: none  uuid: 1234-5678...
                        if line.startswith('Label:'):
                            if current_pool:
                                pools.append(current_pool)
                            
                            import re
                            # Match Label: '...' or Label: none
                            label_match = re.search(r"Label:\s+('(.*?)'|(\S+))", line)
                            uuid_match = re.search(r"uuid:\s+(\S+)", line)
                            
                            label = 'none'
                            if label_match:
                                label = label_match.group(2) or label_match.group(3)
                            
                            uuid_val = uuid_match.group(1) if uuid_match else ''
                            
                            current_pool = {
                                'id': uuid_val,
                                'name': label,
                                'uuid': uuid_val,
                                'devices': [],
                                'total_size': 0,
                                'used_size': 0,
                                'raid_level': 'unknown'
                            }
                        
                        # Device entry
                        elif line.startswith('devid') and current_pool:
                            # Parse: devid 1 size 100.00GiB used 10.00GiB path /dev/sdb
                            parts = line.split()
                            dev_path = ''
                            if 'path' in parts:
                                dev_path = parts[parts.index('path') + 1]
                            
                            if dev_path:
                                current_pool['devices'].append(dev_path)
                    
                    # Add last pool
                    if current_pool:
                        pools.append(current_pool)
                    
                    # Get usage information for each pool
                    for pool in pools:
                        if pool['devices']:
                            try:
                                usage_result = subprocess.run(
                                    ['sudo', 'btrfs', 'filesystem', 'usage', pool['devices'][0]],
                                    capture_output=True, text=True, timeout=3
                                )
                                
                                if usage_result.returncode == 0:
                                    # Parse usage output with better logical size calculation
                                    used_bytes = 0
                                    free_estimated_bytes = 0
                                    
                                    for line in usage_result.stdout.split('\n'):
                                        line = line.strip()
                                        if line.startswith('Used:'):
                                            # This is logical used data
                                            try:
                                                val = line.split(':')[1].strip()
                                                if 'TiB' in val: used_bytes = float(val.replace('TiB','')) * 1024**4
                                                elif 'GiB' in val: used_bytes = float(val.replace('GiB','')) * 1024**3
                                                elif 'MiB' in val: used_bytes = float(val.replace('MiB','')) * 1024**2
                                                elif 'KiB' in val: used_bytes = float(val.replace('KiB','')) * 1024
                                                else: used_bytes = float(val.replace('B',''))
                                            except: pass
                                            
                                        elif line.startswith('Free (estimated):'):
                                            # This is logical free space (considering RAID level)
                                            try:
                                                # Format: Free (estimated):     1.80TiB  (Min: 1.80TiB)
                                                val = line.split(':')[1].split('(')[0].strip()
                                                if 'TiB' in val: free_estimated_bytes = float(val.replace('TiB','')) * 1024**4
                                                elif 'GiB' in val: free_estimated_bytes = float(val.replace('GiB','')) * 1024**3
                                                elif 'MiB' in val: free_estimated_bytes = float(val.replace('MiB','')) * 1024**2
                                                elif 'KiB' in val: free_estimated_bytes = float(val.replace('KiB','')) * 1024
                                                else: free_estimated_bytes = float(val.replace('B',''))
                                            except: pass
                                            
                                        elif 'Data,' in line:
                                            if 'RAID1' in line:
                                                pool['raid_level'] = 'RAID1'
                                            elif 'RAID0' in line:
                                                pool['raid_level'] = 'RAID0'
                                            elif 'RAID10' in line:
                                                pool['raid_level'] = 'RAID10'
                                            else:
                                                pool['raid_level'] = 'Single'
                                    
                                    pool['raid_level'] = 'Single'
                                    
                                    # Fallback to df if no valid bytes parsed
                                    if used_bytes == 0 and free_estimated_bytes == 0:
                                        try:
                                            df_res = subprocess.run(['df', '-B1', pool.get('mount_point', '')], capture_output=True, text=True)
                                            if df_res.returncode == 0:
                                                # /dev/sda1 1000 500 500 50% /mnt
                                                parts = df_res.stdout.splitlines()[1].split()
                                                if len(parts) >= 3:
                                                    total_bytes = float(parts[1])
                                                    used_bytes = float(parts[2])
                                        except:
                                            pass

                                    # Calculate logic total
                                    total_bytes = used_bytes + free_estimated_bytes
                                        
                                    # Convert back to human readable
                                    def bytes_to_human(n):
                                        for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
                                            if n < 1024: return f"{n:.2f}{unit}"
                                            n /= 1024
                                        return f"{n:.2f}PiB"
                                            
                                    pool['used_size'] = bytes_to_human(used_bytes)
                                    pool['total_size'] = bytes_to_human(total_bytes)

                            except:
                                # Fallback to df if btrfs usage parsing fails
                                try:
                                    if 'mount_point' in pool:
                                        df_res = subprocess.run(['df', '-h', pool['mount_point']], capture_output=True, text=True)
                                        if df_res.returncode == 0:
                                            # Filesystem      Size  Used Avail Use% Mounted on
                                            # /dev/sda1       100G   10G   90G  10% /mnt/pool
                                            lines = df_res.stdout.strip().split('\n')
                                            if len(lines) >= 2:
                                                parts = lines[1].split()
                                                if len(parts) >= 4:
                                                    pool['total_size'] = parts[1]
                                                    pool['used_size'] = parts[2]
                                except:
                                    pass
            else:
                # Mock data for development
                pools = [
                    {
                        'id': 'mock-pool-1',
                        'name': 'storage-pool',
                        'uuid': 'abc123-def456-ghi789',
                        'devices': ['/dev/sdb', '/dev/sdc'],
                        'total_size': '4.0 TiB',
                        'used_size': '1.2 TiB',
                        'raid_level': 'RAID1'
                    }
                ]
        
        except Exception as e:
            print(f"Error getting pools: {e}")
        
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
                    subprocess.run(['sudo', 'umount', mount_point], timeout=5)
                    
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
                result = subprocess.run(
                    ['sudo', 'btrfs', 'subvolume', 'list', mount_point],
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
            subprocess.Popen(['sudo', 'btrfs', 'balance', 'start', mount_point])
            
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
    
    # Add base mount point
    paths.append({'name': 'Default Storage Root', 'path': '/mnt/alvaos'})
    
    # Add Pools
    pools = load_pools_state()
    for pid, pool in pools.items():
        if 'mount_point' in pool:
            paths.append({'name': f"Pool: {pool['name']}", 'path': pool['mount_point']})
            
            # Dynamic Subvolume Lookup
            # Instead of looking for a non-existent cache file, we list subvolumes directly
            if platform.system() == 'Linux':
                try:
                    result = subprocess.run(
                        ['sudo', 'btrfs', 'subvolume', 'list', pool['mount_point']],
                        capture_output=True, text=True, timeout=3
                    )
                    if result.returncode == 0:
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
        
        try:
            share_id = f'share-{len(load_shares_state())}'
            
            if platform.system() == 'Linux':
                if protocol == 'nfs':
                    # Configure NFS export
                    export_data = f'# AlvaOS Share: {share_name}\n{share_path} {allowed_hosts}({"ro" if read_only else "rw"},sync,no_subtree_check)\n'
                    
                    # Use tee -a with sudo to append to /etc/exports
                    subprocess.run(f"echo '{export_data}' | sudo tee -a /etc/exports", shell=True, check=True)
                    
                    # Reload NFS exports
                    res, err = run_sudo_command(['sudo', 'exportfs', '-ra'])
                    if err: return jsonify({'error': f'Failed to reload NFS: {err}'}), 500
                    
                elif protocol == 'smb':
                    # Configure Samba share
                    smb_config = f'\n# AlvaOS Share: {share_name}\n[{share_name}]\n    path = {share_path}\n    browseable = yes\n    read only = {"yes" if read_only else "no"}\n    guest ok = {"yes" if guest_access else "no"}\n    create mask = 0644\n    directory mask = 0755\n'
                    
                    # Ensure global guest mapping exists if guest access is requested
                    if guest_access:
                         try:
                             with open('/etc/samba/smb.conf', 'r') as f:
                                 conf_content = f.read()
                             if 'map to guest = Bad User' not in conf_content:
                                 # Inject into [global]
                                 # Simple sed replacement or via python
                                 subprocess.run(["sudo", "sed", "-i", "/\\[global\\]/a \\   map to guest = Bad User", "/etc/samba/smb.conf"])
                         except:
                             pass
                    
                    # Use tee -a with sudo to append to /etc/samba/smb.conf
                    subprocess.run(f"echo '{smb_config}' | sudo tee -a /etc/samba/smb.conf", shell=True, check=True)
                    
                    # Restart Samba
                    res, err = run_sudo_command(['sudo', 'systemctl', 'restart', 'smbd'])
                    if err: return jsonify({'error': f'Failed to restart Samba: {err}'}), 500
            
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
                'created_at': datetime.now().isoformat(),
                'status': 'active'
            }
            save_shares_state(shares_state)
            
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
                        process = subprocess.Popen(['sudo', 'tee', '/etc/exports'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                        process = subprocess.Popen(['sudo', 'tee', '/etc/samba/smb.conf'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        process.communicate(input=content)
                        
                        # Restart Samba
                        run_sudo_command(['sudo', 'systemctl', 'restart', 'smbd'])
                    except Exception as e:
                        print(f"Error removing SMB share: {e}")
            
            # Remove from state
            del shares_state[share_id]
            save_shares_state(shares_state)
            
            return jsonify({'success': True, 'message': f'Share "{share_name}" deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete share: {str(e)}'}), 500

    return jsonify({
        'status': 'healthy',
        'version': VERSION,
    })

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

