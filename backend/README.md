# AlvaOS Backend

The AlvaOS REST API server (v0.1).

## Current Implementation

**Technology:** Python 3 + Flask

For v0.1, we use Python Flask for rapid prototyping. Future versions may migrate to Go for better performance and single-binary deployment.

## Features (v0.1)

- ✅ System information API (`/api/v1/system/info`)
- ✅ Health check endpoint (`/api/v1/health`)
- ✅ Static file serving (Web UI)
- ✅ CORS enabled for development

## API Endpoints

### GET `/api/v1/system/info`

Returns comprehensive system information:

```json
{
  "version": "0.1.0",
  "timestamp": "2026-01-29T15:00:00",
  "cpu": {
    "cores": 4,
    "threads": 8,
    "usage_percent": 25.5,
    "frequency_mhz": 2400.0
  },
  "memory": {
    "total_gb": 16.0,
    "used_gb": 8.2,
    "available_gb": 7.8,
    "percent": 51.2
  },
  "disk": {
    "total_gb": 500.0,
    "used_gb": 120.5,
    "free_gb": 379.5,
    "percent": 24.1
  },
  "network": {
    "hostname": "alvaos",
    "ip_address": "192.168.1.100"
  },
  "system": {
    "os": "Linux",
    "os_version": "6.1.0-17-amd64",
    "architecture": "x86_64",
    "uptime_hours": 48.5,
    "boot_time": "2026-01-27 18:30:00"
  }
}
```

### GET `/api/v1/health`

Health check endpoint:

```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

## Development

### Prerequisites

- Python 3.9+
- pip

### Installation

```bash
cd backend
pip install -r requirements.txt
```

### Running Locally

```bash
# Quick test
chmod +x test.sh
./test.sh

# Or manually
python3 alvaos-backend.py
```

The backend will start on `http://0.0.0.0:8080`

### Testing

```bash
# Health check
curl http://localhost:8080/api/v1/health

# System info
curl http://localhost:8080/api/v1/system/info | jq

# Web UI
xdg-open http://localhost:8080
```

## Dependencies

- **Flask** - Web framework
- **flask-cors** - CORS support
- **psutil** - System and process utilities

See `requirements.txt` for versions.

## Deployment

The backend is deployed as a systemd service on installed AlvaOS systems:

```ini
[Unit]
Description=AlvaOS Web Interface
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/alvaos/bin
ExecStart=/usr/bin/python3 /opt/alvaos/bin/alvaos-backend.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Future Plans

### v0.2
- Storage management endpoints
- Docker integration
- User authentication

### v1.0
- Migrate to Go for better performance
- Single binary deployment
- Reduced dependencies
