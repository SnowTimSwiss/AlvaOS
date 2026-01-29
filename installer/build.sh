#!/bin/bash
set -euo pipefail

# AlvaOS Installer Build Script (live-build edition)
# Uses Debian live-build to create a standard, reliable hybrid ISO.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/live-build-work"
VERSION="${ALVAOS_VERSION:-dev}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[AlvaOS]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

# Check root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (use sudo)"
fi

# Check requirements
if ! command -v lb &> /dev/null; then
    error "live-build is not installed. Please run: sudo apt-get install live-build"
fi

log "Starting AlvaOS Installer Build (live-build) - Version: ${VERSION}"

# Prepare build directory
if [ -d "${BUILD_DIR}" ]; then
    log "Cleaning previous build directory..."
    rm -rf "${BUILD_DIR}"
fi
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

# 1. Initialize Configuration
log "Initializing live-build configuration..."
lb config \
    --distribution bookworm \
    --mode debian \
    --system false \
    --keyring-packages debian-archive-keyring \
    --debian-installer live \
    --mirror-bootstrap "http://deb.debian.org/debian/" \
    --mirror-chroot "http://deb.debian.org/debian/" \
    --mirror-binary "http://deb.debian.org/debian/" \
    --security false \
    --archive-areas "main non-free-firmware" \
    --architectures amd64 \
    --linux-flavours amd64 \
    --source false \
    --binary-images iso-hybrid \
    --bootappend-live "boot=live components hostname=alvaos-installer locales=en_US.UTF-8 keyboard-layouts=us quiet splash" \
    --iso-application "AlvaOS Installer" \
    --iso-publisher "AlvaOS Project" \
    --iso-volume "ALVAOS_INSTALLER" \
    --memtest none

# 2. Add Packages
log "Configuring packages..."
mkdir -p config/package-lists
cat > config/package-lists/alvaos.list.chroot << EOF
live-boot
live-config
live-config-systemd
systemd-sysv
grub-efi-amd64
grub-pc
shim-signed
parted
dosfstools
btrfs-progs
cryptsetup
curl
wget
vim
openssh-server
iproute2
net-tools
efibootmgr
EOF

# 3. Add Custom Files (Installer Script & Assets)
log "Adding AlvaOS specific files..."
mkdir -p config/includes.chroot/opt/alvaos
mkdir -p config/includes.chroot/etc/systemd/system/getty@tty1.service.d

# Copy Installer Script
if [ -f "${SCRIPT_DIR}/install-system.sh" ]; then
    cp "${SCRIPT_DIR}/install-system.sh" config/includes.chroot/opt/alvaos/install.sh
    chmod +x config/includes.chroot/opt/alvaos/install.sh
else
    error "install-system.sh not found!"
fi

# Copy Backend (if present)
BACKEND_SRC="${SCRIPT_DIR}/../backend/alvaos-backend.py"
if [ -f "$BACKEND_SRC" ]; then
    mkdir -p config/includes.chroot/opt/alvaos/backend
    cp "$BACKEND_SRC" config/includes.chroot/opt/alvaos/backend/
fi

# Copy Frontend (if present)
FRONTEND_SRC="${SCRIPT_DIR}/../frontend"
if [ -d "$FRONTEND_SRC" ]; then
    mkdir -p config/includes.chroot/opt/alvaos/webui
    cp "$FRONTEND_SRC"/*.html config/includes.chroot/opt/alvaos/webui/ 2>/dev/null || true
    cp "$FRONTEND_SRC"/*.css config/includes.chroot/opt/alvaos/webui/ 2>/dev/null || true
    cp "$FRONTEND_SRC"/*.js config/includes.chroot/opt/alvaos/webui/ 2>/dev/null || true
fi

# Auto-login configuration
cat > config/includes.chroot/etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I \$TERM
EOF

# Custom MOTD
mkdir -p config/includes.chroot/etc
cat > config/includes.chroot/etc/motd << EOF
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║       █████╗ ██╗    ██╗   ██╗ █████╗  ██████╗ ███████╗   ║
║      ██╔══██╗██║    ██║   ██║██╔══██╗██╔═══██╗██╔════╝   ║
║      ███████║██║    ██║   ██║███████║██║   ██║███████╗   ║
║      ██╔══██║██║    ╚██╗ ██╔╝██╔══██║██║   ██║╚════██║   ║
║      ██║  ██║███████╗╚████╔╝ ██║  ██║╚██████╔╝███████║   ║
║      ╚═╝  ╚═╝╚══════╝ ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝

Welcome to the AlvaOS Installer!

Run installation:
  /opt/alvaos/install.sh

EOF

# 4. Build ISO
log "Building ISO..."
lb build

# Move artifact
ARTIFACT_NAME="alvaos-installer-${VERSION}.iso"
if [ -f "live-image-amd64.hybrid.iso" ]; then
    mv "live-image-amd64.hybrid.iso" "${SCRIPT_DIR}/build/${ARTIFACT_NAME}"
    log "✅ Build Success! ISO available at: ${SCRIPT_DIR}/build/${ARTIFACT_NAME}"
else
    error "Build failed - no ISO generated."
fi
