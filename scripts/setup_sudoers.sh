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

# Check the new rules before they replace the file: a broken sudoers file
# locks everyone out of sudo, and set -e stops the script before the install.
checked="$(mktemp)"
trap 'rm -f "$checked"' EXIT
cp "$SUDOERS_SOURCE" "$checked"
visudo -c -f "$checked" >/dev/null
install -o root -g root -m 0440 "$checked" "$SUDOERS_FILE"

echo "Success! Sudo permissions configured."
echo "You can now run updates and management tasks."
