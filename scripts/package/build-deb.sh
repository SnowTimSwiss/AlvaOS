#!/bin/bash
set -euo pipefail

# AlvaOS .deb Package Builder
# Builds the alvaos-system package for distribution

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build/package"
VERSION="${ALVAOS_VERSION:-0.1.0}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[Package]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log "Building AlvaOS system package v${VERSION}"

# Clean previous build
if [ -d "${BUILD_DIR}" ]; then
    log "Cleaning previous build..."
    rm -rf "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}"

# Create package directory structure
PKG_DIR="${BUILD_DIR}/alvaos-system_${VERSION}_amd64"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/opt/alvaos/bin"
mkdir -p "${PKG_DIR}/opt/alvaos/webui"
mkdir -p "${PKG_DIR}/opt/alvaos/scripts"
mkdir -p "${PKG_DIR}/etc/alvaos"
mkdir -p "${PKG_DIR}/etc/systemd/system"
mkdir -p "${PKG_DIR}/var/lib/alvaos"

log "Building backend binary..."
# TODO: Compile Go backend when it exists
# For now, create a placeholder
cat > "${PKG_DIR}/opt/alvaos/bin/alvaos-backend" << 'EOF'
#!/bin/bash
echo "AlvaOS Backend v${VERSION}"
echo "API server placeholder - to be implemented"
EOF
chmod +x "${PKG_DIR}/opt/alvaos/bin/alvaos-backend"

log "Building frontend..."
# TODO: Build frontend when it exists
# For now, create a placeholder
mkdir -p "${PKG_DIR}/opt/alvaos/webui"
cat > "${PKG_DIR}/opt/alvaos/webui/index.html" << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>AlvaOS</title>
    <meta charset="UTF-8">
</head>
<body>
    <h1>AlvaOS Web UI</h1>
    <p>Placeholder - to be implemented</p>
</body>
</html>
EOF

log "Copying scripts..."
# Copy post-install scripts (when they exist)
# For now, create placeholders
cat > "${PKG_DIR}/opt/alvaos/scripts/setup.sh" << 'EOF'
#!/bin/bash
# AlvaOS post-install setup script
echo "Setting up AlvaOS..."
EOF
chmod +x "${PKG_DIR}/opt/alvaos/scripts/setup.sh"

log "Creating configuration files..."
cat > "${PKG_DIR}/etc/alvaos/config.yaml" << 'EOF'
# AlvaOS Configuration
version: "${VERSION}"
api:
  host: 0.0.0.0
  port: 8080
storage:
  base_path: /srv
logging:
  level: info
  path: /var/log/alvaos
EOF

# Create version file
cat > "${PKG_DIR}/etc/alvaos/version.json" << EOF
{
  "alvaos_version": "${VERSION}",
  "build_date": "$(date -Iseconds)",
  "update_channel": "stable"
}
EOF

log "Creating systemd unit files..."
cat > "${PKG_DIR}/etc/systemd/system/alvaos-backend.service" << 'EOF'
[Unit]
Description=AlvaOS Backend API Server
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
ExecStart=/opt/alvaos/bin/alvaos-backend
Restart=always
RestartSec=10
User=root
Environment="ALVAOS_CONFIG=/etc/alvaos/config.yaml"

[Install]
WantedBy=multi-user.target
EOF

cat > "${PKG_DIR}/etc/systemd/system/alvaos-ui.service" << 'EOF'
[Unit]
Description=AlvaOS Web UI
After=network.target alvaos-backend.service
Requires=alvaos-backend.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m http.server 80 --directory /opt/alvaos/webui
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

log "Creating package control file..."
cat > "${PKG_DIR}/DEBIAN/control" << EOF
Package: alvaos-system
Version: ${VERSION}
Architecture: amd64
Maintainer: AlvaOS Team <dev@alvaos.org>
Depends: docker.io, docker-compose, btrfs-progs, systemd, python3
Recommends: wireguard, smartmontools
Section: admin
Priority: optional
Homepage: https://github.com/SnowTimSwiss/AlvaOS
Description: AlvaOS NAS operating system
 Ultra-stable, lightweight NAS operating system based on Debian Stable.
 .
 Features:
  - Btrfs storage pools with easy expansion
  - Docker container management
  - Buddy Backup (NAS-to-NAS encrypted backups)
  - Clean web-based management UI
  - API-first design
EOF

log "Creating post-install script..."
cat > "${PKG_DIR}/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e

echo "Configuring AlvaOS..."

# Reload systemd
systemctl daemon-reload

# Enable services (but don't start yet on first install)
systemctl enable alvaos-backend.service
systemctl enable alvaos-ui.service

# Create necessary directories
mkdir -p /var/log/alvaos
mkdir -p /srv/storage
mkdir -p /var/lib/alvaos/db

# Set permissions
chown -R root:root /opt/alvaos
chmod 755 /opt/alvaos/bin/alvaos-backend
chmod 755 /opt/alvaos/scripts/*.sh

echo "AlvaOS system package installed successfully!"
echo "To start services: sudo systemctl start alvaos-backend alvaos-ui"

exit 0
EOF
chmod +x "${PKG_DIR}/DEBIAN/postinst"

log "Creating pre-remove script..."
cat > "${PKG_DIR}/DEBIAN/prerm" << 'EOF'
#!/bin/bash
set -e

echo "Stopping AlvaOS services..."
systemctl stop alvaos-backend.service || true
systemctl stop alvaos-ui.service || true

exit 0
EOF
chmod +x "${PKG_DIR}/DEBIAN/prerm"

log "Building .deb package..."
dpkg-deb --build "${PKG_DIR}"

DEB_FILE="${BUILD_DIR}/alvaos-system_${VERSION}_amd64.deb"
log "Package created: ${DEB_FILE}"
log "Package size: $(du -h "${DEB_FILE}" | cut -f1)"

log "Generating SHA256 checksum..."
(cd "${BUILD_DIR}" && sha256sum "alvaos-system_${VERSION}_amd64.deb" > checksums.txt)

log "╔═══════════════════════════════════════╗"
log "║   Package build complete!             ║"
log "╚═══════════════════════════════════════╝"
log ""
log "To install locally:"
log "  sudo apt install ${DEB_FILE}"
log ""
log "To upload to GitHub Release:"
log "  gh release upload v${VERSION} ${DEB_FILE} ${BUILD_DIR}/checksums.txt"
