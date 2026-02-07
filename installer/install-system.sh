#!/bin/bash
set -e

# AlvaOS Installation Script 0.4.0
# This script installs AlvaOS to the target system with whiptail TUI and Mirror support

# Colors for terminal output (still useful for logs)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Progress tracking
PROGRESS=0
TOTAL_STEPS=15

# GUI Helpers
msg() {
    whiptail --title "AlvaOS Installer" --msgbox "$1" 10 60
}

confirm() {
    whiptail --title "AlvaOS Installer" --yesno "$1" 10 60
}

input() {
    whiptail --title "AlvaOS Installer" --inputbox "$1" 10 60 "$2" 3>&1 1>&2 2>&3
}

menu() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --menu "$title" 15 60 5 "$@" 3>&1 1>&2 2>&3
}

checklist() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --checklist "$title" 15 60 5 "$@" 3>&1 1>&2 2>&3
}

update_progress() {
    step_msg=$1
    PROGRESS=$((PROGRESS + 1))
    PERCENT=$((PROGRESS * 100 / TOTAL_STEPS))
    echo "XXX"
    echo "$PERCENT"
    echo "$step_msg"
    echo "XXX"
}

# Cleanup function for error recovery
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        msg "Installation failed! Check the terminal logs for details."
    fi
    
    # Unmount everything
    umount -l /mnt/sys 2>/dev/null || true
    umount -l /mnt/proc 2>/dev/null || true
    umount -l /mnt/dev/pts 2>/dev/null || true
    umount -l /mnt/dev 2>/dev/null || true
    umount -l /mnt/boot/efi 2>/dev/null || true
    umount -l /mnt 2>/dev/null || true
}

trap cleanup EXIT ERR INT TERM

# Logo
LOGO="
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
╚═══════════════════════════════════════════════════════════╝"

whiptail --title "Welcome to AlvaOS" --msgbox "$LOGO" 20 70

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    msg "Please run as root (use sudo)"
    exit 1
fi

# Mode Selection
INSTALL_MODE=$(menu "Select Installation Mode" \
    "SINGLE" "Install on a single disk" \
    "MIRROR" "Mirror (RAID1) on two disks")

AVAILABLE_DISKS=$(lsblk -d -o NAME,SIZE,TYPE | grep disk | awk '{print $1 " (" $2 ") off"}')
if [ "$INSTALL_MODE" == "SINGLE" ]; then
    TARGET_DISKS=$(checklist "Select target disk" $AVAILABLE_DISKS)
    TOTAL_DISKS=$(echo $TARGET_DISKS | wc -w)
    if [ "$TOTAL_DISKS" -ne 1 ]; then
        msg "Please select exactly ONE disk for Single mode."
        exit 1
    fi
else
    TARGET_DISKS=$(checklist "Select TWO target disks for Mirror" $AVAILABLE_DISKS)
    TOTAL_DISKS=$(echo $TARGET_DISKS | wc -w)
    if [ "$TOTAL_DISKS" -ne 2 ]; then
        msg "Please select exactly TWO disks for Mirror mode."
        exit 1
    fi
fi

# Remove quotes from disks
TARGET_DISKS=$(echo $TARGET_DISKS | tr -d '"')

if ! confirm "WARNING: This will ERASE ALL DATA on: $TARGET_DISKS\n\nAre you sure you want to continue?"; then
    exit 0
fi

# Main Installation Logic wrapped in progress bar
{
    update_progress "Preparing disks..."
    for disk in $TARGET_DISKS; do
        disk_path="/dev/$disk"
        parted -s "$disk_path" mklabel gpt
        parted -s "$disk_path" mkpart primary fat32 1MiB 512MiB
        parted -s "$disk_path" set 1 esp on
        parted -s "$disk_path" mkpart primary btrfs 512MiB 100%
        partprobe "$disk_path" || true
    done
    sleep 2

    update_progress "Formatting partitions..."
    DISK_ARRAY=($TARGET_DISKS)
    if [ "$INSTALL_MODE" == "SINGLE" ]; then
        disk=${DISK_ARRAY[0]}
        if [[ "$disk" == *"nvme"* ]]; then
            EFI_PART="/dev/${disk}p1"
            ROOT_PART="/dev/${disk}p2"
        else
            EFI_PART="/dev/${disk}1"
            ROOT_PART="/dev/${disk}2"
        fi
        mkfs.fat -F32 "$EFI_PART"
        mkfs.btrfs -f "$ROOT_PART"
        mount "$ROOT_PART" /mnt
    else
        # Mirror mode
        EFI_PARTS=()
        ROOT_PARTS=()
        for disk in "${DISK_ARRAY[@]}"; do
            if [[ "$disk" == *"nvme"* ]]; then
                EFI_PARTS+=("/dev/${disk}p1")
                ROOT_PARTS+=("/dev/${disk}p2")
            else
                EFI_PARTS+=("/dev/${disk}1")
                ROOT_PARTS+=("/dev/${disk}2")
            fi
        done
        
        for efi in "${EFI_PARTS[@]}"; do
            mkfs.fat -F32 "$efi"
        done
        
        # Create Btrfs RAID1
        mkfs.btrfs -f -d raid1 -m raid1 "${ROOT_PARTS[@]}"
        mount "${ROOT_PARTS[0]}" /mnt
        EFI_PART=${EFI_PARTS[0]} # Use first EFI partition for initial mount
    fi

    mkdir -p /mnt/boot/efi
    mount "$EFI_PART" /mnt/boot/efi

    update_progress "Installing base system (debootstrap)..."
    debootstrap --arch=amd64 bookworm /mnt http://deb.debian.org/debian >/dev/null

    update_progress "Configuring system..."
    echo "alvaos" > /mnt/etc/hostname
    cat > /mnt/etc/hosts << HOSTS_EOF
127.0.0.1   localhost
127.0.1.1   alvaos
::1         localhost ip6-localhost ip6-loopback
HOSTS_EOF

    update_progress "Configuring networking..."
    cat > /mnt/etc/network/interfaces << NET_EOF
source /etc/network/interfaces.d/*
auto lo
iface lo inet loopback
NET_EOF

    update_progress "Configuring apt sources..."
    cat > /mnt/etc/apt/sources.list << SOURCES_EOF
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware
SOURCES_EOF

    update_progress "Configuring fstab..."
    ROOT_UUID=$(blkid -s UUID -o value $(findmnt -n -o SOURCE /mnt))
    cat > /mnt/etc/fstab << FSTAB_EOF
UUID=$ROOT_UUID  /          btrfs  defaults  0  1
FSTAB_EOF
    
    # EFI partitions in fstab
    if [ "$INSTALL_MODE" == "SINGLE" ]; then
        BOOT_UUID=$(blkid -s UUID -o value "$EFI_PART")
        echo "UUID=$BOOT_UUID  /boot/efi  vfat  defaults  0  2" >> /mnt/etc/fstab
    else
        # Mirror: For simplicity, pick first EFI for /boot/efi auto-mount
        BOOT_UUID=$(blkid -s UUID -o value "${EFI_PARTS[0]}")
        echo "UUID=$BOOT_UUID  /boot/efi  vfat  defaults  0  2" >> /mnt/etc/fstab
    fi

    update_progress "Mounting virtual filesystems..."
    mount --rbind /dev /mnt/dev
    mount --make-rslave /mnt/dev
    mount --rbind /sys /mnt/sys
    mount --make-rslave /mnt/sys
    mount -t proc proc /mnt/proc

    update_progress "Installing kernel and essential packages..."
    chroot /mnt apt-get update >/dev/null
    chroot /mnt apt-get install -y \
        linux-image-amd64 python3 python3-flask python3-psutil python3-requests \
        systemd network-manager openssh-server docker.io docker-compose btrfs-progs \
        curl wget vim sudo smartmontools nfs-kernel-server samba >/dev/null

    update_progress "Installing bootloader..."
    if [ -d /sys/firmware/efi ]; then
        chroot /mnt apt-get install -y grub-efi-amd64 >/dev/null
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS --recheck --removable >/dev/null
        done
    else
        chroot /mnt apt-get install -y grub-pc >/dev/null
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=i386-pc "/dev/$disk" >/dev/null
        done
    fi
    chroot /mnt update-grub >/dev/null
    chroot /mnt update-initramfs -u >/dev/null

    update_progress "Securing root account..."
    RANDOM_PASS=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    echo "root:${RANDOM_PASS}" | chroot /mnt chpasswd

    update_progress "Setting up AlvaOS components..."
    mkdir -p /mnt/opt/alvaos/{bin,webui} /mnt/etc/alvaos /mnt/var/lib/alvaos /mnt/var/log/alvaos /mnt/opt/alvaos/scripts

    # Copy backend
    [ -f "/opt/alvaos/backend/alvaos-backend.py" ] && cp /opt/alvaos/backend/alvaos-backend.py /mnt/opt/alvaos/bin/
    [ -f "/opt/alvaos/backend/update_manager.py" ] && cp /opt/alvaos/backend/update_manager.py /mnt/opt/alvaos/bin/
    chmod +x /mnt/opt/alvaos/bin/alvaos-backend.py 2>/dev/null || true

    # Copy scripts
    if [ -d "/opt/alvaos/scripts" ]; then
        cp /opt/alvaos/scripts/update_checker.sh /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        cp /opt/alvaos/scripts/apply_update.sh /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        chmod +x /mnt/opt/alvaos/scripts/*.sh 2>/dev/null || true
        [ -f "/opt/alvaos/scripts/alvaos-update-checker.service" ] && cp /opt/alvaos/scripts/alvaos-update-checker.service /mnt/etc/systemd/system/
    fi

    # Copy frontend
    [ -d "/opt/alvaos/webui" ] && cp -r /opt/alvaos/webui/* /mnt/opt/alvaos/webui/
    [ -f "/opt/alvaos/VERSION" ] && cp /opt/alvaos/VERSION /mnt/etc/alvaos/VERSION

    update_progress "Configuring services..."
    cat > /mnt/etc/systemd/system/alvaos.service << SERVICE_EOF
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

    # Sudoers
    mkdir -p /mnt/etc/sudoers.d
    cat > /mnt/etc/sudoers.d/alvaos << 'SUDOERS_EOF'
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd, /usr/sbin/useradd, /usr/sbin/userdel, /usr/bin/smbpasswd, /usr/sbin/groupadd, /usr/sbin/groupdel, /usr/bin/gpasswd, /usr/bin/chgrp, /usr/bin/chmod
alvaos ALL=(ALL) NOPASSWD: /bin/systemctl restart alvaos.service, /bin/systemctl start alvaos.service, /bin/systemctl stop alvaos.service, /usr/bin/systemd-run, /bin/systemctl restart ssh, /bin/systemctl status docker.service, /bin/systemctl restart smbd, /bin/systemctl reload nfs-kernel-server
alvaos ALL=(ALL) NOPASSWD: /usr/bin/apt, /usr/bin/apt-get, /usr/bin/dpkg, /usr/bin/dpkg-deb
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/smartctl, /usr/bin/lsblk, /usr/bin/btrfs, /usr/sbin/wipefs, /usr/sbin/partprobe, /usr/bin/umount, /usr/bin/mount, /usr/bin/mkdir, /usr/bin/rmdir, /usr/sbin/mkfs.btrfs, /usr/sbin/mkfs.ext4, /usr/sbin/blkid, /usr/bin/cat
alvaos ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl, /usr/bin/timedatectl, /usr/bin/journalctl, /usr/bin/tail, /usr/sbin/reboot, /usr/sbin/poweroff, /usr/bin/tee /etc/hosts, /usr/bin/sed
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/exportfs, /bin/cat /etc/exports, /bin/cat /etc/samba/smb.conf, /usr/bin/tee /etc/exports, /usr/bin/tee -a /etc/exports, /usr/bin/tee /etc/samba/smb.conf, /usr/bin/tee -a /etc/samba/smb.conf
SUDOERS_EOF
    chmod 440 /mnt/etc/sudoers.d/alvaos

    # SSH Security
    mkdir -p /mnt/etc/ssh/sshd_config.d
    cat > /mnt/etc/ssh/sshd_config.d/00-alvaos-security.conf << 'SSH_EOF'
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
SSH_EOF

    update_progress "Finalizing configuration..."
    chroot /mnt useradd -r -s /bin/bash -d /opt/alvaos -M alvaos || true
    chroot /mnt chown -R alvaos:alvaos /opt/alvaos /var/lib/alvaos /var/log/alvaos /etc/alvaos
    
    # Version file
    cat > /mnt/etc/alvaos/version.json << VERSION_EOF
{
  "alvaos_version": "0.4.0",
  "build_date": "$(date +%Y-%m-%d)",
  "installer_version": "0.4.0"
}
VERSION_EOF

    # Enable services
    chroot /mnt systemctl enable alvaos.service >/dev/null 2>&1 || true
    [ -f "/mnt/etc/systemd/system/alvaos-update-checker.service" ] && chroot /mnt systemctl enable alvaos-update-checker.service >/dev/null 2>&1 || true
    chroot /mnt systemctl enable NetworkManager >/dev/null 2>&1 || true

    update_progress "Cleanup..."
    chroot /mnt apt-get clean
    umount -l /mnt/sys /mnt/proc /mnt/dev/pts /mnt/dev /mnt/boot/efi /mnt 2>/dev/null || true

    echo "100"
} | whiptail --title "Installing AlvaOS" --gauge "Please wait..." 10 60 0

msg "Installation completed successfully!\n\nRoot password is random. SSH root login is DISABLED.\n\nSet your password via Web UI at: http://[SERVER-IP]:8080\n\nPlease remove installation media and reboot."
reboot
