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

# Cleanup function to ensure virtual filesystems are unmounted
cleanup() {
    log "Cleaning up..."
    umount "${ROOTFS_DIR}/sys" 2>/dev/null || true
    umount "${ROOTFS_DIR}/proc" 2>/dev/null || true
    umount "${ROOTFS_DIR}/dev/pts" 2>/dev/null || true
    umount "${ROOTFS_DIR}/dev" 2>/dev/null || true
}
trap cleanup EXIT

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
    --include=linux-image-amd64,live-boot,live-boot-initramfs-tools,grub-efi-amd64,grub-pc,systemd,udev,iproute2,iputils-ping,curl,vim-tiny,openssh-server,parted,dosfstools,e2fsprogs \
    bookworm \
    "${ROOTFS_DIR}" \
    http://deb.debian.org/debian

# Configure the rootfs
log "Configuring rootfs..."

# Set hostname
echo "alvaos-installer" > "${ROOTFS_DIR}/etc/hostname"

# Set root password to 'alvaos' (should be changed on first boot)
chroot "${ROOTFS_DIR}" bash -c "echo 'root:alvaos' | chpasswd"

# Add AlvaOS components to installer
log "Adding AlvaOS components..."
mkdir -p "${ROOTFS_DIR}/opt/alvaos"
mkdir -p "${ROOTFS_DIR}/opt/alvaos/backend"
mkdir -p "${ROOTFS_DIR}/opt/alvaos/webui"

# Copy installation script
if [ -f "${SCRIPT_DIR}/install-system.sh" ]; then
    cp "${SCRIPT_DIR}/install-system.sh" "${ROOTFS_DIR}/opt/alvaos/install.sh"
    chmod +x "${ROOTFS_DIR}/opt/alvaos/install.sh"
    log "✓ Installation script added"
else
    warn "install-system.sh not found, using placeholder"
    cat > "${ROOTFS_DIR}/opt/alvaos/install.sh" << 'PLACEHOLDER_EOF'
#!/bin/bash
echo "AlvaOS Installer v0.1"
echo "Real installer script not found!"
echo "Please check the build."
PLACEHOLDER_EOF
    chmod +x "${ROOTFS_DIR}/opt/alvaos/install.sh"
fi

# Copy backend
BACKEND_SRC="${SCRIPT_DIR}/../backend/alvaos-backend.py"
if [ -f "$BACKEND_SRC" ]; then
    cp "$BACKEND_SRC" "${ROOTFS_DIR}/opt/alvaos/backend/"
    log "✓ Backend added"
else
    warn "Backend not found at $BACKEND_SRC"
fi

# Copy frontend
FRONTEND_SRC="${SCRIPT_DIR}/../frontend"
if [ -d "$FRONTEND_SRC" ]; then
    cp "$FRONTEND_SRC"/*.html "${ROOTFS_DIR}/opt/alvaos/webui/" 2>/dev/null || true
    cp "$FRONTEND_SRC"/*.css "${ROOTFS_DIR}/opt/alvaos/webui/" 2>/dev/null || true
    cp "$FRONTEND_SRC"/*.js "${ROOTFS_DIR}/opt/alvaos/webui/" 2>/dev/null || true
    log "✓ Frontend added"
else
    warn "Frontend not found at $FRONTEND_SRC"
fi

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

# Mount virtual filesystems for chroot operations (required for initramfs generation)
log "Mounting virtual filesystems in chroot..."
mkdir -p "${ROOTFS_DIR}/dev/pts"
mount --bind /dev "${ROOTFS_DIR}/dev"
mount --bind /dev/pts "${ROOTFS_DIR}/dev/pts"
mount -t proc proc "${ROOTFS_DIR}/proc"
mount -t sysfs sysfs "${ROOTFS_DIR}/sys"

# Create live-boot configuration
log "Configuring live-boot..."
mkdir -p "${ROOTFS_DIR}/etc/live/boot.conf.d"
cat > "${ROOTFS_DIR}/etc/live/boot.conf.d/alvaos.conf" << 'EOF'
LIVE_MEDIA_PATH=/live
EOF

# Regenerate initramfs with live-boot support (clean rebuild)
log "Regenerating initramfs with live-boot support..."
chroot "${ROOTFS_DIR}" update-initramfs -c -k all

# Unmount virtual filesystems before creating squashfs
log "Unmounting virtual filesystems..."
umount "${ROOTFS_DIR}/sys" || true
umount "${ROOTFS_DIR}/proc" || true
umount "${ROOTFS_DIR}/dev/pts" || true
umount "${ROOTFS_DIR}/dev" || true

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
    linux /live/vmlinuz boot=live quiet splash nomodeset
    initrd /live/initrd
}

menuentry "AlvaOS Installer (safe mode)" {
    linux /live/vmlinuz boot=live single nomodeset
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
