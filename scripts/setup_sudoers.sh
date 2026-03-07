#!/bin/bash
set -e

# AlvaOS Sudoers Setup Script
# Run this once as root to configure permissions

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDOERS_FILE="/etc/sudoers.d/alvaos"
SUDOERS_SOURCE="${SCRIPT_DIR}/sudoers.alvaos"

echo "Configuring passwordless sudo for 'alvaos' user..."

if [ ! -f "$SUDOERS_SOURCE" ]; then
    echo "Canonical sudoers source not found: $SUDOERS_SOURCE"
    exit 1
fi

cp "$SUDOERS_SOURCE" "$SUDOERS_FILE"

chown root:root "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE" >/dev/null

echo "Success! Sudo permissions configured."
echo "You can now run updates and management tasks."
