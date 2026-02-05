#!/bin/bash
set -e

# AlvaOS Installation Script 0.2.1
# This script installs AlvaOS to the target system

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Cleanup function for error recovery
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "${RED}[ERROR]${NC} Installation failed!"
    fi
    
    # Unmount everything
    log "Cleaning up..."
    umount -l /mnt/sys 2>/dev/null || true
    umount -l /mnt/proc 2>/dev/null || true
    umount -l /mnt/dev/pts 2>/dev/null || true
    umount -l /mnt/dev 2>/dev/null || true
    umount -l /mnt/boot/efi 2>/dev/null || true
    umount -l /mnt 2>/dev/null || true
}

# Set trap for cleanup
trap cleanup EXIT ERR INT TERM

# Logo
cat << 'EOF'
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║       █████╗ ██╗    ██╗   ██╗ █████╗  ██████╗ ███████╗    ║
║      ██╔══██╗██║    ██║   ██║██╔══██╗██╔═══██╗██╔════╝    ║
║      ███████║██║    ██║   ██║███████║██║   ██║███████╗    ║
║      ██╔══██║██║    ╚██╗ ██╔╝██╔══██║██║   ██║╚════██║    ║
║      ██║  ██║███████╗╚████╔╝ ██║  ██║╚██████╔╝███████║    ║
║      ╚═╝  ╚═╝╚══════╝ ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝    ║
║                                                           ║
║            Your homelab journey starts here!              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
EOF
echo ""

log() {
    echo -e "${GREEN}[AlvaOS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    error "Please run as root (use sudo)"
fi

# Check for EFI boot mode
if [ ! -d "/sys/firmware/efi" ]; then
    warn "System is NOT booted in EFI mode!"
    warn "This installer is designed for UEFI systems."
    read -p "Continue anyway? (y/N) " EFI_CONFIRM
    if [[ "$EFI_CONFIRM" != "y" && "$EFI_CONFIRM" != "Y" ]]; then
        exit 1
    fi
fi

# Detect target disk
log "Detecting available disks..."
echo ""
lsblk -d -o NAME,SIZE,TYPE | grep disk
echo ""

read -p "Enter target disk (e.g., sda, vda, nvme0n1): " TARGET_DISK
TARGET_DISK="/dev/${TARGET_DISK}"

if [ ! -b "$TARGET_DISK" ]; then
    error "Disk $TARGET_DISK not found!"
fi

# Confirm installation
echo ""
warn "⚠️  WARNING: This will ERASE ALL DATA on $TARGET_DISK"
echo ""
read -p "Type 'yes' to continue: " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Installation cancelled."
    exit 0
fi

log "Starting installation to $TARGET_DISK..."
sleep 2

# Partition the disk
log "Creating partitions..."
parted -s "$TARGET_DISK" mklabel gpt
parted -s "$TARGET_DISK" mkpart primary fat32 1MiB 512MiB
parted -s "$TARGET_DISK" set 1 esp on
parted -s "$TARGET_DISK" mkpart primary ext4 512MiB 100%

# Sync partitions
partprobe "$TARGET_DISK" || true
sleep 2

# Format partitions
log "Formatting partitions..."
if [[ "$TARGET_DISK" == *"nvme"* ]]; then
    EFI_PART="${TARGET_DISK}p1"
    ROOT_PART="${TARGET_DISK}p2"
else
    EFI_PART="${TARGET_DISK}1"
    ROOT_PART="${TARGET_DISK}2"
fi

mkfs.fat -F32 "$EFI_PART"
mkfs.ext4 -F "$ROOT_PART"

# Mount filesystems
log "Mounting filesystems..."
mount "$ROOT_PART" /mnt
mkdir -p /mnt/boot/efi
mount "$EFI_PART" /mnt/boot/efi

# Install base system
log "Installing Debian base system (this may take a while)..."
debootstrap --arch=amd64 bookworm /mnt http://deb.debian.org/debian

# Configure the new system
log "Configuring system..."

# Set hostname
echo "alvaos" > /mnt/etc/hostname

# Configure hosts
cat > /mnt/etc/hosts << 'HOSTS_EOF'
127.0.0.1   localhost
127.0.1.1   alvaos
::1         localhost ip6-localhost ip6-loopback
HOSTS_EOF

# Configure network (DHCP on all common interfaces)
# We use a more generic approach to avoid issues with predictable interface names
cat > /mnt/etc/network/interfaces << 'NET_EOF'
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

# We let NetworkManager handle all other interfaces automatically
NET_EOF

# Better apt sources for the target system
cat > /mnt/etc/apt/sources.list << 'SOURCES_EOF'
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware
SOURCES_EOF

# Configure fstab
BOOT_UUID=$(blkid -s UUID -o value "$EFI_PART")
ROOT_UUID=$(blkid -s UUID -o value "$ROOT_PART")

cat > /mnt/etc/fstab << FSTAB_EOF
UUID=$ROOT_UUID  /          ext4  defaults  0  1
UUID=$BOOT_UUID  /boot/efi  vfat  defaults  0  2
FSTAB_EOF

# Install kernel and essential packages
log "Installing kernel and packages..."

# Mount virtual filesystems for chroot
mount --rbind /dev /mnt/dev
mount --make-rslave /mnt/dev
mount --rbind /sys /mnt/sys
mount --make-rslave /mnt/sys
mount -t proc proc /mnt/proc

chroot /mnt apt-get update
chroot /mnt apt-get install -y \
    linux-image-amd64 \
    python3 \
    python3-pip \
    python3-flask \
    python3-flask-cors \
    python3-psutil \
    systemd \
    network-manager \
    openssh-server \
    docker.io \
    docker-compose \
    btrfs-progs \
    curl \
    wget \
    vim \
    sudo \
    firmware-linux-free \
    intel-microcode \
    amd64-microcode \
    initramfs-tools \
    systemd-timesyncd \
    iputils-ping \
    net-tools \
    smartmontools \
    nfs-kernel-server \
    samba

# Install GRUB based on boot mode
log "Installing bootloader packages..."
if [ -d /sys/firmware/efi ]; then
    log "UEFI mode detected, installing grub-efi-amd64..."
    DEBIAN_FRONTEND=noninteractive chroot /mnt apt-get install -y grub-efi-amd64
else
    log "Legacy BIOS mode detected, installing grub-pc..."
    DEBIAN_FRONTEND=noninteractive chroot /mnt apt-get install -y grub-pc
fi

# Install GRUB
log "Configuring bootloader..."
if [ -d /sys/firmware/efi ]; then
    chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS --recheck --removable
else
    # Fallback for Legacy BIOS
    warn "System is not in UEFI mode, attempting legacy GRUB install on $TARGET_DISK..."
    chroot /mnt grub-install --target=i386-pc "$TARGET_DISK" || true
fi
# Add nomodeset to installed system for better hardware compatibility
sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="quiet"/GRUB_CMDLINE_LINUX_DEFAULT="quiet nomodeset"/' /mnt/etc/default/grub
chroot /mnt update-grub

# Secure root account with random password
log "Securing root account..."
# Generate a secure random password that nobody will know
# This forces users to go through the Web UI setup wizard
RANDOM_PASS=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
echo "root:${RANDOM_PASS}" | chroot /mnt chpasswd

# Disable root SSH login until setup is complete
log "Configuring SSH security..."
cat > /mnt/etc/ssh/sshd_config.d/00-alvaos-security.conf << 'SSH_EOF'
# AlvaOS Security Configuration
# Root login disabled until Web UI setup is completed
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
SSH_EOF

echo ""
log "╔════════════════════════════════════════════════════════════╗"
log "║  Root account secured with random password                 ║"
log "║  SSH root login DISABLED                                   ║"
log "║                                                            ║"
log "║  👉 You MUST set password via Web UI on first boot:        ║"
log "║     http://[SERVER-IP]:8080                                ║"
log "╚════════════════════════════════════════════════════════════╝"
echo ""

# Install AlvaOS components
log "Installing AlvaOS components..."

# Create directories
mkdir -p /mnt/opt/alvaos/bin
mkdir -p /mnt/opt/alvaos/webui
mkdir -p /mnt/etc/alvaos
mkdir -p /mnt/var/lib/alvaos
mkdir -p /mnt/var/log/alvaos

# Copy backend
if [ -f "/opt/alvaos/backend/alvaos-backend.py" ]; then
    cp /opt/alvaos/backend/alvaos-backend.py /mnt/opt/alvaos/bin/
    chmod +x /mnt/opt/alvaos/bin/alvaos-backend.py
fi

# Copy frontend
if [ -d "/opt/alvaos/webui" ]; then
    cp -r /opt/alvaos/webui/* /mnt/opt/alvaos/webui/
fi

# Copy VERSION
if [ -f "/opt/alvaos/VERSION" ]; then
    cp /opt/alvaos/VERSION /mnt/etc/alvaos/VERSION
fi

# Create systemd service
cat > /mnt/etc/systemd/system/alvaos.service << 'SERVICE_EOF'
[Unit]
Description=AlvaOS Web Interface
After=network.target

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
SERVICE_EOF

# Create alvaos system user in chroot
log "Creating AlvaOS service user..."
chroot /mnt useradd -r -s /bin/bash -d /opt/alvaos -M alvaos || true

# Set up permissions
chroot /mnt chown -R alvaos:alvaos /opt/alvaos
chroot /mnt chown -R alvaos:alvaos /var/lib/alvaos
chroot /mnt chown -R alvaos:alvaos /var/log/alvaos
chroot /mnt chown -R alvaos:alvaos /etc/alvaos

# Create sudoers rules for specific privileged operations
mkdir -p /mnt/etc/sudoers.d
cat > /mnt/etc/sudoers.d/alvaos << 'SUDOERS_EOF'
# AlvaOS backend needs specific privileged commands
# User Management
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/useradd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/userdel
alvaos ALL=(ALL) NOPASSWD: /usr/bin/smbpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/groupadd
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/groupdel
alvaos ALL=(ALL) NOPASSWD: /usr/bin/gpasswd
alvaos ALL=(ALL) NOPASSWD: /usr/bin/chgrp
alvaos ALL=(ALL) NOPASSWD: /usr/bin/chmod

# Service Management
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl restart alvaos.service
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl restart ssh
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl status docker.service
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl restart smbd
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl reload nfs-kernel-server

# Storage Management (SMART, Btrfs, Partitions)
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

# System Settings (Hostname, Time, Power)
alvaos ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/timedatectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/journalctl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tail
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/reboot
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/poweroff
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/hosts
alvaos ALL=(ALL) NOPASSWD: /usr/bin/sed

# Network Shares Config (NFS Exports, Samba)
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/exportfs
alvaos ALL=(ALL) NOPASSWD: /bin/cat /etc/exports
alvaos ALL=(ALL) NOPASSWD: /bin/cat /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/samba/smb.conf
SUDOERS_EOF
chroot /mnt chmod 440 /etc/sudoers.d/alvaos

# Enable service
chroot /mnt systemctl enable alvaos.service
chroot /mnt systemctl enable NetworkManager

# Create version file
cat > /mnt/etc/alvaos/version.json << 'VERSION_EOF'
{
  "alvaos_version": "0.2.1",
  "build_date": "2026-02-04",
  "installer_version": "0.2.1"
}
VERSION_EOF

# Cleanup
log "Cleaning up..."
chroot /mnt apt-get clean

# Unmount
log "Unmounting filesystems..."
umount -l /mnt/sys/firmware/efi/efivars 2>/dev/null || true
umount -l /mnt/sys || true
umount -l /mnt/proc || true
umount -l /mnt/dev/pts || true
umount -l /mnt/dev || true
umount -l /mnt/boot/efi || true
umount -l /mnt || true

log "╔═══════════════════════════════════════╗"
log "║  ✅ Installation completed!           ║"
log "╚═══════════════════════════════════════╝"
echo ""
log "AlvaOS has been installed to $TARGET_DISK"
log ""
log "Next steps:"
log "1. Remove the installation media"
log "2. Reboot the system: reboot"
log "3. Access Web UI at: http://[IP-ADDRESS]:8080"
log "4. Complete setup wizard to set your password"
log ""
warn "⚠️  SSH root login is DISABLED until you complete web setup!"
echo ""
read -p "Press Enter to reboot, or Ctrl+C to stay in installer..."
reboot
