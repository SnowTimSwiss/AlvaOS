# Scripts

System setup and maintenance scripts for AlvaOS.

## Purpose

- Post-install system configuration
- Storage pool initialization
- Docker setup
- Buddy Backup utilities
- System updates and maintenance

## Philosophy

Keep scripts:
- **Simple** - Readable shell scripts
- **Idempotent** - Safe to run multiple times
- **Logged** - Clear output and error messages
- **Modular** - One script per logical task

## Examples

- `setup-storage.sh` - Initialize Btrfs pools
- `setup-docker.sh` - Install and configure Docker
- `setup-network.sh` - Network configuration
- `buddy-backup-init.sh` - Initialize backup pairing
