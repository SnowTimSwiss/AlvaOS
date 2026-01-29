#!/usr/bin/env python3
"""
AlvaOS Backend v0.1
Simple REST API for system information
"""

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import psutil
import platform
import socket
import os
from datetime import datetime

app = Flask(__name__, static_folder='/opt/alvaos/webui', static_url_path='')
CORS(app)

@app.route('/')
def index():
    """Serve the Web UI"""
    return send_from_directory('/opt/alvaos/webui', 'index.html')

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
    except:
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
    return jsonify({'status': 'healthy', 'version': '0.1.0'})

if __name__ == '__main__':
    print("Starting AlvaOS Backend v0.1...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    app.run(host='0.0.0.0', port=8080, debug=False)
