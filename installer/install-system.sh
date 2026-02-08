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

# Log file for output redirection
INSTALL_LOG="/tmp/alvaos-install.log"
touch "$INSTALL_LOG"

# GUI Helpers
msg() {
    whiptail --title "AlvaOS Installer" --msgbox "$1" 12 70
}

confirm() {
    whiptail --title "AlvaOS Installer" --yesno "$1" 12 70
}

input() {
    whiptail --title "AlvaOS Installer" --inputbox "$1" 12 70 "$2" 3>&1 1>&2 2>&3
}

menu() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --menu "$title" 16 70 5 "$@" 3>&1 1>&2 2>&3
}

checklist() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --checklist "$title" 16 70 5 "$@" 3>&1 1>&2 2>&3
}

update_progress() {
    step_msg=$1
    PROGRESS=$((PROGRESS + 1))
    PERCENT=$((PROGRESS * 100 / TOTAL_STEPS))
    echo "XXX"
    echo "$PERCENT"
    echo "$step_msg"
    echo "XXX"
    echo "[$(date +%T)] Step $PROGRESS/$TOTAL_STEPS: $step_msg" >> "$INSTALL_LOG"
}

netmask_to_prefix() {
    local mask="$1"
    local IFS=.
    local octets=($mask)
    local prefix=0
    for o in "${octets[@]}"; do
        case "$o" in
            255) prefix=$((prefix + 8)) ;;
            254) prefix=$((prefix + 7)) ;;
            252) prefix=$((prefix + 6)) ;;
            248) prefix=$((prefix + 5)) ;;
            240) prefix=$((prefix + 4)) ;;
            224) prefix=$((prefix + 3)) ;;
            192) prefix=$((prefix + 2)) ;;
            128) prefix=$((prefix + 1)) ;;
            0) ;;
            *) echo ""; return ;;
        esac
    done
    echo "$prefix"
}

# Cleanup function for error recovery
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        msg "Installation failed! \n\nPlease check the logs at $INSTALL_LOG"
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

# Network Selection
NET_MODE=$(menu "Network Configuration" \
    "DHCP" "Automatic (default, DHCP)" \
    "STATIC" "Manual (Static IP)")

if [ "$NET_MODE" == "STATIC" ]; then
    STATIC_IP=$(input "Enter Static IP address (e.g., 192.168.1.50)" "192.168.1.50")
    STATIC_NETMASK=$(input "Enter Netmask (e.g., 255.255.255.0)" "255.255.255.0")
    STATIC_GW=$(input "Enter Gateway (e.g., 192.168.1.1)" "192.168.1.1")
    STATIC_DNS=$(input "Enter DNS Server (e.g., 1.1.1.1)" "1.1.1.1")
fi

# Main Installation Logic wrapped in progress bar
{
    update_progress "Preparing disks..."
    for disk in $TARGET_DISKS; do
        disk_path="/dev/$disk"
        parted -s "$disk_path" mklabel gpt >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" mkpart primary fat32 1MiB 512MiB >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" set 1 esp on >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" mkpart primary btrfs 512MiB 100% >> "$INSTALL_LOG" 2>&1
        partprobe "$disk_path" >> "$INSTALL_LOG" 2>&1 || true
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
        mkfs.fat -F32 "$EFI_PART" >> "$INSTALL_LOG" 2>&1
        mkfs.btrfs -f "$ROOT_PART" >> "$INSTALL_LOG" 2>&1
        mount "$ROOT_PART" /mnt >> "$INSTALL_LOG" 2>&1
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
            mkfs.fat -F32 "$efi" >> "$INSTALL_LOG" 2>&1
        done
        
        # Create Btrfs RAID1
        mkfs.btrfs -f -d raid1 -m raid1 "${ROOT_PARTS[@]}" >> "$INSTALL_LOG" 2>&1
        mount "${ROOT_PARTS[0]}" /mnt >> "$INSTALL_LOG" 2>&1
        EFI_PART=${EFI_PARTS[0]} # Use first EFI partition for initial mount
    fi

    mkdir -p /mnt/boot/efi
    mount "$EFI_PART" /mnt/boot/efi >> "$INSTALL_LOG" 2>&1

    update_progress "Installing base system (debootstrap)..."
    debootstrap --arch=amd64 bookworm /mnt http://deb.debian.org/debian >> "$INSTALL_LOG" 2>&1

    update_progress "Configuring system..."
    echo "alvaos" > /mnt/etc/hostname
    cat > /mnt/etc/hosts << HOSTS_EOF
127.0.0.1   localhost
127.0.1.1   alvaos
::1         localhost ip6-localhost ip6-loopback
HOSTS_EOF

    update_progress "Configuring networking..."
    INTERFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep -v lo | head -n1)
    [ -z "$INTERFACE" ] && INTERFACE="eth0"

    mkdir -p /mnt/etc/NetworkManager
    cat > /mnt/etc/NetworkManager/NetworkManager.conf << 'NMCONF_EOF'
[main]
plugins=ifupdown,keyfile

[ifupdown]
managed=true
NMCONF_EOF

    cat > /mnt/etc/network/interfaces << NET_EOF
auto lo
iface lo inet loopback
NET_EOF

    NM_CONN_DIR=/mnt/etc/NetworkManager/system-connections
    mkdir -p "$NM_CONN_DIR"
    chmod 700 "$NM_CONN_DIR"
    CONN_UUID=$(cat /proc/sys/kernel/random/uuid)

    if [ "$NET_MODE" == "STATIC" ]; then
        PREFIX=$(netmask_to_prefix "$STATIC_NETMASK")
        [ -z "$PREFIX" ] && PREFIX="24"
        cat > "$NM_CONN_DIR/alvaos.nmconnection" << NM_EOF
[connection]
id=alvaos
uuid=$CONN_UUID
type=ethernet
interface-name=$INTERFACE
autoconnect=true

[ipv4]
method=manual
addresses1=$STATIC_IP/$PREFIX,$STATIC_GW
dns=$STATIC_DNS;
dns-search=

[ipv6]
method=ignore
NM_EOF
    else
        cat > "$NM_CONN_DIR/alvaos.nmconnection" << NM_EOF
[connection]
id=alvaos
uuid=$CONN_UUID
type=ethernet
interface-name=$INTERFACE
autoconnect=true

[ipv4]
method=auto
dhcp-hostname=alvaos

[ipv6]
method=ignore
NM_EOF
    fi
    chmod 600 "$NM_CONN_DIR/alvaos.nmconnection"

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
    mount --rbind /dev /mnt/dev >> "$INSTALL_LOG" 2>&1
    mount --make-rslave /mnt/dev >> "$INSTALL_LOG" 2>&1
    mount --rbind /sys /mnt/sys >> "$INSTALL_LOG" 2>&1
    mount --make-rslave /mnt/sys >> "$INSTALL_LOG" 2>&1
    mount -t proc proc /mnt/proc >> "$INSTALL_LOG" 2>&1

    update_progress "Installing kernel and essential packages..."
    chroot /mnt apt-get update >> "$INSTALL_LOG" 2>&1
    chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
        linux-image-amd64 python3 python3-flask python3-flask-cors python3-psutil python3-requests python3-packaging python3-yaml \
        systemd network-manager openssh-server docker.io docker-compose btrfs-progs \
        curl wget vim sudo smartmontools nfs-kernel-server samba >> "$INSTALL_LOG" 2>&1

    update_progress "Installing bootloader..."
    if [ -d /sys/firmware/efi ]; then
        chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
            grub-efi-amd64 >> "$INSTALL_LOG" 2>&1
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS --recheck --removable >> "$INSTALL_LOG" 2>&1
        done
    else
        chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
            grub-pc >> "$INSTALL_LOG" 2>&1
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=i386-pc "/dev/$disk" >> "$INSTALL_LOG" 2>&1
        done
    fi
    chroot /mnt update-grub >> "$INSTALL_LOG" 2>&1
    chroot /mnt update-initramfs -u >> "$INSTALL_LOG" 2>&1

    update_progress "Securing root account..."
    RANDOM_PASS=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    echo "root:${RANDOM_PASS}" | chroot /mnt chpasswd >> "$INSTALL_LOG" 2>&1

    update_progress "Setting up AlvaOS components..."
    mkdir -p /mnt/opt/alvaos/{bin,webui} /mnt/etc/alvaos /mnt/var/lib/alvaos /mnt/var/log/alvaos /mnt/opt/alvaos/scripts

    # Copy backend
    if [ -d "/opt/alvaos/backend" ]; then
        cp /opt/alvaos/backend/*.py /mnt/opt/alvaos/bin/ 2>/dev/null || true
        chmod +x /mnt/opt/alvaos/bin/*.py 2>/dev/null || true
    fi

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
    if [ -d "/opt/alvaos/apps" ]; then
        mkdir -p /mnt/opt/alvaos/apps
        cp -r /opt/alvaos/apps/* /mnt/opt/alvaos/apps/ 2>/dev/null || true
    fi

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
alvaos ALL=(ALL) NOPASSWD: /sbin/smartctl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/lsblk
alvaos ALL=(ALL) NOPASSWD: /usr/bin/btrfs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/wipefs
alvaos ALL=(ALL) NOPASSWD: /sbin/wipefs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/partprobe
alvaos ALL=(ALL) NOPASSWD: /sbin/partprobe
alvaos ALL=(ALL) NOPASSWD: /usr/bin/umount
alvaos ALL=(ALL) NOPASSWD: /bin/umount
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mount
alvaos ALL=(ALL) NOPASSWD: /bin/mount
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mkdir
alvaos ALL=(ALL) NOPASSWD: /bin/mkdir
alvaos ALL=(ALL) NOPASSWD: /usr/bin/rmdir
alvaos ALL=(ALL) NOPASSWD: /bin/rmdir
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/mkfs.btrfs
alvaos ALL=(ALL) NOPASSWD: /sbin/mkfs.btrfs
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/mkfs.ext4
alvaos ALL=(ALL) NOPASSWD: /sbin/mkfs.ext4
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/blkid
alvaos ALL=(ALL) NOPASSWD: /sbin/blkid
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat
alvaos ALL=(ALL) NOPASSWD: /bin/cat
alvaos ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/timedatectl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/journalctl
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tail
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/reboot
alvaos ALL=(ALL) NOPASSWD: /sbin/reboot
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/poweroff
alvaos ALL=(ALL) NOPASSWD: /sbin/poweroff
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/hosts
alvaos ALL=(ALL) NOPASSWD: /usr/bin/sed
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/exportfs
alvaos ALL=(ALL) NOPASSWD: /sbin/exportfs
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/exports
alvaos ALL=(ALL) NOPASSWD: /bin/cat /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /bin/cat /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/exports
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/samba/smb.conf
alvaos ALL=(ALL) NOPASSWD: /usr/bin/mountpoint
alvaos ALL=(ALL) NOPASSWD: /usr/bin/docker
alvaos ALL=(ALL) NOPASSWD: /usr/bin/docker-compose
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/ip
alvaos ALL=(ALL) NOPASSWD: /sbin/ip
alvaos ALL=(ALL) NOPASSWD: /usr/bin/id
alvaos ALL=(ALL) NOPASSWD: /usr/bin/df
alvaos ALL=(ALL) NOPASSWD: /bin/df
alvaos ALL=(ALL) NOPASSWD: /usr/bin/getent
alvaos ALL=(ALL) NOPASSWD: /usr/bin/nohup
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
    chroot /mnt useradd -r -s /bin/bash -d /opt/alvaos -M alvaos >> "$INSTALL_LOG" 2>&1 || true
    chroot /mnt chown -R alvaos:alvaos /opt/alvaos /var/lib/alvaos /var/log/alvaos /etc/alvaos >> "$INSTALL_LOG" 2>&1
    
    # Version file
    cat > /mnt/etc/alvaos/version.json << VERSION_EOF
{
  "alvaos_version": "0.4.0",
  "build_date": "$(date +%Y-%m-%d)",
  "installer_version": "0.4.0"
}
VERSION_EOF

    # Enable services
    chroot /mnt systemctl enable alvaos.service >> "$INSTALL_LOG" 2>&1 || true
    [ -f "/mnt/etc/systemd/system/alvaos-update-checker.service" ] && chroot /mnt systemctl enable alvaos-update-checker.service >> "$INSTALL_LOG" 2>&1 || true
    chroot /mnt systemctl enable NetworkManager >> "$INSTALL_LOG" 2>&1 || true

    update_progress "Cleanup..."
    chroot /mnt apt-get clean >> "$INSTALL_LOG" 2>&1
    umount -l /mnt/sys /mnt/proc /mnt/dev/pts /mnt/dev /mnt/boot/efi /mnt >> "$INSTALL_LOG" 2>&1 || true

    echo "100"
} | whiptail --title "Installing AlvaOS" --gauge "Please wait..." 10 70 0

# Final Message
if [ "$NET_MODE" == "STATIC" ]; then
    msg "Installation Successful!\n\nPlease remove installation media and press Enter to reboot.\n\nAfter reboot, visit http://$STATIC_IP:8080 to complete setup."
else
    msg "Installation Successful!\n\nPlease remove installation media and press Enter to reboot.\n\nAfter reboot, find the server IP in your router (DHCP) or run 'ip a' on the server.\nThen visit http://<ip>:8080 to complete setup."
fi
reboot
