# Installer

The AlvaOS installer image build system.

## Purpose

- Creates a minimal Debian-based installer image
- NOT a live desktop ISO
- Just enough to bootstrap the system
- Post-install configuration via scripts

## Build Process

1. Use `debootstrap` to create a minimal Debian base
2. Add AlvaOS installer scripts
3. Package as bootable ISO or disk image
4. Keep it small (< 500 MB target)

## Output

- `alvaos-installer-<version>.iso` - Bootable installer image
- Intended for USB boot or VM deployment

## Requirements

- Debian-based build environment
- `debootstrap`, `xorriso`, `squashfs-tools`
