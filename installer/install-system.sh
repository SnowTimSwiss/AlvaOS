#!/bin/bash
set -e

# AlvaOS Installation Script v0.1
# This script installs AlvaOS to the target system

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logo
cat << 'EOF'
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║       █████╗ ██╗    ██╗   ██╗ █████╗  ██████╗ ███████╗   ║
║      ██╔══██╗██║    ██║   ██║██╔══██╗██╔═══██╗██╔════╝   ║
║      ███████║██║    ██║   ██║███████║██║   ██║███████╗   ║
║      ██╔══██║██║    ╚██╗ ██╔╝██╔══██║██║   ██║╚════██║   ║
║      ██║  ██║███████╗╚████╔╝ ██║  ██║╚██████╔╝███████║   ║
║      ╚═╝  ╚═╝╚══════╝ ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ║
║                                                           ║
║            Installation Script v0.1                       ║
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

# Configure network (DHCP)
cat > /mnt/etc/network/interfaces << 'NET_EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp

auto enp0s3
iface enp0s3 inet dhcp
NET_EOF

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
mount --bind /dev /mnt/dev
mount --bind /dev/pts /mnt/dev/pts
mount -t proc proc /mnt/proc
mount -t sysfs sysfs /mnt/sys

chroot /mnt apt-get update
chroot /mnt apt-get install -y \
    linux-image-amd64 \
    grub-efi-amd64 \
    python3 \
    python3-pip \
    python3-flask \
    python3-flask-cors \
    python3-psutil \
    systemd \
    network-manager \
    openssh-server \
    curl \
    wget \
    vim

# Install GRUB
log "Installing bootloader..."
chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS
chroot /mnt update-grub

# Set root password
log "Setting root password..."
echo "root:alvaos" | chroot /mnt chpasswd
warn "Default root password is 'alvaos' - PLEASE CHANGE IT AFTER FIRST LOGIN!"

# Install AlvaOS components
log "Installing AlvaOS components..."

# Create directories
mkdir -p /mnt/opt/alvaos/bin
mkdir -p /mnt/opt/alvaos/webui
mkdir -p /mnt/etc/alvaos
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

# Create systemd service
cat > /mnt/etc/systemd/system/alvaos.service << 'SERVICE_EOF'
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
SERVICE_EOF

# Enable service
chroot /mnt systemctl enable alvaos.service
chroot /mnt systemctl enable NetworkManager

# Create version file
cat > /mnt/etc/alvaos/version.json << 'VERSION_EOF'
{
  "alvaos_version": "0.1.0",
  "build_date": "2026-01-29",
  "installer_version": "0.1.0"
}
VERSION_EOF

# Cleanup
log "Cleaning up..."
chroot /mnt apt-get clean

# Unmount
log "Unmounting filesystems..."
umount /mnt/sys || true
umount /mnt/proc || true
umount /mnt/dev/pts || true
umount /mnt/dev || true
umount /mnt/boot/efi
umount /mnt

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
log ""
warn "Default login: root / alvaos (CHANGE THIS!)"
echo ""
read -p "Press Enter to reboot, or Ctrl+C to stay in installer..."
reboot
