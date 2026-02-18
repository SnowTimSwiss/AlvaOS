#!/bin/bash
set -e

# AlvaOS Sudoers Setup Script
# Run this once as root to configure permissions

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root"
    exit 1
fi

SUDOERS_FILE="/etc/sudoers.d/alvaos"

echo "Configuring passwordless sudo for 'alvaos' user..."

# Create sudoers file
cat > "$SUDOERS_FILE" <<'SUDOERS_EOF'
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
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/btrfs
alvaos ALL=(ALL) NOPASSWD: /sbin/btrfs
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
alvaos ALL=(ALL) NOPASSWD: /usr/bin/bash
alvaos ALL=(ALL) NOPASSWD: /usr/bin/wg
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/wg
alvaos ALL=(ALL) NOPASSWD: /usr/bin/wg-quick
alvaos ALL=(ALL) NOPASSWD: /usr/sbin/wg-quick
SUDOERS_EOF

chown root:root "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

echo "Success! Sudo permissions configured."
echo "You can now run updates and management tasks."
