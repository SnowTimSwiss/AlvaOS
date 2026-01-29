#!/bin/bash
set -euo pipefail

# AlvaOS Installer Build Script
# Creates a minimal Debian-based installer image

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
ROOTFS_DIR="${BUILD_DIR}/rootfs"
ISO_DIR="${BUILD_DIR}/iso"
VERSION="${ALVAOS_VERSION:-dev}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[AlvaOS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (use sudo)"
fi

log "Starting AlvaOS installer build (version: ${VERSION})"

# Clean previous build
if [ -d "${BUILD_DIR}" ]; then
    log "Cleaning previous build..."
    rm -rf "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}" "${ROOTFS_DIR}" "${ISO_DIR}"

# Create minimal Debian rootfs
log "Creating minimal Debian rootfs with debootstrap..."
debootstrap \
    --arch=amd64 \
    --variant=minbase \
    --include=linux-image-amd64,grub-pc,systemd,udev,iproute2,iputils-ping,curl,vim-tiny,openssh-server \
    bookworm \
    "${ROOTFS_DIR}" \
    http://deb.debian.org/debian

# Configure the rootfs
log "Configuring rootfs..."

# Set hostname
echo "alvaos-installer" > "${ROOTFS_DIR}/etc/hostname"

# Set root password to 'alvaos' (should be changed on first boot)
chroot "${ROOTFS_DIR}" bash -c "echo 'root:alvaos' | chpasswd"

# Add AlvaOS installer script
log "Adding installer script..."
mkdir -p "${ROOTFS_DIR}/opt/alvaos"
cat > "${ROOTFS_DIR}/opt/alvaos/install.sh" << 'INSTALLER_EOF'
#!/bin/bash
# AlvaOS Installation Script
# This script installs AlvaOS to the target disk

echo "╔═══════════════════════════════════════╗"
echo "║      AlvaOS Installer v${VERSION}     ║"
echo "╔═══════════════════════════════════════╗"
echo ""
echo "This will install AlvaOS to your system."
echo ""
echo "WARNING: This is a placeholder installer."
echo "Full installation logic will be implemented soon."
echo ""
echo "For now, this performs a minimal Debian installation."
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Installation cancelled."
    exit 0
fi

echo "Installation would proceed here..."
echo "Target: Minimal Debian + AlvaOS scripts + Web UI"
INSTALLER_EOF

chmod +x "${ROOTFS_DIR}/opt/alvaos/install.sh"

# Create auto-login for installer
log "Configuring auto-login..."
mkdir -p "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d"
cat > "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d/autologin.conf" << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF

# Create motd
cat > "${ROOTFS_DIR}/etc/motd" << 'EOF'
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║       █████╗ ██╗    ██╗   ██╗ █████╗  ██████╗ ███████╗   ║
║      ██╔══██╗██║    ██║   ██║██╔══██╗██╔═══██╗██╔════╝   ║
║      ███████║██║    ██║   ██║███████║██║   ██║███████╗   ║
║      ██╔══██║██║    ╚██╗ ██╔╝██╔══██║██║   ██║╚════██║   ║
║      ██║  ██║███████╗╚████╔╝ ██║  ██║╚██████╔╝███████║   ║
║      ╚═╝  ╚═╝╚══════╝ ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ║
║                                                           ║
║            Ultra-stable, lightweight NAS OS               ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝

Welcome to the AlvaOS Installer!

To install AlvaOS, run:
  /opt/alvaos/install.sh

For help, visit: https://github.com/SnowTimSwiss/AlvaOS
EOF

# Create squashfs filesystem
log "Creating squashfs filesystem..."
mksquashfs "${ROOTFS_DIR}" "${BUILD_DIR}/filesystem.squashfs" \
    -comp xz \
    -b 1M \
    -Xdict-size 100%

# Prepare ISO structure
log "Preparing ISO structure..."
mkdir -p "${ISO_DIR}/live"
cp "${BUILD_DIR}/filesystem.squashfs" "${ISO_DIR}/live/"
cp "${ROOTFS_DIR}/boot/vmlinuz-"* "${ISO_DIR}/live/vmlinuz"
cp "${ROOTFS_DIR}/boot/initrd.img-"* "${ISO_DIR}/live/initrd"

# Create GRUB config for UEFI and BIOS boot
log "Creating GRUB configuration..."
mkdir -p "${ISO_DIR}/boot/grub"
cat > "${ISO_DIR}/boot/grub/grub.cfg" << 'EOF'
set timeout=5
set default=0

menuentry "AlvaOS Installer" {
    linux /live/vmlinuz boot=live quiet splash
    initrd /live/initrd
}

menuentry "AlvaOS Installer (safe mode)" {
    linux /live/vmlinuz boot=live single
    initrd /live/initrd
}
EOF

# Create ISO
log "Creating bootable ISO image..."
ISO_NAME="alvaos-installer-${VERSION}.iso"
grub-mkrescue -o "${BUILD_DIR}/${ISO_NAME}" "${ISO_DIR}"

log "Build complete! ISO created at: ${BUILD_DIR}/${ISO_NAME}"
log "ISO size: $(du -h "${BUILD_DIR}/${ISO_NAME}" | cut -f1)"

log "╔═══════════════════════════════════════╗"
log "║     Build completed successfully!     ║"
log "╚═══════════════════════════════════════╝"
