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
    --debian-installer none \
    --mirror-bootstrap "http://deb.debian.org/debian" \
    --mirror-chroot "http://deb.debian.org/debian" \
    --mirror-binary "http://deb.debian.org/debian" \
    --apt-indices true \
    --security false \
    --archive-areas "main contrib non-free non-free-firmware" \
    --architectures amd64 \
    --linux-flavours none \
    --linux-packages none \
    --source false \
    --binary-images iso-hybrid \
    --bootappend-live "boot=live components hostname=alvaos-installer console=ttyS0,115200n8 console=tty0 locales=en_US.UTF-8 keyboard-layouts=us quiet nosplash nomodeset" \
    --iso-application "AlvaOS Server Installer" \
    --iso-publisher "AlvaOS Project" \
    --iso-volume "ALVAOS_SERVER_INSTALLER" \
    --memtest none

# Fix for Debian Bookworm Security Repository (bookworm/updates -> bookworm-security)
mkdir -p config/archives
echo "deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware" > config/archives/security.list.chroot
cp config/archives/security.list.chroot config/archives/security.list.binary

# 2. Add Packages
log "Configuring packages..."
mkdir -p config/package-lists
cat > config/package-lists/alvaos.list.chroot << EOF
# Live System
live-boot
live-config
live-config-systemd

# Kernel
linux-image-amd64

# Bootloader
grub-efi-amd64
grub-pc
grub2-common
shim-signed
efibootmgr

# Partitionierung
parted
gdisk
dosfstools
btrfs-progs
xfsprogs
lvm2
cryptsetup
e2fsprogs

# Netzwerk
curl
wget
openssh-server
iproute2
net-tools

# Basis-Tools
vim-tiny
less
sudo
ca-certificates
locales
console-setup
kbd
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

# Create installer user and auto-login
mkdir -p config/includes.chroot/usr/lib/live/config
cat > config/includes.chroot/usr/lib/live/config/0031-installer-user << 'EOF'
#!/bin/sh
# Create installer user
useradd -m -s /bin/bash installer
echo "installer:alvaos" | chpasswd
echo "installer ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/installer
chmod 440 /etc/sudoers.d/installer
EOF
chmod +x config/includes.chroot/usr/lib/live/config/0031-installer-user

# Auto-login configuration for installer user
cat > config/includes.chroot/etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin installer --noclear %I \$TERM
EOF

# SSH configuration for remote installation
mkdir -p config/includes.chroot/etc/ssh/sshd_config.d
cat > config/includes.chroot/etc/ssh/sshd_config.d/alvaos.conf << EOF
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding no
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Auto-start SSH
mkdir -p config/includes.chroot/etc/systemd/system/multi-user.target.wants
ln -sf /lib/systemd/system/ssh.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/ssh.service

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

Welcome to the AlvaOS Server Installer!

System is ready for installation.

SSH Access: ssh installer@<ip> (password: alvaos)

Run installation manually:
  sudo /opt/alvaos/install.sh

EOF

# Add installer user's bashrc to auto-start installation
mkdir -p config/includes.chroot/home/installer
cat > config/includes.chroot/home/installer/.bashrc << 'EOF'
# Auto-start installation on first login
if [ -f /opt/alvaos/install.sh ] && [ ! -f /tmp/alvaos-install-started ]; then
    touch /tmp/alvaos-install-started
    echo "Starting AlvaOS installation..."
    sudo /opt/alvaos/install.sh
fi
EOF
chown -R 1000:1000 config/includes.chroot/home/installer

# 4. Build ISO
log "Building ISO..."
lb build

# Move artifact
ARTIFACT_NAME="alvaos-installer-${VERSION}.iso"
if [ -f "live-image-amd64.hybrid.iso" ]; then
    mv "live-image-amd64.hybrid.iso" "${SCRIPT_DIR}/build/${ARTIFACT_NAME}"
    log "✅ Build Success! ISO available at: ${SCRIPT_DIR}/build/${ARTIFACT_NAME}"
    
    # Optional cleanup
    log "Cleaning build cache..."
    lb clean --cache
    
else
    error "Build failed - no ISO generated."
fi