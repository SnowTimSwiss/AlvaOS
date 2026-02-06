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
cat > "$SUDOERS_FILE" <<EOF
# AlvaOS Permissions
alvaos ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/dpkg, /usr/bin/mount, /usr/bin/umount, /usr/bin/lsblk, /usr/bin/systemctl, /usr/sbin/service, /usr/bin/cp, /usr/bin/mv, /usr/bin/rm, /usr/bin/mkdir, /usr/bin/chown, /usr/bin/chmod, /usr/sbin/useradd, /usr/sbin/userdel, /usr/sbin/usermod, /usr/sbin/chpasswd, /usr/bin/smbpasswd, /usr/bin/systemd-run
EOF

chmod 440 "$SUDOERS_FILE"

echo "Success! Sudo permissions configured."
echo "You can now run updates and management tasks."
