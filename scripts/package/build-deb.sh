#!/bin/bash
set -euo pipefail

# AlvaOS .deb Package Builder
# Builds the alvaos-system package for distribution

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build/package"
VERSION=$(cat "${REPO_ROOT}/VERSION")

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

log "Copying backend and frontend..."
# Copy backend
cp "${REPO_ROOT}/backend/alvaos-backend.py" "${PKG_DIR}/opt/alvaos/bin/"
chmod +x "${PKG_DIR}/opt/alvaos/bin/alvaos-backend.py"
cp "${REPO_ROOT}/backend/update_manager.py" "${PKG_DIR}/opt/alvaos/bin/"
cp "${REPO_ROOT}/backend/docker_manager.py" "${PKG_DIR}/opt/alvaos/bin/"
cp "${REPO_ROOT}/backend/app_store.py" "${PKG_DIR}/opt/alvaos/bin/"

# Copy frontend
cp -r "${REPO_ROOT}/frontend/"* "${PKG_DIR}/opt/alvaos/webui/"

# Copy app catalog
mkdir -p "${PKG_DIR}/opt/alvaos/apps/icons"
cp "${REPO_ROOT}/apps/catalog.json" "${PKG_DIR}/opt/alvaos/apps/"

# Copy VERSION
cp "${REPO_ROOT}/VERSION" "${PKG_DIR}/etc/alvaos/VERSION"

# Copy update scripts
cp "${REPO_ROOT}/scripts/update_checker.sh" "${PKG_DIR}/opt/alvaos/scripts/"
cp "${REPO_ROOT}/scripts/apply_update.sh" "${PKG_DIR}/opt/alvaos/scripts/"
chmod +x "${PKG_DIR}/opt/alvaos/scripts/update_checker.sh" "${PKG_DIR}/opt/alvaos/scripts/apply_update.sh"

# Copy update checker unit
cp "${REPO_ROOT}/scripts/alvaos-update-checker.service" "${PKG_DIR}/etc/systemd/system/"

log "Creating configuration files..."
# Create version file
cat > "${PKG_DIR}/etc/alvaos/version.json" << EOF
{
  "alvaos_version": "${VERSION}",
  "build_date": "$(date -Iseconds)",
  "update_channel": "stable"
}
EOF

log "Creating systemd unit file..."
cat > "${PKG_DIR}/etc/systemd/system/alvaos.service" << 'EOF'
[Unit]
Description=AlvaOS Web Interface
After=network.target network-manager.service

[Service]
Type=simple
User=alvaos
Group=alvaos
WorkingDirectory=/opt/alvaos/bin
ExecStart=/usr/bin/python3 /opt/alvaos/bin/alvaos-backend.py
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

log "Creating sudoers rules..."
mkdir -p "${PKG_DIR}/etc/sudoers.d"
cat > "${PKG_DIR}/etc/sudoers.d/alvaos" << 'SUDOERS_EOF'
# AlvaOS Permissions - Comprehensive List
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/useradd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/userdel
alvaos ALL=(ALL) NOPASSWD: /usr/bin/smbpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/groupadd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/groupdel
alvaos ALL=(ALL) NOPASSWD: /usr/bin/gpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/bin/chgrp
alvaos ALL=(ALL) NOPASSWD: /usr/bin/chmod
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart alvaos.service
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl start alvaos.service
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop alvaos.service
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl status alvaos.service
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemd-run
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart ssh
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl status docker.service
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart smbd
alvaos ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload nfs-kernel-server
alvaos ALL=(ALL) NOPASSWD: /usr/bin/apt
alvaos ALL=(ALL) NOPASSWD: /usr/bin/apt-get
alvaos ALL=(ALL) NOPASSWD: /usr/bin/dpkg
alvaos ALL=(ALL) NOPASSWD: /usr/bin/dpkg-deb
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/smartctl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/lsblk
alvaos ALL=(ALL) NOPASSWD: /usr/bin/btrfs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/wipefs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/partprobe
alvaos ALL=(ALL) NOPASSWD: /usr/bin/umount
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mount
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mkdir
alvaos ALL=(ALL) NOPASSWD: /usr/bin/rmdir
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/mkfs.btrfs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/mkfs.ext4
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/blkid
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat
alvaos ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/timedatectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/journalctl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tail
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/reboot
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/poweroff
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/hosts
alvaos ALL=(ALL) NOPASSWD: /usr/bin/sed
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/exportfs
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mountpoint
alvaos ALL=(ALL) NOPASSWD: /usr/bin/docker
alvaos ALL=(ALL) NOPASSWD: /usr/bin/docker-compose
SUDOERS_EOF

log "Creating package control file..."
cat > "${PKG_DIR}/DEBIAN/control" << EOF
Package: alvaos-system
Version: ${VERSION}
Architecture: amd64
Maintainer: AlvaOS Team <dev@alvaos.org>
Depends: python3, python3-flask, python3-flask-cors, python3-psutil, python3-requests, python3-packaging, python3-yaml, docker.io, docker-compose, btrfs-progs, systemd, smartmontools, nfs-kernel-server, samba, network-manager
Section: admin
Priority: optional
Homepage: https://github.com/SnowTimSwiss/AlvaOS
Description: AlvaOS NAS operating system
 Features:
  - Btrfs storage pools with easy expansion
  - RAID status monitoring and warnings
  - Network shares (NFS/SMB)
  - Docker container management
  - Clean web-based management UI
EOF

log "Creating post-install script..."
cat > "${PKG_DIR}/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e

echo "Configuring AlvaOS..."

# Create alvaos system user if it doesn't exist
if ! id alvaos >/dev/null 2>&1; then
    useradd -r -s /bin/bash -d /opt/alvaos -M alvaos
fi

# Create necessary directories
mkdir -p /var/log/alvaos
mkdir -p /var/lib/alvaos
mkdir -p /etc/alvaos
mkdir -p /mnt/alvaos

# Set permissions
chown -R alvaos:alvaos /opt/alvaos
chown -R alvaos:alvaos /var/lib/alvaos
chown -R alvaos:alvaos /var/log/alvaos
chown -R alvaos:alvaos /etc/alvaos
chmod 440 /etc/sudoers.d/alvaos

# Reload systemd
systemctl daemon-reload

# Enable services
systemctl enable alvaos.service
systemctl enable alvaos-update-checker.service

echo "AlvaOS system package installed successfully!"
echo "To start services: sudo systemctl start alvaos"

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
