#!/bin/bash
set -euo pipefail

# AlvaOS Installer Build Script (live-build, Debian Trixie)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/live-build-work"
VERSION=${ALVAOS_VERSION:-$(cat "${SCRIPT_DIR}/../VERSION")}

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[AlvaOS]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

# Root check
[ "$EUID" -eq 0 ] || error "Run as root (sudo)"

# Dependency check
command -v lb >/dev/null || error "live-build not installed (apt install live-build)"

log "Starting AlvaOS Installer Build - Version: ${VERSION}"

# Clean build dir
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# Check for existing ISO lock
TARGET_ISO="${SCRIPT_DIR}/build/alvaos-installer-${VERSION}.iso"
if [ -f "$TARGET_ISO" ]; then
    log "Removing old ISO..."
    rm -f "$TARGET_ISO" || error "Could not remove old ISO! Is it mounted in a VM/Windows? Please detach it first."
fi

cd "${BUILD_DIR}"

# ------------------------------------------------------------
# 1. live-build config
# ------------------------------------------------------------
log "Configuring live-build..."

lb config \
  --distribution trixie \
  --mode debian \
  --initramfs live-boot \
  --keyring-packages debian-archive-keyring \
  --mirror-bootstrap http://deb.debian.org/debian \
  --mirror-chroot http://deb.debian.org/debian \
  --mirror-binary http://deb.debian.org/debian \
  --security true \
  --archive-areas "main contrib non-free non-free-firmware" \
  --architectures amd64 \
  --linux-flavours amd64 \
  --debian-installer none \
  --binary-images iso-hybrid \
  --bootappend-live "boot=live components hostname=alvaos-installer username=installer locales=en_US.UTF-8 keyboard-layouts=us quiet nomodeset" \
  --iso-application "AlvaOS Server Installer" \
  --iso-publisher "AlvaOS Project" \
  --iso-volume "ALVAOS_SERVER_INSTALLER" \
  --memtest none

# ------------------------------------------------------------
# 2. Packages
# ------------------------------------------------------------
log "Adding packages..."

mkdir -p config/package-lists
cat > config/package-lists/alvaos.list.chroot << 'EOF'
live-boot
live-config
live-config-systemd

linux-image-amd64

grub-efi-amd64-bin
grub-efi-amd64-signed
grub-pc-bin
grub2-common
shim-signed
efibootmgr

parted
gdisk
dosfstools
btrfs-progs
xfsprogs
lvm2
cryptsetup
e2fsprogs

curl
wget
openssh-server
iproute2
net-tools

vim-tiny
less
sudo
ca-certificates
locales
console-setup
kbd
debootstrap
EOF

# ------------------------------------------------------------
# 3. Installer files
# ------------------------------------------------------------
log "Adding AlvaOS files..."

mkdir -p config/includes.chroot/opt/alvaos

[ -f "${SCRIPT_DIR}/install-system.sh" ] || error "install-system.sh missing"
cp "${SCRIPT_DIR}/install-system.sh" config/includes.chroot/opt/alvaos/install.sh
chmod +x config/includes.chroot/opt/alvaos/install.sh

# VERSION file
cp "${SCRIPT_DIR}/../VERSION" config/includes.chroot/opt/alvaos/VERSION

# Backend (optional)
if [ -f "${SCRIPT_DIR}/../backend/alvaos-backend.py" ]; then
  mkdir -p config/includes.chroot/opt/alvaos/backend
  cp "${SCRIPT_DIR}/../backend/"*.py config/includes.chroot/opt/alvaos/backend/
  cp "${SCRIPT_DIR}/../backend/alvaos-priv" config/includes.chroot/opt/alvaos/backend/
fi

# Update signing public key (optional; without it updates are refused)
if [ -f "${SCRIPT_DIR}/../keys/update-signing.pub" ]; then
  mkdir -p config/includes.chroot/opt/alvaos/keys
  cp "${SCRIPT_DIR}/../keys/update-signing.pub" config/includes.chroot/opt/alvaos/keys/
fi

# Apps catalog (optional)
if [ -d "${SCRIPT_DIR}/../apps" ]; then
  mkdir -p config/includes.chroot/opt/alvaos/apps
  cp -r "${SCRIPT_DIR}/../apps/"* config/includes.chroot/opt/alvaos/apps/ 2>/dev/null || true
fi

# Frontend (optional)
if [ -d "${SCRIPT_DIR}/../frontend" ]; then
  mkdir -p config/includes.chroot/opt/alvaos/webui
  cp "${SCRIPT_DIR}/../frontend"/*.{html,css,js} \
     config/includes.chroot/opt/alvaos/webui/ 2>/dev/null || true
fi

# Update scripts (optional)
mkdir -p config/includes.chroot/opt/alvaos/scripts
for script_name in update_checker.sh apply_update.sh setup_sudoers.sh; do
  if [ -f "${SCRIPT_DIR}/../scripts/${script_name}" ]; then
    cp "${SCRIPT_DIR}/../scripts/${script_name}" config/includes.chroot/opt/alvaos/scripts/
    chmod +x "config/includes.chroot/opt/alvaos/scripts/${script_name}"
  fi
done
if [ -f "${SCRIPT_DIR}/../scripts/sudoers.alvaos" ]; then
  cp "${SCRIPT_DIR}/../scripts/sudoers.alvaos" config/includes.chroot/opt/alvaos/scripts/
fi

if [ -f "${SCRIPT_DIR}/../scripts/alvaos-update-checker.service" ]; then
  # Copy to /opt/alvaos/scripts/ so install-system.sh can find and copy it
  mkdir -p config/includes.chroot/opt/alvaos/scripts
  cp "${SCRIPT_DIR}/../scripts/alvaos-update-checker.service" config/includes.chroot/opt/alvaos/scripts/
fi
for unit in scripts/alvaos-update-checker.timer backend/alvaos-watchdog.service backend/alvaos-watchdog.timer; do
  if [ -f "${SCRIPT_DIR}/../${unit}" ]; then
    mkdir -p config/includes.chroot/opt/alvaos/scripts
    cp "${SCRIPT_DIR}/../${unit}" config/includes.chroot/opt/alvaos/scripts/
  fi
done

# ------------------------------------------------------------
# 4. live-config hooks
# ------------------------------------------------------------
log "Configuring live system..."

mkdir -p config/includes.chroot/usr/lib/live/config

# Installer user
cat > config/includes.chroot/usr/lib/live/config/0031-installer-user << 'EOF'
#!/bin/sh
set -e

if ! id installer >/dev/null 2>&1; then
  useradd -m -s /bin/bash installer
  # Generate random temporary password for installer environment only
  RANDOM_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c12)
  echo "installer:${RANDOM_PASS}" | chpasswd
  echo "INSTALLER TEMPORARY PASSWORD: ${RANDOM_PASS}" > /etc/motd.installer
  echo "This password will be cleared after installation." >> /etc/motd.installer
fi

echo "installer ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/installer
chmod 440 /etc/sudoers.d/installer

# Auto-login on tty1
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'AUTOLOGIN_EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin installer --noclear %I $TERM
AUTOLOGIN_EOF
EOF
chmod +x config/includes.chroot/usr/lib/live/config/0031-installer-user

# Autostart installer via profile
cat > config/includes.chroot/usr/lib/live/config/9999-autostart-installer << 'EOF'
#!/bin/sh
# Create profile script to start installer on login
cat > /etc/profile.d/autostart-installer.sh << 'PROFILE_EOF'
if [ "$USER" = "installer" ] && [ -t 0 ] && [ "$(tty)" = "/dev/tty1" ]; then
    # Clear screen
    clear
    # Run installer
    sudo /opt/alvaos/install.sh
fi
PROFILE_EOF
chmod +x /etc/profile.d/autostart-installer.sh
EOF
chmod +x config/includes.chroot/usr/lib/live/config/9999-autostart-installer

# ------------------------------------------------------------
# 5. SSH enable
# ------------------------------------------------------------
mkdir -p config/includes.chroot/etc/systemd/system/ssh.service.d
cat > config/includes.chroot/etc/systemd/system/ssh.service.d/override.conf << 'EOF'
[Install]
WantedBy=multi-user.target
EOF

mkdir -p config/includes.chroot/etc/ssh/sshd_config.d
cat > config/includes.chroot/etc/ssh/sshd_config.d/alvaos.conf << 'EOF'
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
UsePAM yes
X11Forwarding no
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# ------------------------------------------------------------
# 6. MOTD
# ------------------------------------------------------------
cat > config/includes.chroot/etc/motd << 'EOF'
=====================================
   AlvaOS Server Installer (Live)
=====================================

SSH:
  ssh installer@<ip>
  password: alvaos

Local:
  Installer starts automatically
=====================================
EOF

# ------------------------------------------------------------
# 7. Build
# ------------------------------------------------------------
log "Building ISO..."
lb build

mkdir -p "${SCRIPT_DIR}/build"

if [ -f live-image-amd64.hybrid.iso ]; then
  mv live-image-amd64.hybrid.iso \
     "${SCRIPT_DIR}/build/alvaos-installer-${VERSION}.iso"
  log "Build successful"
else
  error "ISO not generated"
fi

lb clean --cache
log "Done."
