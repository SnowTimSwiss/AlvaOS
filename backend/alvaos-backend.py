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
        'version': '0.2.0'
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
        'version': '0.2.0'
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
        'version': '0.2.0',
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
                ['lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL'],
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
                        
                        # Get SMART status if available
                        smart_status = 'unknown'
                        try:
                            smart_result = subprocess.run(
                                ['sudo', 'smartctl', '-H', f'/dev/{device["name"]}'],
                                capture_output=True, text=True, timeout=3
                            )
                            if 'PASSED' in smart_result.stdout:
                                smart_status = 'healthy'
                            elif 'FAILED' in smart_result.stdout:
                                smart_status = 'failed'
                        except:
                            pass
                        
                        disk_info = {
                            'name': device['name'],
                            'path': f'/dev/{device["name"]}',
                            'size': device.get('size', 'Unknown'),
                            'model': device.get('model', 'Unknown').strip() if device.get('model') else 'Unknown',
                            'serial': device.get('serial', 'N/A'),
                            'fstype': device.get('fstype', 'none'),
                            'mountpoint': device.get('mountpoint', None),
                            'is_system_disk': is_system_disk,
                            'smart_status': smart_status,
                            'partitions': []
                        }
                        
                        # Add partition information
                        for child in children:
                            partition = {
                                'name': child['name'],
                                'size': child.get('size', 'Unknown'),
                                'fstype': child.get('fstype', 'none'),
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
                    'smart_status': 'healthy',
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
                    'smart_status': 'healthy',
                    'partitions': []
                }
            ]
    
    except Exception as e:
        print(f"Error getting disk info: {e}")
        return jsonify({'error': str(e)}), 500
    
    return jsonify({'disks': disks})

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
                        if line.startswith('Label:'):
                            if current_pool:
                                pools.append(current_pool)
                            
                            # Parse label and UUID
                            parts = line.split()
                            label = 'none'
                            uuid = ''
                            
                            for i, part in enumerate(parts):
                                if part == 'Label:':
                                    label = parts[i+1].strip("'\"") if i+1 < len(parts) else 'none'
                                elif part == 'uuid:':
                                    uuid = parts[i+1] if i+1 < len(parts) else ''
                            
                            current_pool = {
                                'id': uuid,
                                'name': label,
                                'uuid': uuid,
                                'devices': [],
                                'total_size': 0,
                                'used_size': 0,
                                'raid_level': 'unknown'
                            }
                        
                        # Device entry
                        elif line.startswith('devid') and current_pool:
                            # Parse: devid 1 size 100.00GiB used 10.00GiB path /dev/sdb
                            parts = line.split()
                            device_info = {}
                            for i, part in enumerate(parts):
                                if part == 'size':
                                    device_info['size'] = parts[i+1] if i+1 < len(parts) else '0'
                                elif part == 'path':
                                    device_info['path'] = parts[i+1] if i+1 < len(parts) else ''
                            
                            if device_info.get('path'):
                                current_pool['devices'].append(device_info['path'])
                    
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
                                    # Parse usage output
                                    for line in usage_result.stdout.split('\n'):
                                        if 'Device size:' in line:
                                            pool['total_size'] = line.split(':')[1].strip()
                                        elif 'Used:' in line:
                                            pool['used_size'] = line.split(':')[1].strip()
                                        elif 'Data,' in line:
                                            if 'RAID1' in line:
                                                pool['raid_level'] = 'RAID1'
                                            elif 'RAID0' in line:
                                                pool['raid_level'] = 'RAID0'
                                            elif 'RAID10' in line:
                                                pool['raid_level'] = 'RAID10'
                                            else:
                                                pool['raid_level'] = 'Single'
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
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode != 0:
                    return jsonify({'error': f'Failed to create pool: {result.stderr}'}), 500
                
                # Create mount point
                mount_point = f'/mnt/alvaos/{pool_name}'
                os.makedirs(mount_point, exist_ok=True)
                
                # Mount the pool
                mount_result = subprocess.run(
                    ['sudo', 'mount', devices[0], mount_point],
                    capture_output=True, text=True, timeout=5
                )
                
                if mount_result.returncode != 0:
                    return jsonify({'error': f'Pool created but failed to mount: {mount_result.stderr}'}), 500
                
                # Save pool state
                pools_state = load_pools_state()
                pool_id = f'pool-{len(pools_state)}'
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
                # Unmount the pool
                if mount_point:
                    subprocess.run(['sudo', 'umount', mount_point], timeout=5)
                    
                    # Remove mount point
                    try:
                        os.rmdir(mount_point)
                    except:
                        pass
            
            # Remove from state
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
                
                result = subprocess.run(
                    ['sudo', 'btrfs', 'subvolume', 'create', subvol_path],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode != 0:
                    return jsonify({'error': f'Failed to create subvolume: {result.stderr}'}), 500
                
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
                
                result = subprocess.run(
                    ['sudo', 'btrfs', 'subvolume', 'delete', subvol_path],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode != 0:
                    return jsonify({'error': f'Failed to delete subvolume: {result.stderr}'}), 500
                
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
                    export_line = f'{share_path} {allowed_hosts}({"ro" if read_only else "rw"},sync,no_subtree_check)\n'
                    
                    # Append to /etc/exports
                    with open('/etc/exports', 'a') as f:
                        f.write(f'# AlvaOS Share: {share_name}\n')
                        f.write(export_line)
                    
                    # Reload NFS exports
                    subprocess.run(['sudo', 'exportfs', '-ra'], timeout=5)
                    
                elif protocol == 'smb':
                    # Configure Samba share
                    smb_config = f'''
[{share_name}]
    path = {share_path}
    browseable = yes
    read only = {"yes" if read_only else "no"}
    guest ok = {"yes" if guest_access else "no"}
    create mask = 0644
    directory mask = 0755
'''
                    
                    # Append to /etc/samba/smb.conf
                    with open('/etc/samba/smb.conf', 'a') as f:
                        f.write(f'\n# AlvaOS Share: {share_name}\n')
                        f.write(smb_config)
                    
                    # Restart Samba
                    subprocess.run(['sudo', 'systemctl', 'restart', 'smbd'], timeout=10)
            
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
                    # Remove from /etc/exports
                    try:
                        with open('/etc/exports', 'r') as f:
                            lines = f.readlines()
                        
                        # Filter out the share
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
                        
                        with open('/etc/exports', 'w') as f:
                            f.writelines(new_lines)
                        
                        # Reload NFS exports
                        subprocess.run(['sudo', 'exportfs', '-ra'], timeout=5)
                    except Exception as e:
                        print(f"Error removing NFS export: {e}")
                
                elif protocol == 'smb':
                    # Remove from /etc/samba/smb.conf
                    try:
                        with open('/etc/samba/smb.conf', 'r') as f:
                            content = f.read()
                        
                        # Find and remove the share section
                        import re
                        pattern = rf'# AlvaOS Share: {share_name}\n\[{share_name}\].*?(?=\n\[|\n# AlvaOS Share:|\Z)'
                        content = re.sub(pattern, '', content, flags=re.DOTALL)
                        
                        with open('/etc/samba/smb.conf', 'w') as f:
                            f.write(content)
                        
                        # Restart Samba
                        subprocess.run(['sudo', 'systemctl', 'restart', 'smbd'], timeout=10)
                    except Exception as e:
                        print(f"Error removing SMB share: {e}")
            
            # Remove from state
            del shares_state[share_id]
            save_shares_state(shares_state)
            
            return jsonify({'success': True, 'message': f'Share "{share_name}" deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete share: {str(e)}'}), 500

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '0.2.0',
        'setup_complete': is_setup_complete()
    })

if __name__ == '__main__':
    print("Starting AlvaOS Backend v0.2.0...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    
    # Ensure directories exist
    ensure_directories()
    
    if not is_setup_complete():
        print("\n⚠️  SETUP REQUIRED: Access the Web UI to complete initial setup")
    
    app.run(host='0.0.0.0', port=8080, debug=False)

