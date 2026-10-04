#!/bin/sh
# Every package installer/install-system.sh installs must exist in the Debian
# release it installs (run inside that release, after apt-get update). One
# wrong name and apt-get stops the whole installation.
set -eu
cd "$(dirname "$0")/../.."
packages=$(sed -n '/apt-get install -y \\$/,/>>/p' installer/install-system.sh \
    | tr -d '\\' | tr ' \t' '\n\n' \
    | grep -E '^[a-z0-9][a-z0-9.+-]+$' | grep -v -E '^(apt-get|install|chroot|env)$' | sort -u)
missing=""
for p in $packages; do
    apt-cache show "$p" >/dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then
    echo "Not in this Debian release:$missing"
    exit 1
fi
echo "All $(echo "$packages" | wc -l) installer packages exist:" $packages
